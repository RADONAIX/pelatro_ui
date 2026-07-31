"""Everything the Snapshot Details screen needs to answer four questions.

A snapshot is the single source of truth for what the rating engine is using, so
the page describing one has to answer, without the reader leaving it:

* **What rules are in v11?** — the rule table, and the execution order they run in.
* **What changed since v10?** — the diff, against the predecessor, found for you.
* **Is it safe to activate?** — the compile report and its issues.
* **What will activating it do?** — impact, and this is the one worth care.

**Impact is measured, not estimated.** There is no subscriber-to-product table in
this service, so "1.2M subscribers affected" would be a number we invented. What
there *is* is `rating_results`, which records the executable rules that priced
every event we have rated — so "the rules that changed in v11 priced 4.2M events
and £310k of charge in the last 30 days, across 84k distinct subscribers" is a
fact, drawn from traffic that actually happened.

Where there is no rated traffic to draw on, the answer is "no rated traffic in
this window" rather than a zero that reads as "no impact". Those are very
different statements and only one of them is true.

Every function here is a read. Nothing in this module can change a snapshot, and
none of the existing snapshot endpoints route through it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.compiler.models import ExecutableRule, RuleSnapshot
from app.modules.compiler.service import get_snapshot
from app.modules.rating.models import RatingResult
from app.modules.rules.constants import STAGE_ORDER

#: How far back to look for traffic when measuring impact. Thirty days is a
#: billing cycle plus a margin — long enough that a weekly pattern cannot
#: dominate, short enough that a tariff retired last quarter does not.
DEFAULT_IMPACT_DAYS = 30

#: Catalogue dimensions an executable rule can pin, and what to call them.
_REACH_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("product_code", "Products"),
    ("offer_code", "Offers"),
    ("tariff_plan_code", "Tariff plans"),
    ("service_type", "Services"),
    ("destination_zone", "Destination zones"),
    ("time_band", "Time bands"),
    ("rating_group", "Rating groups"),
)


# --- Previous snapshot -------------------------------------------------------


async def previous_snapshot(
    db: AsyncSession, snapshot: RuleSnapshot
) -> RuleSnapshot | None:
    """The snapshot this one succeeded — the highest version below it.

    By version rather than by ``superseded_at``, because a snapshot compiled and
    never activated has no supersession relationship and still has a meaningful
    predecessor. "What changed since the last one" is a question about the
    sequence, not about what happened to be live.
    """
    return (
        await db.execute(
            select(RuleSnapshot)
            .where(RuleSnapshot.version < snapshot.version)
            .order_by(RuleSnapshot.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# --- Compile report ----------------------------------------------------------


@dataclass(slots=True)
class CompileReport:
    snapshot_id: str
    version: int
    status: str
    checksum: str
    rule_count: int
    compiled_by: str | None
    compiled_at: datetime | None
    #: True when the compile was forced past blocking issues. Surfaced first,
    #: because an override that is only visible in a log is one nobody sees.
    forced: bool = False
    error_count: int = 0
    warning_count: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    #: Blocking findings grouped by cause, same discipline as everywhere else.
    grouped_issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def safe_to_activate(self) -> bool:
        """No errors, and it was not forced past any.

        Deliberately conservative: a forced compile is safe to activate only in
        the sense that somebody already decided it was, and the screen should say
        so rather than agreeing on their behalf.
        """
        return self.error_count == 0 and not self.forced


def compile_report(snapshot: RuleSnapshot) -> CompileReport:
    issues = list(snapshot.issues or [])
    stats = dict(snapshot.stats or {})

    errors = [i for i in issues if str(i.get("severity", "")).upper() == "ERROR"]
    warnings = [i for i in issues if str(i.get("severity", "")).upper() == "WARNING"]

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in issues:
        key = (str(issue.get("severity", "")), str(issue.get("code", "")))
        row = grouped.setdefault(
            key,
            {
                "severity": issue.get("severity"),
                "code": issue.get("code"),
                "message": issue.get("message", ""),
                "count": 0,
                "examples": [],
            },
        )
        row["count"] += 1
        path = issue.get("path") or issue.get("rule_key")
        if path and len(row["examples"]) < 5:
            row["examples"].append(path)

    return CompileReport(
        snapshot_id=snapshot.id,
        version=snapshot.version,
        status=snapshot.status,
        checksum=snapshot.checksum,
        rule_count=snapshot.rule_count,
        compiled_by=snapshot.compiled_by_name,
        compiled_at=snapshot.created_at,
        forced=bool(stats.get("forced")),
        error_count=len(errors),
        warning_count=len(warnings),
        issues=issues,
        stats=stats,
        grouped_issues=sorted(grouped.values(), key=lambda r: -r["count"]),
    )


# --- Execution order ---------------------------------------------------------


@dataclass(slots=True)
class StageGroup:
    stage: str
    stage_order: int
    rule_count: int
    #: The order rules are considered within the stage: specificity first, then
    #: priority. Shown because "why did that rule win?" is the most common
    #: question a snapshot has to answer.
    rules: list[dict[str, Any]] = field(default_factory=list)


async def execution_order(
    db: AsyncSession, snapshot_id: str, *, sample: int = 10
) -> list[StageGroup]:
    """The snapshot's rules as the engine will walk them: stage by stage.

    A flat list of 4,000 rules cannot answer "what happens to a call, in what
    order". Grouped by stage in execution order, it can — and the empty stages
    are as informative as the full ones, because a pipeline with no ROUNDING
    stage rounds nothing.
    """
    rows = (
        await db.execute(
            select(
                ExecutableRule.execution_stage,
                ExecutableRule.stage_order,
                func.count().label("rule_count"),
            )
            .where(ExecutableRule.snapshot_id == snapshot_id)
            .group_by(ExecutableRule.execution_stage, ExecutableRule.stage_order)
            .order_by(ExecutableRule.stage_order)
        )
    ).all()

    groups: list[StageGroup] = []
    for stage, order, count in rows:
        top = (
            await db.execute(
                select(ExecutableRule)
                .where(
                    ExecutableRule.snapshot_id == snapshot_id,
                    ExecutableRule.execution_stage == stage,
                )
                .order_by(
                    ExecutableRule.specificity.desc(),
                    ExecutableRule.priority.desc(),
                    ExecutableRule.rule_key,
                )
                .limit(sample)
            )
        ).scalars().all()
        groups.append(
            StageGroup(
                stage=stage,
                stage_order=int(order),
                rule_count=int(count),
                rules=[
                    {
                        "rule_key": r.rule_key,
                        "rule_name": r.rule_name,
                        "rule_type": r.rule_type,
                        "specificity": r.specificity,
                        "priority": r.priority,
                        "stacking_policy": r.stacking_policy,
                        "conflict_group": r.conflict_group,
                        "product_code": r.product_code,
                        "service_type": r.service_type,
                    }
                    for r in top
                ],
            )
        )

    # Stages the pipeline defines but this snapshot has no rules for. Reported
    # so an operator can see that, say, nothing rounds — an absence that is
    # invisible in a list of what is present.
    present = {g.stage for g in groups}
    for index, stage in enumerate(STAGE_ORDER):
        if stage not in present:
            groups.append(StageGroup(stage=stage, stage_order=index, rule_count=0))
    return sorted(groups, key=lambda g: g.stage_order)


# --- Impact ------------------------------------------------------------------


@dataclass(slots=True)
class Reach:
    """What the snapshot's rules target in the catalogue."""

    dimension: str
    label: str
    #: `None` in an executable rule means "any", so a rule with no product
    #: pinned reaches every product. Counted separately: "12 products named,
    #: plus 40 rules that apply to all of them" is the honest summary.
    values: list[str] = field(default_factory=list)
    wildcard_rules: int = 0


