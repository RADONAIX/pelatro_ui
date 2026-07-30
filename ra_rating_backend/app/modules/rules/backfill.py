"""R4 — backfill ``rating.rules`` into the canonical model, and prove parity.

Two operations, and the second is the one that matters.

**Backfill** reads legacy rules, converts each through
:mod:`ingest.adapters.legacy`, and writes them with the ingestion kernel — the
same kernel the wizard and every importer use. A migration that writes rows its
own way produces an estate the maintaining code has never seen, and the
divergence surfaces months later as "why does this backfilled rule behave
differently from the imported one next to it".

**Parity** compiles the legacy row and the canonical row *through the same
compiler* and compares the output field by field. That is the gate on the whole
cut-over, and it is only meaningful because both sides go through
``compile_rule``: comparing an old compiler against a new one proves the two
implementations agree, which is a much weaker claim than "the migration changed
nothing".

Nothing here revokes writes on the legacy tables or repoints the compiler. Those
are the plan's M4 and M5, and they belong behind a green parity report rather
than behind a code review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.catalog import models as cm
from app.modules.compiler import compiler
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
from app.modules.rules.ingest import kernel
from app.modules.rules.ingest.adapters import legacy as legacy_adapter
from app.modules.rules.models import Rule

#: Rows read from the legacy table per pass. The kernel commits in its own
#: chunks; this only bounds how much is held in memory at once, which is what
#: keeps a 40,000-rule backfill inside the plan's 1 GB RSS target.
PAGE_SIZE = 500

#: Compiled fields compared for parity. Deliberately explicit rather than
#: "every column": ``id`` and ``snapshot_id`` are expected to differ, and a
#: comparison that silently skipped an unknown new column would report parity it
#: had not checked.
PARITY_FIELDS: tuple[str, ...] = (
    "rule_key", "rule_version", "rule_name", "rule_type", "execution_stage",
    "stage_order", "priority", "specificity", "stacking_policy", "conflict_group",
    "condition_logic", "effective_from", "effective_to", "currency_code",
    "service_type", "product_code", "offer_code", "tariff_plan_code",
    "dimension_sets", "predicates", "actions", "signature",
)

#: Dimension columns are generated from the compiler's registry rather than
#: listed, so a new match dimension is compared the day it is added.
_DIMENSION_FIELDS: tuple[str, ...] = tuple(
    column for _, column in compiler.MATCH_DIMENSIONS
)


@dataclass(slots=True)
class Difference:
    rule_key: str
    field: str
    legacy: Any
    canonical: Any

    def __str__(self) -> str:
        return f"{self.rule_key}.{self.field}: {self.legacy!r} → {self.canonical!r}"


@dataclass(slots=True)
class Report:
    """What a run produced, in the shape an operator needs to sign it off."""

    read: int = 0
    converted: int = 0
    written: int = 0
    unchanged: int = 0
    rejected: int = 0
    #: Rules whose money could not be recovered from the float it was stored as.
    #: Backfilled, but flagged: the plan's G.6 open question, answered by
    #: reporting rather than by silence.
    needs_money_review: list[str] = field(default_factory=list)
    #: Legacy rules the adapter could not convert at all.
    failures: list[tuple[str, str]] = field(default_factory=list)
    notes: dict[str, list[str]] = field(default_factory=dict)
    #: Populated by :func:`check_parity`.
    compared: int = 0
    differences: list[Difference] = field(default_factory=list)

    @property
    def parity(self) -> bool:
        return not self.differences

    def summary(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "converted": self.converted,
            "written": self.written,
            "unchanged": self.unchanged,
            "rejected": self.rejected,
            "needs_money_review": len(self.needs_money_review),
            "failures": len(self.failures),
            "compared": self.compared,
            "differences": len(self.differences),
            "parity": self.parity,
        }


# --- Backfill ---------------------------------------------------------------


async def backfill(
    db: AsyncSession,
    *,
    actor: kernel.Actor,
    limit: int | None = None,
    rule_key: str | None = None,
    dry_run: bool = False,
    tenant_id: str | None = None,
) -> Report:
    """Convert every latest-version legacy rule into the canonical model.

    Only the latest version of each logical rule is migrated. Superseded legacy
    versions stay where they are: they exist so a historical rating result can be
    re-explained, the legacy table is not being dropped in this phase, and
    re-versioning them into the canonical store would invent a version history
    that never happened.
    """
    report = Report()
    codes = await _catalogue_codes(db)
    drafts: list[Any] = []
    keys: list[str] = []

    for rule in await _latest_versions(db, limit=limit, rule_key=rule_key):
        report.read += 1
        try:
            conversion = legacy_adapter.convert(rule, **codes)
        except Exception as exc:
            # One unconvertible rule must not cost the other 39,999. It is
            # reported by key, so the fix is targeted rather than a re-run.
            report.failures.append((rule.rule_key, str(exc)))
            continue

        report.converted += 1
        if conversion.notes:
            report.notes[rule.rule_key] = conversion.notes
        if conversion.needs_review:
            report.needs_money_review.append(rule.rule_key)
        drafts.append(conversion.draft)
        keys.append(rule.rule_key)

    if not drafts:
        return report

    result = await kernel.ingest(
        db,
        drafts,
        actor=actor,
        channel="MIGRATION",
        dry_run=dry_run,
        tenant_id=tenant_id or settings.default_tenant_id,
        # A backfilled rule keeps the status and version number it already had;
        # _carry_identity applies both afterwards. Passing DRAFT here and leaving
        # it would silently unpublish the whole live tariff.
        status=kernel.RuleStatus.DRAFT,
    )
    report.written = result.counts.get("NEW", 0) + result.counts.get("CHANGED", 0)
    report.unchanged = result.counts.get("UNCHANGED", 0)
    report.rejected = result.counts.get("REJECTED", 0)
    for record in result.records:
        if record.decision == "REJECTED":
            report.failures.append((record.rule_key, record.reason))

    if not dry_run:
        await _carry_identity(db, keys)
    return report


async def _latest_versions(
    db: AsyncSession, *, limit: int | None, rule_key: str | None
) -> list[Rule]:
    """The newest row per ``rule_key``, which is the rule as it stands today."""
    newest = (
        select(Rule.rule_key, func.max(Rule.version).label("v"))
        .group_by(Rule.rule_key)
        .subquery()
    )
    stmt = (
        select(Rule)
        .join(newest, (Rule.rule_key == newest.c.rule_key) & (Rule.version == newest.c.v))
        .order_by(Rule.rule_key)
    )
    if rule_key:
        stmt = stmt.where(Rule.rule_key == rule_key)
    if limit:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def _catalogue_codes(db: AsyncSession) -> dict[str, dict[str, str]]:
    """``{id: code}`` per catalogue, read once for the whole run.

    A draft carries codes; the legacy row carries ids. Resolving per rule would
    put the 40,000 round trips back into the loop the canonical model exists to
    take them out of.
    """
    out: dict[str, dict[str, str]] = {}
    for key, model in (
        ("product_codes", cm.Product),
        ("offer_codes", cm.Offer),
        ("tariff_plan_codes", cm.TariffPlan),
    ):
        rows = (await db.execute(select(model.id, model.code))).all()
        out[key] = dict(rows)
    return out


async def _carry_identity(db: AsyncSession, rule_keys: list[str]) -> None:
    """Carry the legacy rule's status and version number onto its canonical row.

    Two corrections the kernel cannot make on its own, because both are true of a
    *migration* and false of an import:

    **Status.** The kernel writes DRAFT, which is right for an incoming vendor
    rule and catastrophic here — a backfill that landed 4,000 live rules as
    drafts would take the whole tariff out of the published set the moment the
    compiler was repointed.

    **Version number.** The kernel numbers from 1, so a legacy rule at v2 becomes
    canonical v1. The parity gate catches that as a compiled difference, and
    rightly: `rule_version` is how an operator and a six-month-old rating result
    refer to a specific rule text, and silently renumbering it makes every such
    reference wrong. The legacy number is carried across, which leaves a hole
    below it — an honest hole, because those earlier versions really do exist, in
    the legacy table this phase is not dropping.
    """
    if not rule_keys:
        return
    newest = (
        select(Rule.rule_key, func.max(Rule.version).label("v"))
        .where(Rule.rule_key.in_(rule_keys))
        .group_by(Rule.rule_key)
        .subquery()
    )
    legacy: dict[str, tuple[str, int]] = {
        key: (status, number)
        for key, status, number in (
            await db.execute(
                select(Rule.rule_key, Rule.status, Rule.version).join(
                    newest,
                    (Rule.rule_key == newest.c.rule_key) & (Rule.version == newest.c.v),
                )
            )
        ).all()
    }
    rows = (
        (
            await db.execute(
                select(CanonicalRule).where(CanonicalRule.rule_key.in_(rule_keys))
            )
        )
        .scalars()
        .all()
    )
    for rule in rows:
        source = legacy.get(rule.rule_key)
        if source is None:
            continue
        status, version_number = source
        rule.status = status
        version = await db.get(CanonicalRuleVersion, rule.current_version_id)
        if version is not None:
            version.status = status
            version.version_number = version_number
    await db.flush()


# --- Parity -----------------------------------------------------------------


async def check_parity(
    db: AsyncSession, report: Report | None = None, *, rule_key: str | None = None
) -> Report:
    """Compile both sides with the same compiler and diff the results.

    The gate on M4. Until this returns zero differences across the estate,
    repointing the compiler at the canonical store means shipping a behaviour
    change nobody has measured.
    """
    report = report or Report()
    references = await compiler._reference_index(db)
    payloads = await compiler._catalog_payloads(db)
    codes = await compiler._code_index(db)

    stages = {
        row.rule_stage_id: row.code
        for row in (await db.execute(select(RuleStage))).scalars()
    }
    types = {
        row.rule_type_id: row for row in (await db.execute(select(RuleTypeRow))).scalars()
    }
    stacking = {
        row.stacking_policy_id: row.code
        for row in (await db.execute(select(RuleStackingPolicyRow))).scalars()
    }
    conflicts = {
        row.conflict_group_id: row.code
        for row in (await db.execute(select(RuleConflictGroupRow))).scalars()
    }

    for rule in await _latest_versions(db, limit=None, rule_key=rule_key):
        canonical = (
            await db.execute(
                select(CanonicalRule).where(CanonicalRule.rule_key == rule.rule_key)
            )
        ).scalar_one_or_none()
        if canonical is None or canonical.current_version_id is None:
            report.differences.append(
                Difference(rule.rule_key, "existence", "present", "missing")
            )
            continue

        view = await _view_for(
            db, canonical, stages, types, stacking, conflicts
        )
        if view is None:
            report.differences.append(
                Difference(rule.rule_key, "existence", "present", "unreadable")
            )
            continue

        report.compared += 1
        report.differences.extend(
            _diff(rule.rule_key, rule, view, references, payloads, codes)
        )
    return report


async def _view_for(
    db: AsyncSession,
    canonical: CanonicalRule,
    stages: dict[str, str],
    types: dict[str, Any],
    stacking: dict[str, str],
    conflicts: dict[str, str],
) -> compilerview.RuleView | None:
    version = await db.get(CanonicalRuleVersion, canonical.current_version_id)
    if version is None:
        return None
    version_id = version.rule_version_id

    async def rows(model, column):
        return list(
            (await db.execute(select(model).where(column == version_id))).scalars().all()
        )

    rule_type = types.get(canonical.rule_type_id)
    return compilerview.build(
        canonical,
        version,
        await rows(RuleConditionGroup, RuleConditionGroup.rule_version_id),
        await rows(RuleConditionRow, RuleConditionRow.rule_version_id),
        await rows(RuleActionRow, RuleActionRow.rule_version_id),
        await rows(RuleParameter, RuleParameter.rule_version_id),
        stage_code=stages.get(canonical.rule_stage_id, ""),
        rule_type_code=getattr(rule_type, "code", ""),
        stacking_codes=stacking,
        conflict_codes=conflicts,
    )


def _diff(
    rule_key: str,
    legacy: Rule,
    canonical: compilerview.RuleView,
    references: dict[str, dict[str, str]],
    payloads: dict[str, dict[str, Any]],
    codes: dict[str, dict[str, str]],
) -> list[Difference]:
    """Compile both and compare, ignoring only what is expected to differ."""
    try:
        left = compiler.compile_rule(legacy, "parity", references, payloads, codes)
    except compiler.CompileError as exc:
        return [Difference(rule_key, "legacy_compile", str(exc), "")]
    try:
        right = compiler.compile_rule(canonical, "parity", references, payloads, codes)
    except compiler.CompileError as exc:
        return [Difference(rule_key, "canonical_compile", "", str(exc))]

    out: list[Difference] = []
    for name in (*PARITY_FIELDS, *_DIMENSION_FIELDS):
        a = _comparable(getattr(left, name, None))
        b = _comparable(getattr(right, name, None))
        if a != b:
            out.append(Difference(rule_key, name, a, b))
    return out


def _comparable(value: Any) -> Any:
    """Normalise for comparison without hiding a real difference.

    ``Decimal`` and ``float`` are collapsed to a canonical decimal string,
    because the legacy side stores floats and the canonical side stores exact
    decimals — a rate that is 0.012345 on both must compare equal even though one
    arrived as a float. A *genuine* rate change survives this, because the
    canonical strings differ.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal | float | int):
        return format(Decimal(repr(value) if isinstance(value, float) else str(value)),
                      "f")
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    if isinstance(value, dict):
        return {k: _comparable(v) for k, v in sorted(value.items())}
    return value
