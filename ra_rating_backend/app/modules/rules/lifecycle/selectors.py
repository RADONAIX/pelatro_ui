"""Turning "which rules" into a list of ids, three ways and only three.

The selectors match how an operator actually thinks about a group of rules:

* **A batch** — "everything Tuesday's Ericsson import produced". The common case.
* **A rule set** — "this release", which may span several imports.
* **A filter** — the catalogue's own filters, so "every prepaid VOICE draft" needs
  no new vocabulary.

* **A list of rule keys** — "these four, which I ticked". Added for the one
  workflow the other three express badly: an operator looking at three duplicate
  rules and removing one of them. Expressing that as a filter means writing a
  filter that *nearly* matches, and a filter that nearly matches deletes the
  wrong rule.

An arbitrary ``rule_ids`` array is still deliberately not offered, and the
distinction between it and ``rule_keys`` is the whole reason the latter is
acceptable. The objection to an id array is not its shape — it is that a UUID
carries no statement of intent, so `{"rule_ids": ["3f2a…", "9c81…"]}` in an audit
log is unreadable and a client that posts five hundred it did not intend to
leaves no trace anyone can review. A key is the rule's name:
`{"rule_keys": ["PREPAID_A_60_SECOND_VOICE_PULSE_COPY"]}` says what it did, a
wrong key fails loudly instead of silently hitting a different rule, and the same
key identifies the rule in both the legacy and the canonical store. The list is
capped for the same reason the others are size-guarded.

All three resolve through one function so that the safety rules downstream
cannot be sidestepped by choosing a different selector — a guard that applies to
two of three entry points is not a guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailedError
from app.modules.rules.api import service as rule_service
from app.modules.rules.canonical.lineage import (
    RuleIngestionBatch,
    RuleIngestionRecord,
)
from app.modules.rules.canonical.rule import CanonicalRule
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember

#: A person ticking boxes does not tick more than this. A larger selection is a
#: filter or a rule set, both of which state their intent in one line.
MAX_KEYS = 500


@dataclass(frozen=True, slots=True)
class Selector:
    """Exactly one of these is set. Validated on construction, not on use."""

    batch_id: str | None = None
    rule_set_id: str | None = None
    #: The catalogue's filters, by the same names the list endpoint uses.
    filters: dict[str, object] | None = None
    #: Explicitly ticked rules, by key. Capped — see MAX_KEYS.
    rule_keys: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        chosen = [
            name
            for name, value in (
                ("batch_id", self.batch_id),
                ("rule_set_id", self.rule_set_id),
                ("filters", self.filters or None),
                ("rule_keys", self.rule_keys or None),
            )
            if value
        ]
        if len(chosen) != 1:
            raise ValidationFailedError(
                "Name exactly one of batch_id, rule_set_id, filters or rule_keys."
                + (f" Got {', '.join(chosen)}." if chosen else ""),
                details={"hint": "A bulk operation must state what it is acting on."},
            )
        if self.rule_keys and len(self.rule_keys) > MAX_KEYS:
            raise ValidationFailedError(
                f"{len(self.rule_keys):,} keys is more than a person ticked. The "
                f"limit is {MAX_KEYS:,}; select by filter or rule set instead.",
                details={"count": len(self.rule_keys), "limit": MAX_KEYS},
            )

    def describe(self) -> dict[str, object]:
        if self.batch_id:
            return {"batch_id": self.batch_id}
        if self.rule_set_id:
            return {"rule_set_id": self.rule_set_id}
        if self.rule_keys:
            return {"rule_keys": list(self.rule_keys)}
        return {"filters": dict(self.filters or {})}


async def resolve(
    db: AsyncSession,
    selector: Selector,
    *,
    tenant_id: str,
    allow_missing: bool = False,
) -> list[CanonicalRule]:
    """The rules this selector names, tenant-scoped.

    Scoped explicitly rather than relying on row-level security, because the
    policies are inert when the service connects as a superuser and a bulk
    operation is the last place to depend on a control that might not be on.
    """
    if selector.batch_id:
        return await _from_batch(db, selector.batch_id, tenant_id)
    if selector.rule_set_id:
        return await _from_rule_set(db, selector.rule_set_id, tenant_id)
    if selector.rule_keys:
        return await _from_keys(
            db, selector.rule_keys, tenant_id, allow_missing=allow_missing
        )
    return await _from_filters(db, selector.filters or {}, tenant_id)


async def _from_keys(
    db: AsyncSession,
    rule_keys: tuple[str, ...],
    tenant_id: str,
    *,
    allow_missing: bool = False,
) -> list[CanonicalRule]:
    """The named rules, refusing the whole call if any key is unknown.

    Refusing rather than silently returning the ones that matched: an operator
    who ticked four rules and saw "3 deleted" would have to work out which one
    was missed.

    ``allow_missing`` exists for **delete**, and only for delete. The other
    operations move a rule's *status*, which is a canonical concept — a key with
    no canonical row has no status to move, so refusing is right. Deletion is
    different: the rule is in the catalogue, it is rating traffic, and it exists
    in `rating.rules` whether or not it has been backfilled. Refusing to remove
    it because of a migration that has not reached it yet would make the button
    fail for exactly the estates that most need it, and the caller handles those
    keys through the legacy path instead.
    """
    rows = list(
        (
            await db.execute(
                select(CanonicalRule).where(
                    CanonicalRule.tenant_id == tenant_id,
                    CanonicalRule.rule_key.in_(rule_keys),
                )
            )
        )
        .scalars()
        .all()
    )
    missing = sorted(set(rule_keys) - {r.rule_key for r in rows})
    if missing and not allow_missing:
        raise NotFoundError(
            f"{len(missing)} of these rules were not found: {', '.join(missing[:5])}"
            + (" …" if len(missing) > 5 else ""),
            details={
                "missing": missing,
                "hint": "A rule authored in the legacy catalogue has no canonical "
                        "row until it is backfilled or re-imported.",
            },
        )
    return rows


async def _from_batch(
    db: AsyncSession, batch_id: str, tenant_id: str
) -> list[CanonicalRule]:
    batch = await db.get(RuleIngestionBatch, batch_id)
    if batch is None or batch.tenant_id != tenant_id:
        raise NotFoundError(f"Import batch '{batch_id}' was not found.")
    if batch.dry_run:
        raise ValidationFailedError(
            "That batch was a preview — it wrote nothing, so there is nothing to "
            "act on.",
            details={"batch_id": batch_id},
        )

    rule_ids = (
        (
            await db.execute(
                select(RuleIngestionRecord.rule_id).where(
                    RuleIngestionRecord.batch_id == batch_id,
                    RuleIngestionRecord.rule_id.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rule_ids:
        return []
    return await _load(db, select(CanonicalRule).where(
        CanonicalRule.rule_id.in_(set(rule_ids)),
        CanonicalRule.tenant_id == tenant_id,
    ))


async def _from_rule_set(
    db: AsyncSession, rule_set_id: str, tenant_id: str
) -> list[CanonicalRule]:
    rule_set = await db.get(CanonicalRuleSet, rule_set_id)
    if rule_set is None or rule_set.tenant_id != tenant_id:
        raise NotFoundError(f"Rule set '{rule_set_id}' was not found.")
    return await _load(db, select(CanonicalRule).join(
        RuleSetMember, RuleSetMember.rule_id == CanonicalRule.rule_id
    ).where(
        RuleSetMember.rule_set_id == rule_set_id,
        CanonicalRule.tenant_id == tenant_id,
    ))


async def _from_filters(
    db: AsyncSession, filters: dict[str, object], tenant_id: str
) -> list[CanonicalRule]:
    """Reuse the catalogue's query builder, so a bulk operation acts on exactly
    the rules the operator was looking at when they decided to act."""
    allowed = {
        "search", "charging_mode", "service_type", "rule_type", "status",
        "product_id", "source_system_id", "snapshot_id", "validation_state",
        "execution_mode", "rule_set_id", "effective_on",
    }
    unknown = sorted(set(filters) - allowed)
    if unknown:
        raise ValidationFailedError(
            f"Unknown filter(s): {', '.join(unknown)}.",
            details={"allowed": sorted(allowed)},
        )

    effective_on = filters.get("effective_on")
    if isinstance(effective_on, str):
        effective_on = date.fromisoformat(effective_on)

    stmt = rule_service.build_list_query(
        **{k: v for k, v in filters.items() if k != "effective_on"},  # type: ignore[arg-type]
        effective_on=effective_on,  # type: ignore[arg-type]
    )
    # `build_list_query` selects (rule, version); only the rule is wanted here.
    rows = (
        await db.execute(stmt.where(CanonicalRule.tenant_id == tenant_id))
    ).all()
    return [rule for rule, _version in rows]


async def _load(db: AsyncSession, stmt: Select) -> list[CanonicalRule]:
    rows = (await db.execute(stmt.order_by(CanonicalRule.rule_key))).scalars().all()
    return list(rows)