@dataclass(slots=True)
class TrafficImpact:
    """Measured from events this platform actually rated."""

    window_days: int
    from_date: date
    to_date: date
    #: False when there is no rated traffic to draw on at all — which is a very
    #: different statement from an impact of zero.
    has_traffic: bool = False
    rated_events: int = 0
    affected_events: int = 0
    distinct_subscribers: int = 0
    affected_charge: Decimal = Decimal("0")
    currency: str = ""
    #: The rules whose change drives the number, most-used first.
    top_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Impact:
    snapshot_id: str
    version: int
    rule_count: int
    reach: list[Reach] = field(default_factory=list)
    traffic: TrafficImpact | None = None
    #: Rule keys that differ from the previous snapshot, when there is one.
    changed_rule_keys: list[str] = field(default_factory=list)
    compared_with_version: int | None = None
    note: str = ""


async def impact(
    db: AsyncSession,
    snapshot_id: str,
    *,
    window_days: int = DEFAULT_IMPACT_DAYS,
    changed_only: bool = True,
) -> Impact:
    """What activating this snapshot would touch.

    Two halves, and they answer different questions. **Reach** is what the rules
    target — derived exactly from the compiled dimensions, always available, and
    true whether or not anything has been rated. **Traffic** is what those rules
    have actually priced, drawn from `rating_results`, and only meaningful once
    the platform has rated something.

    ``changed_only`` narrows traffic to the rules that differ from the previous
    snapshot, which is the number an operator is really asking for: not "how big
    is this tariff" but "how much of my traffic does this change move".
    """
    snapshot = await get_snapshot(db, snapshot_id)
    result = Impact(
        snapshot_id=snapshot.id,
        version=snapshot.version,
        rule_count=snapshot.rule_count,
        reach=await _reach(db, snapshot_id),
    )

    rule_keys: list[str] | None = None
    if changed_only:
        previous = await previous_snapshot(db, snapshot)
        if previous is not None:
            result.compared_with_version = previous.version
            rule_keys = await _changed_keys(db, previous.id, snapshot.id)
            result.changed_rule_keys = rule_keys
            if not rule_keys:
                result.note = (
                    f"Nothing changed between v{previous.version} and "
                    f"v{snapshot.version}, so activating it moves no traffic."
                )
        else:
            result.note = (
                "This is the first snapshot, so every rule in it is new and the "
                "traffic figures below cover all of them."
            )

    result.traffic = await _traffic(db, snapshot_id, rule_keys, window_days)
    return result


