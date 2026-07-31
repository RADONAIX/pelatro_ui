"""Read the primary rows, map them, write the mirror.

Every function here takes the **primary** session (to read what was just
committed) and opens its own **mirror** session. The two are never in the same
transaction — that separation is the whole consistency design, and it is what
lets a mirror outage be a log line instead of a failed request.

Reads against the primary are plain `select()`s issued after its transaction has
already committed, so they add no locking and cannot alter its outcome.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.balances.models import BalanceBucket
from app.modules.catalog.models import DestinationPrefix, DestinationZone, TaxRule, TimeBand
from app.modules.cdr.models import SubscriberProduct
from app.modules.mirror import mapping, tables, vocabulary
from app.modules.mirror.engine import mirror_session
from app.modules.mirror.mapping import Unmappable
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.lookups import (
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.models import Rule as LegacyRule

log = get_logger("mirror.sync")


async def _write_rule(
    rule_row: dict[str, Any],
    condition_rows: list[dict[str, Any]],
    action_rows: list[dict[str, Any]],
) -> None:
    """Replace one rule and its logic in the mirror, self-healing the vocabulary.

    `rating_rule_condition` and `rating_rule_action` carry foreign keys onto
    `rule_attribute`, `rule_operator` and `rule_action_type`. Those three are
    projections of Python registries, seeded at startup — so if they are
    truncated, or the mirror database is recreated, or the service started before
    the mirror was reachable, every rule write fails on a foreign key and keeps
    failing until somebody restarts the process.

    So a foreign-key failure re-seeds the vocabulary and retries once. Once,
    deliberately: if the write fails again the cause is the rule, not the
    lookups, and a second retry would only delay the log line that says so.
    """
    for attempt in (1, 2):
        try:
            async with mirror_session() as mirror:
                await vocabulary.ensure(mirror)
                await _upsert(mirror, tables.rating_rule, [rule_row], ["rule_id"])
                # Replace rather than merge: a rewritten rule may have fewer
                # conditions than before, and a merge would leave the removed
                # ones live in the mirror.
                await mirror.execute(
                    delete(tables.rating_rule_condition).where(
                        tables.rating_rule_condition.c.rule_id == rule_row["rule_id"]
                    )
                )
                await mirror.execute(
                    delete(tables.rating_rule_action).where(
                        tables.rating_rule_action.c.rule_id == rule_row["rule_id"]
                    )
                )
                if condition_rows:
                    await mirror.execute(
                        insert(tables.rating_rule_condition).values(condition_rows)
                    )
                if action_rows:
                    await mirror.execute(insert(tables.rating_rule_action).values(action_rows))
            return
        except IntegrityError:
            if attempt == 2:
                raise
            log.warning("mirror_vocabulary_stale", rule_id=rule_row["rule_id"])
            vocabulary.invalidate()


async def _upsert(
    db: AsyncSession, table: Any, rows: list[dict[str, Any]], keys: list[str]
) -> None:
    if not rows:
        return
    stmt = insert(table).values(rows)
    updatable = [c for c in rows[0] if c not in keys]
    if updatable:
        stmt = stmt.on_conflict_do_update(
            index_elements=keys, set_={c: stmt.excluded[c] for c in updatable}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    await db.execute(stmt)


# --- Rules -------------------------------------------------------------------


async def _tax_rates(primary: AsyncSession, action_codes: set[str]) -> dict[str, Any]:
    """Resolve catalogue tax references before crossing the mirror boundary."""
    if "APPLY_TAX" not in action_codes:
        return {}
    rows = (
        await primary.execute(select(TaxRule.code, TaxRule.rate_percent))
    ).all()
    return {str(code): rate for code, rate in rows}


async def push_rule_version(primary: AsyncSession, rule_version_id: str) -> bool:
    """Mirror one rule version and its logic. Returns whether anything was written."""
    version = await primary.get(CanonicalRuleVersion, rule_version_id)
    if version is None:
        log.warning("mirror_version_missing", rule_version_id=rule_version_id)
        return False
    rule = await primary.get(CanonicalRule, version.rule_id)
    if rule is None:
        log.warning("mirror_rule_missing", rule_id=version.rule_id)
        return False

    rule_type = await primary.get(RuleTypeRow, rule.rule_type_id)
    stage = await primary.get(RuleStage, rule.rule_stage_id)
    if rule_type is None or stage is None:
        log.warning("mirror_lookup_missing", rule_id=rule.rule_id)
        return False
    policy = (
        await primary.get(RuleStackingPolicyRow, version.stacking_policy_id)
        if version.stacking_policy_id
        else None
    )

    groups = list(
        (
            await primary.execute(
                select(RuleConditionGroup).where(
                    RuleConditionGroup.rule_version_id == rule_version_id
                )
            )
        )
        .scalars()
        .all()
    )
    conditions = list(
        (
            await primary.execute(
                select(RuleConditionRow).where(
                    RuleConditionRow.rule_version_id == rule_version_id
                )
            )
        )
        .scalars()
        .all()
    )
    actions = list(
        (
            await primary.execute(
                select(RuleActionRow).where(RuleActionRow.rule_version_id == rule_version_id)
            )
        )
        .scalars()
        .all()
    )
    parameters = list(
        (
            await primary.execute(
                select(RuleParameter).where(RuleParameter.rule_version_id == rule_version_id)
            )
        )
        .scalars()
        .all()
    )
    action_codes = {a.action_type for a in actions}
    tax_rates = await _tax_rates(primary, action_codes)

    try:
        rule_row = mapping.map_rule(
            rule,
            version,
            stage_code=stage.code,
            rule_type_code=rule_type.code,
            allows_multiple=bool(policy and policy.allows_multiple),
            action_codes=action_codes,
        )
        group_index = mapping.flatten_condition_groups(
            condition_logic=version.condition_logic, groups=groups, conditions=conditions
        )
        condition_rows = mapping.map_conditions(
            rule_id=rule_row["rule_id"],
            conditions=conditions,
            group_index=group_index,
            service_type=rule.service_type,
            service_created_at=rule.created_at,
        )
        action_rows = mapping.map_actions(
            rule_id=rule_row["rule_id"],
            actions=actions,
            parameters=parameters,
            tax_rates=tax_rates,
        )
    except Unmappable as exc:
        # The one place a rule is deliberately dropped. Logged with its key so the
        # estate's unmirrorable rules are enumerable from the logs alone.
        log.warning(
            "mirror_rule_unmappable",
            rule_key=rule.rule_key,
            rule_version_id=rule_version_id,
            reason=str(exc),
        )
        return False

    await _write_rule(rule_row, condition_rows, action_rows)

    log.info(
        "mirror_rule_written",
        rule_key=rule.rule_key,
        version_no=version.version_number,
        conditions=len(condition_rows),
        actions=len(action_rows),
    )
    return True


# --- Legacy rules ------------------------------------------------------------


async def push_legacy_rule(primary: AsyncSession, rule_id: str) -> bool:
    """Mirror one legacy `rating.rules` row.

    This is the path the rule-management UI actually exercises: a default
    deployment has `RULE_COMPILE_SOURCE=LEGACY` and the authoring API writes
    `rating.rules`, not the canonical `ra_rule.*` model.
    """
    rule = await primary.get(LegacyRule, rule_id)
    if rule is None:
        log.warning("mirror_legacy_rule_missing", rule_id=rule_id)
        return False

    tax_rates = await _tax_rates(primary, {a.action_type for a in rule.actions})

    try:
        rule_row = mapping.map_legacy_rule(rule)
        condition_rows = mapping.map_legacy_conditions(rule)
        action_rows = mapping.map_legacy_actions(rule, tax_rates=tax_rates)
    except Unmappable as exc:
        log.warning(
            "mirror_rule_unmappable",
            source="LEGACY",
            rule_key=rule.rule_key,
            rule_id=rule_id,
            reason=str(exc),
        )
        return False

    await _write_rule(rule_row, condition_rows, action_rows)

    log.info(
        "mirror_rule_written",
        source="LEGACY",
        rule_key=rule.rule_key,
        version_no=rule.version,
        conditions=len(condition_rows),
        actions=len(action_rows),
    )
    return True


async def delete_legacy_rule(rule_id: str) -> bool:
    """Mirror the hard delete of a first-version draft.

    `delete_draft` really removes the row, and `fk_condition_rule` /
    `fk_action_rule` cascade on the target, so one delete is enough.
    """
    async with mirror_session() as mirror:
        await mirror.execute(
            delete(tables.rating_rule).where(tables.rating_rule.c.rule_id == rule_id)
        )
    log.info("mirror_rule_deleted", source="LEGACY", rule_id=rule_id)
    return True


# --- Metadata catalogue ------------------------------------------------------


async def push_time_band(primary: AsyncSession, entity_id: str) -> bool:
    band = await primary.get(TimeBand, entity_id)
    if band is None:
        log.warning("mirror_time_band_missing", entity_id=entity_id)
        return False
    try:
        rows = mapping.map_time_band(band)
    except Unmappable as exc:
        log.warning("mirror_time_band_unmappable", code=band.code, reason=str(exc))
        return False

    async with mirror_session() as mirror:
        # The target has no unique key on this table, so replace-by-code is the
        # only idempotent write available. Safe: one platform band owns one code.
        await mirror.execute(
            delete(tables.time_band).where(tables.time_band.c.time_band_code == band.code[:50])
        )
        await mirror.execute(insert(tables.time_band).values(rows))
    log.info("mirror_time_band_written", code=band.code, rows=len(rows))
    return True


async def push_destination_prefix(primary: AsyncSession, entity_id: str) -> bool:
    prefix = await primary.get(DestinationPrefix, entity_id)
    if prefix is None:
        log.warning("mirror_prefix_missing", entity_id=entity_id)
        return False
    zone = await primary.get(DestinationZone, prefix.zone_id)
    try:
        row = mapping.map_destination_prefix(prefix, zone)
    except Unmappable as exc:
        log.warning("mirror_prefix_unmappable", prefix=prefix.prefix, reason=str(exc))
        return False

    async with mirror_session() as mirror:
        await mirror.execute(
            delete(tables.destination_prefix).where(
                tables.destination_prefix.c.prefix == row["prefix"],
                tables.destination_prefix.c.zone_code == row["zone_code"],
            )
        )
        await mirror.execute(insert(tables.destination_prefix).values([row]))
    log.info("mirror_prefix_written", prefix=prefix.prefix, zone=row["zone_code"])
    return True


async def delete_destination_prefix(prefix: str, zone_code: str) -> bool:
    """Mirror a hard delete.

    `delete_prefix` in the catalogue router really removes the row, so without
    this the mirror would keep matching on a prefix the platform no longer knows.
    """
    async with mirror_session() as mirror:
        await mirror.execute(
            delete(tables.destination_prefix).where(
                tables.destination_prefix.c.prefix == prefix[:40],
                tables.destination_prefix.c.zone_code == zone_code[:50],
            )
        )
    log.info("mirror_prefix_deleted", prefix=prefix, zone=zone_code)
    return True


# --- Subscriber plane --------------------------------------------------------


async def push_subscriber_offer(primary: AsyncSession, entity_id: str) -> bool:
    assignment = await primary.get(SubscriberProduct, entity_id)
    if assignment is None:
        return False
    try:
        row = mapping.map_subscriber_offer(assignment)
    except Unmappable as exc:
        log.warning("mirror_subscriber_offer_unmappable", entity_id=entity_id, reason=str(exc))
        return False
    async with mirror_session() as mirror:
        await mirror.execute(
            delete(tables.subscriber_offer).where(
                tables.subscriber_offer.c.subscriber_id == row["subscriber_id"],
                tables.subscriber_offer.c.offer_id == row["offer_id"],
                tables.subscriber_offer.c.effective_from == row["effective_from"],
            )
        )
        await mirror.execute(insert(tables.subscriber_offer).values([row]))
    return True


async def push_bundle_balance(primary: AsyncSession, entity_id: str) -> bool:
    bucket = await primary.get(BalanceBucket, entity_id)
    if bucket is None:
        return False
    try:
        row = mapping.map_subscriber_bundle_balance(bucket)
    except Unmappable as exc:
        log.warning("mirror_bundle_balance_unmappable", entity_id=entity_id, reason=str(exc))
        return False
    async with mirror_session() as mirror:
        await mirror.execute(
            delete(tables.subscriber_bundle_balance).where(
                tables.subscriber_bundle_balance.c.subscriber_id == row["subscriber_id"],
                tables.subscriber_bundle_balance.c.bundle_code == row["bundle_code"],
                tables.subscriber_bundle_balance.c.effective_from == row["effective_from"],
            )
        )
        await mirror.execute(insert(tables.subscriber_bundle_balance).values([row]))
    return True
