"""Cross-rule conflict detection over the canonical store.

The tier a per-rule validator cannot reach: every check here needs two rules at
once, which is why it lives apart from ``validation/`` and runs over the estate
rather than over a draft.

Three kinds, each a different failure and each with a different fix:

**Overlapping live windows in a conflict group.** Two rules that are supposed to
be mutually exclusive, both live on the same day. This is two prices for one
event — the most expensive incident class in a rating platform, and the reason
``rule_version`` carries a GiST exclusion constraint on ``(rule_id, window)``.
That constraint stops one *rule* contradicting itself; nothing stops two
different rules in one group doing it, so it is checked here.

**Identical behaviour under different keys.** Two rules whose behaviour hashes
match. Harmless until someone changes one of them and cannot work out why the
charge did not move — the other is still there.

**An unbreakable tie.** Same stage, same targeting, same priority *and* the same
specificity. The winner is then decided by row order, which is stable within a
compile and not guaranteed between them. A tariff that quietly changes on
republish is worse than one that is wrong consistently.

Everything is a read. Nothing here blocks a save — the report is what an operator
reviews before publishing, which is the point at which a conflict actually
matters.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rules.api import schemas as s
from app.modules.rules.canonical.lookups import RuleConflictGroupRow, RuleStage
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.constants import RuleStatus

#: Statuses that mean "this rule can rate an event". A draft cannot conflict with
#: anything, and reporting that it does trains operators to ignore the report.
_LIVE: frozenset[str] = frozenset(
    {RuleStatus.ACTIVE, RuleStatus.PUBLISHED, RuleStatus.APPROVED}
)


def _overlaps(
    a_from: date, a_to: date | None, b_from: date, b_to: date | None
) -> bool:
    """Inclusive on both ends, and an open end really is open."""
    return (a_to is None or b_from <= a_to) and (b_to is None or a_from <= b_to)


async def detect(
    db: AsyncSession,
    *,
    charging_mode: str | None = None,
    service_type: str | None = None,
) -> s.ConflictReport:
    stmt = (
        select(CanonicalRule, CanonicalRuleVersion)
        .join(
            CanonicalRuleVersion,
            CanonicalRuleVersion.rule_version_id == CanonicalRule.current_version_id,
        )
        .where(CanonicalRule.status.in_(_LIVE))
    )
    if charging_mode:
        stmt = stmt.where(CanonicalRule.charging_mode == charging_mode)
    if service_type:
        stmt = stmt.where(CanonicalRule.service_type == service_type)

    rows = list((await db.execute(stmt)).all())
    stages = {
        r.rule_stage_id: r.code for r in (await db.execute(select(RuleStage))).scalars()
    }
    groups = {
        r.conflict_group_id: r.code
        for r in (await db.execute(select(RuleConflictGroupRow))).scalars()
    }

    conflicts: list[s.ConflictOut] = []
    conflicts.extend(_group_overlaps(rows, stages, groups))
    conflicts.extend(_duplicate_behaviour(rows, stages))
    conflicts.extend(_unbreakable_ties(rows, stages))
    return s.ConflictReport(examined=len(rows), conflicts=conflicts)


def _group_overlaps(rows, stages, groups) -> list[s.ConflictOut]:
    by_group: dict[str, list] = defaultdict(list)
    for rule, version in rows:
        if version.conflict_group_id:
            by_group[version.conflict_group_id].append((rule, version))

    out: list[s.ConflictOut] = []
    for group_id, members in by_group.items():
        for index, (rule_a, version_a) in enumerate(members):
            for rule_b, version_b in members[index + 1 :]:
                if not _overlaps(
                    version_a.effective_from, version_a.effective_to,
                    version_b.effective_from, version_b.effective_to,
                ):
                    continue
                out.append(
                    s.ConflictOut(
                        kind="OVERLAPPING_WINDOW",
                        severity="ERROR",
                        message=(
                            f"'{rule_a.rule_name}' and '{rule_b.rule_name}' are in the "
                            f"same conflict group and are both live between "
                            f"{max(version_a.effective_from, version_b.effective_from)} "
                            "and the earlier of their end dates."
                        ),
                        rule_keys=[rule_a.rule_key, rule_b.rule_key],
                        conflict_group=groups.get(group_id, ""),
                        stage_code=stages.get(rule_a.rule_stage_id, ""),
                        hint="Close one window before the other opens, or move one "
                             "rule out of the group.",
                    )
                )
    return out


def _duplicate_behaviour(rows, stages) -> list[s.ConflictOut]:
    by_hash: dict[str, list] = defaultdict(list)
    for rule, version in rows:
        if version.behaviour_hash:
            by_hash[version.behaviour_hash].append((rule, version))

    return [
        s.ConflictOut(
            kind="DUPLICATE_BEHAVIOUR",
            severity="WARNING",
            message=(
                f"{len(members)} rules say exactly the same thing: "
                + ", ".join(sorted(r.rule_name for r, _ in members))
            ),
            rule_keys=sorted(r.rule_key for r, _ in members),
            stage_code=stages.get(members[0][0].rule_stage_id, ""),
            hint="Retire the duplicates. Left in place, changing one of them "
                 "produces no visible effect and nobody can see why.",
        )
        for members in by_hash.values()
        if len(members) > 1
    ]


def _unbreakable_ties(rows, stages) -> list[s.ConflictOut]:
    by_key: dict[tuple, list] = defaultdict(list)
    for rule, version in rows:
        by_key[
            (
                rule.rule_stage_id,
                rule.charging_mode,
                rule.service_type,
                version.product_id,
                version.offer_id,
                version.priority,
                version.specificity_score,
            )
        ].append((rule, version))

    out: list[s.ConflictOut] = []
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        # Rules that already differ behaviourally are reported by the duplicate
        # check; this one is about *selection*, so it fires regardless.
        out.append(
            s.ConflictOut(
                kind="UNBREAKABLE_TIE",
                severity="WARNING",
                message=(
                    f"{len(members)} rules target the same events at the same stage "
                    f"with priority {key[5]} and specificity {key[6]}. Which one wins "
                    "is decided by row order, and row order is not guaranteed to be "
                    "the same on the next compile."
                ),
                rule_keys=sorted(r.rule_key for r, _ in members),
                stage_code=stages.get(key[0], ""),
                hint="Give one of them a higher priority, or narrow its conditions "
                     "so specificity separates them.",
            )
        )
    return out