async def _reach(db: AsyncSession, snapshot_id: str) -> list[Reach]:
    """Which catalogue entities the snapshot's rules name.

    A NULL dimension means "any", so it is counted as a wildcard rather than
    ignored — forty rules that apply to every product are a larger blast radius
    than twelve that name one each, and a list of named codes alone would imply
    the opposite.
    """
    out: list[Reach] = []
    for column_name, label in _REACH_DIMENSIONS:
        column = getattr(ExecutableRule, column_name)
        values = (
            await db.execute(
                select(column)
                .where(ExecutableRule.snapshot_id == snapshot_id, column.isnot(None))
                .distinct()
                .order_by(column)
            )
        ).scalars().all()
        wildcards = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ExecutableRule)
                    .where(
                        ExecutableRule.snapshot_id == snapshot_id, column.is_(None)
                    )
                )
            ).scalar_one()
        )
        out.append(
            Reach(
                dimension=column_name,
                label=label,
                values=[v for v in values if v],
                wildcard_rules=wildcards,
            )
        )
    return out


async def _changed_keys(
    db: AsyncSession, from_id: str, to_id: str
) -> list[str]:
    """Rule keys added or altered between two snapshots.

    Compared on ``signature`` — the compiled match key — rather than on the whole
    row, because a rule whose id changed but whose behaviour did not is not a
    change anyone cares about.
    """
    async def rows(snapshot_id: str) -> dict[str, str]:
        found = (
            await db.execute(
                select(ExecutableRule.rule_key, ExecutableRule.signature).where(
                    ExecutableRule.snapshot_id == snapshot_id
                )
            )
        ).all()
        return dict(found)

    before, after = await rows(from_id), await rows(to_id)
    changed = [k for k, sig in after.items() if before.get(k) != sig]
    removed = [k for k in before if k not in after]
    return sorted({*changed, *removed})


async def _traffic(
    db: AsyncSession,
    snapshot_id: str,
    rule_keys: list[str] | None,
    window_days: int,
) -> TrafficImpact:
    """Events these rules actually priced, in the recent window.

    The join is through ``rating_results.selected_rule_ids``, which records the
    executable rule chosen at each stage — so this counts events the rules in
    question genuinely decided, not events that merely happen to look like them.
    """
    to_date = datetime.now(UTC).date()
    from_date = to_date - timedelta(days=window_days)
    traffic = TrafficImpact(
        window_days=window_days, from_date=from_date, to_date=to_date
    )

    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RatingResult)
                .where(RatingResult.event_date >= from_date)
            )
        ).scalar_one()
    )
    traffic.rated_events = total
    traffic.has_traffic = total > 0
    if not total:
        return traffic

    ids = await _executable_ids(db, snapshot_id, rule_keys)
    if not ids:
        return traffic

    # `selected_rule_ids` is {stage: executable_rule_id}, so the test is whether
    # any of the snapshot's ids appear among its values. Done in Python over the
    # window rather than in SQL because the JSONB shape is a map keyed by stage,
    # and a containment operator over its *values* is not indexable anyway.
    rows = (
        await db.execute(
            select(
                RatingResult.selected_rule_ids,
                RatingResult.subscriber_id,
                RatingResult.expected_final_charge,
                RatingResult.currency,
            ).where(RatingResult.event_date >= from_date)
        )
    ).all()

    subscribers: set[str] = set()
    charge = Decimal("0")
    per_rule: dict[str, int] = defaultdict(int)
    currencies: set[str] = set()

    for selected, subscriber, final_charge, currency in rows:
        hits = {v for v in (selected or {}).values() if v in ids}
        if not hits:
            continue
        traffic.affected_events += 1
        if subscriber:
            subscribers.add(subscriber)
        if final_charge is not None:
            charge += Decimal(str(final_charge))
        if currency:
            currencies.add(currency)
        for hit in hits:
            per_rule[hit] += 1

    traffic.distinct_subscribers = len(subscribers)
    traffic.affected_charge = charge
    traffic.currency = currencies.pop() if len(currencies) == 1 else ""

    if per_rule:
        named = dict(
            (
                await db.execute(
                    select(ExecutableRule.id, ExecutableRule.rule_key).where(
                        ExecutableRule.id.in_(per_rule)
                    )
                )
            ).all()
        )
        traffic.top_rules = sorted(
            (
                {"rule_key": named.get(rid, rid), "events": count}
                for rid, count in per_rule.items()
            ),
            key=lambda r: -r["events"],
        )[:20]
    return traffic


async def _executable_ids(
    db: AsyncSession, snapshot_id: str, rule_keys: list[str] | None
) -> set[str]:
    stmt = select(ExecutableRule.id).where(ExecutableRule.snapshot_id == snapshot_id)
    if rule_keys is not None:
        if not rule_keys:
            return set()
        stmt = stmt.where(ExecutableRule.rule_key.in_(rule_keys))
    return set((await db.execute(stmt)).scalars().all())
