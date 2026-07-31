"""Where the compiler gets its rules from — the legacy table, or the canonical one.

This is plan step **M5**, and it is deliberately a *source* swap rather than a
rewrite. ``compile_rule`` is untouched: it still reads the same attributes off
whatever it is handed, and ``compilerview.RuleView`` presents a canonical rule
version wearing exactly that interface. That is why the R4 parity gate is
meaningful — both sides go through one compiler, so "the migration changed
nothing" is a claim about the data rather than about two implementations
agreeing.

**The default is still LEGACY.** Flipping it is a decision about a specific
estate, taken once its own parity report is clean, not a decision taken here.
The capability exists; the switch is `RULE_COMPILE_SOURCE=CANONICAL`.

The canonical loader deliberately mirrors the legacy one's semantics exactly:

* the same eligible statuses — APPROVED, COMPILED, PUBLISHED, ACTIVE;
* the same "newest **eligible** version per logical rule". The word matters. A
  live rule being edited has v1=ACTIVE and v2=DRAFT, and rating must keep using
  v1 until v2 is approved. Gating on `rule.status` instead would drop the whole
  rule the moment somebody opened it for editing — a silent revenue hole that
  only appears on estates that edit in place, which is all of them;
* the same ordering, so a snapshot's row order does not shift under the swap.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.rules.canonical import compilerview
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.lookups import (
    RuleConflictGroupRow,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember
from app.modules.rules.constants import RuleStatus


class CompileSource(StrEnum):
    LEGACY = "LEGACY"
    CANONICAL = "CANONICAL"


#: Statuses a rule must be in to reach a snapshot. Identical to the legacy
#: loader's list — a rule that is compilable on one side and not the other would
#: make every parity comparison meaningless.
ELIGIBLE: tuple[str, ...] = (
    RuleStatus.APPROVED,
    RuleStatus.COMPILED,
    RuleStatus.PUBLISHED,
    RuleStatus.ACTIVE,
)


def configured_source() -> str:
    raw = str(getattr(settings, "rule_compile_source", CompileSource.LEGACY)).upper()
    return raw if raw in set(CompileSource) else CompileSource.LEGACY


async def load_canonical(
    db: AsyncSession, rule_set_id: str | None, *, tenant_id: str | None = None
) -> list[compilerview.RuleView]:
    """Every canonical rule eligible for a snapshot, as the compiler expects them.

    Loads in a fixed number of queries regardless of rule count: one for the
    rules, one for the lookups, and one per child table for the whole set. The
    legacy loader gets away with ``selectinload`` because a legacy rule owns its
    conditions and actions directly; the canonical model spreads a rule across
    five tables, and doing that per rule would be five thousand round trips on a
    thousand-rule snapshot.
    """
    tenant = tenant_id or settings.default_tenant_id

    # Joined on *every* version and filtered by the version's own status, not on
    # `current_version_id` — the current version of an actively-edited rule is its
    # draft, and the draft is precisely the one that must not rate.
    stmt = (
        select(CanonicalRule, CanonicalRuleVersion)
        .join(
            CanonicalRuleVersion,
            CanonicalRuleVersion.rule_id == CanonicalRule.rule_id,
        )
        .where(
            CanonicalRule.tenant_id == tenant,
            CanonicalRuleVersion.status.in_(ELIGIBLE),
        )
    )
    if rule_set_id:
        stmt = stmt.join(
            RuleSetMember, RuleSetMember.rule_id == CanonicalRule.rule_id
        ).where(RuleSetMember.rule_set_id == rule_set_id)

    pairs = list((await db.execute(stmt)).all())
    if not pairs:
        return []

    # One version per logical rule: the newest eligible one. Two versions of the
    # same rule in a snapshot would both match every context, and which won would
    # depend on row order.
    newest: dict[str, tuple[CanonicalRule, CanonicalRuleVersion]] = {}
    for rule, version in pairs:
        current = newest.get(rule.rule_key)
        if current is None or version.version_number > current[1].version_number:
            newest[rule.rule_key] = (rule, version)

    version_ids = [v.rule_version_id for _, v in newest.values()]
    maps = await _lookups(db, tenant)
    children = await _children(db, version_ids)

    views = [
        compilerview.build(
            rule,
            version,
            children["groups"].get(version.rule_version_id, []),
            children["conditions"].get(version.rule_version_id, []),
            children["actions"].get(version.rule_version_id, []),
            children["parameters"].get(version.rule_version_id, []),
            stage_code=maps["stages"].get(rule.rule_stage_id, ""),
            rule_type_code=getattr(
                maps["types"].get(rule.rule_type_id), "code", ""
            ),
            stacking_codes=maps["stacking"],
            conflict_codes=maps["conflicts"],
        )
        for rule, version in newest.values()
    ]
    # Same ordering as the legacy loader, so a snapshot's row order does not
    # shift under the swap — and a diff between the last legacy snapshot and the
    # first canonical one shows real changes rather than a reshuffle.
    return sorted(
        views, key=lambda v: (v.execution_stage, -v.specificity, v.rule_key)
    )


async def _lookups(db: AsyncSession, tenant_id: str) -> dict[str, dict]:
    async def by_id(model, key: str, value: str) -> dict:
        rows = (
            await db.execute(select(model).where(model.tenant_id == tenant_id))
        ).scalars().all()
        return {getattr(r, key): (r if value == "*" else getattr(r, value)) for r in rows}

    return {
        "stages": await by_id(RuleStage, "rule_stage_id", "code"),
        "types": await by_id(RuleTypeRow, "rule_type_id", "*"),
        "stacking": await by_id(
            RuleStackingPolicyRow, "stacking_policy_id", "code"
        ),
        "conflicts": await by_id(
            RuleConflictGroupRow, "conflict_group_id", "code"
        ),
    }


async def _children(db: AsyncSession, version_ids: list[str]) -> dict[str, dict]:
    """Every child row for the whole snapshot, in four queries.

    Grouped in Python rather than joined, because a join across four one-to-many
    tables multiplies rows and the assembly cost of un-multiplying them exceeds
    the four round trips it saves.
    """
    out: dict[str, dict] = {}
    for key, model in (
        ("groups", RuleConditionGroup),
        ("conditions", RuleConditionRow),
        ("actions", RuleActionRow),
        ("parameters", RuleParameter),
    ):
        grouped: dict[str, list] = {}
        rows = (
            await db.execute(
                select(model).where(model.rule_version_id.in_(version_ids))
            )
        ).scalars().all()
        for row in rows:
            grouped.setdefault(row.rule_version_id, []).append(row)
        out[key] = grouped
    return out


async def mark_compiled(
    db: AsyncSession, rule_keys: list[str], *, tenant_id: str | None = None
) -> None:
    """Promote APPROVED canonical rules to COMPILED after a snapshot is built.

    The legacy path does this by assigning to the mapped row it just compiled.
    The canonical path cannot: what it compiled was a projection over five
    tables, and assigning to it would update nothing while looking exactly like
    it had worked. Both the rule and its version move, because either one left
    behind makes the catalogue and the rule's own history disagree.
    """
    if not rule_keys:
        return
    tenant = tenant_id or settings.default_tenant_id
    rule_ids = (
        (
            await db.execute(
                select(CanonicalRule.rule_id).where(
                    CanonicalRule.tenant_id == tenant,
                    CanonicalRule.rule_key.in_(rule_keys),
                    CanonicalRule.status == RuleStatus.APPROVED,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rule_ids:
        return
    await db.execute(
        update(CanonicalRule)
        .where(CanonicalRule.rule_id.in_(rule_ids))
        .values(status=RuleStatus.COMPILED)
    )
    await db.execute(
        update(CanonicalRuleVersion)
        .where(
            CanonicalRuleVersion.rule_id.in_(rule_ids),
            CanonicalRuleVersion.status == RuleStatus.APPROVED,
        )
        .values(status=RuleStatus.COMPILED)
    )


async def rule_set_id_for_code(
    db: AsyncSession, code: str, *, tenant_id: str | None = None
) -> str | None:
    tenant = tenant_id or settings.default_tenant_id
    return (
        await db.execute(
            select(CanonicalRuleSet.rule_set_id).where(
                CanonicalRuleSet.tenant_id == tenant, CanonicalRuleSet.code == code
            )
        )
    ).scalar_one_or_none()
