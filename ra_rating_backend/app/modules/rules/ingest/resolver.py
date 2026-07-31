"""Batched code → id resolution, cached for the life of one ingestion run.

The legacy importers called ``latest_version()`` and a catalogue lookup **per row,
inside the commit loop**. At 40,000 rows that is tens of thousands of round trips,
which is why a full Ericsson dump took hours rather than minutes. Here the whole
batch declares what it needs up front, and each catalogue is read once.

The other job this class does is give a *useful* answer when a code is missing. A
prepaid rule referencing ``balance-types`` before the R7 tables exist should say so
plainly — "that catalogue is not available in this release" — instead of the
literal truth, "'MAIN' does not exist", which sends an operator hunting for a
data-entry mistake that was never made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.charging import models as chm
from app.modules.rules.canonical.lookups import (
    RuleConflictGroupRow,
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.canonical.rule import CanonicalRule
from app.modules.rules.canonical.sets import CanonicalRuleSet
from app.modules.rules.vocabulary.actions import (
    REFERENCED_CATALOGUES as ACTION_CATALOGUES,
)
from app.modules.rules.vocabulary.attributes import (
    REFERENCED_CATALOGUES as ATTRIBUTE_CATALOGUES,
)

#: Catalogue slug → model. Values are entity *codes*, never ids, so a rule set
#: exported from staging imports into production unchanged.
REFERENCE_MODELS: dict[str, type[Any]] = {
    "products": cm.Product,
    "offers": cm.Offer,
    "tariff-plans": cm.TariffPlan,
    "destination-zones": cm.DestinationZone,
    "time-bands": cm.TimeBand,
    "rating-groups": cm.RatingGroup,
    "currencies": cm.Currency,
    "tax-rules": cm.TaxRule,
    "rounding-rules": cm.RoundingRule,
    "discounts": cm.DiscountDefinition,
    "bundles": cm.BundleDefinition,
    "promotions": cm.Promotion,
    # --- Charging, prepaid and postpaid metadata ---------------------------
    # Same contract as the rest: keyed by *code*, resolved once per batch. These
    # live in the rule-management schema rather than `rating`, which changes
    # nothing here — the resolver reads a model, not a schema.
    "charging-units": chm.ChargingUnit,
    "pulse-profiles": chm.PulseProfile,
    "charge-limit-profiles": chm.ChargeLimitProfile,
    "balance-types": chm.BalanceType,
    "balance-buckets": chm.BalanceBucketDefinition,
    "ocs-profiles": chm.OcsProfile,
    "reservation-policies": chm.ReservationPolicy,
    "charging-profiles": chm.ChargingProfile,
    "balance-priorities": chm.BalancePriority,
    "proration-profiles": chm.ProrationProfile,
    "billing-cycles": chm.BillingCycle,
    "invoice-components": chm.InvoiceComponent,
    "recurring-charges": chm.RecurringCharge,
    "one-time-charges": chm.OneTimeCharge,
    "credit-limit-profiles": chm.CreditLimitProfile,
    "usage-aggregation-profiles": chm.UsageAggregationProfile,
    "late-fee-profiles": chm.LateFeeProfile,
}

#: Catalogues the vocabulary references but no model backs. Empty as of the
#: charging-metadata phase, and a test keeps it that way: a reference the
#: resolver cannot resolve is a dropdown with no source behind it.
PENDING_CATALOGUES: frozenset[str] = frozenset(
    (ACTION_CATALOGUES | ATTRIBUTE_CATALOGUES) - set(REFERENCE_MODELS)
)


@dataclass(slots=True)
class ResolutionCache:
    """Per-run cache. One instance per ingestion batch, discarded afterwards.

    Not a process-level cache on purpose: a catalogue entry created between two
    nightly imports must be visible to the second one, and a long-lived cache of
    "codes that do not exist" is exactly the sort of state that produces a support
    ticket nobody can reproduce.
    """

    db: AsyncSession
    tenant_id: str
    #: slug → {CODE: id}
    catalogues: dict[str, dict[str, str]] = field(default_factory=dict)
    stages: dict[str, str] = field(default_factory=dict)
    rule_types: dict[str, RuleTypeRow] = field(default_factory=dict)
    stacking: dict[str, str] = field(default_factory=dict)
    conflict_groups: dict[str, str] = field(default_factory=dict)
    rule_sets: dict[str, str] = field(default_factory=dict)
    #: source system code → id. Not a catalogue slug — source systems live in the
    #: `rating` schema and are keyed by code, so they get their own map rather than
    #: being wedged into the catalogue lookup where the miss would be silent.
    source_systems: dict[str, str] = field(default_factory=dict)
    #: rule_key → (rule_id, behaviour_hash of the current version)
    rule_index: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    #: Kept separate so the hot reconciliation tuple remains backward-compatible.
    #: A retired identity is special: seeing it in a new import means the tariff
    #: has been reintroduced and must cut a fresh version, even when unchanged.
    rule_statuses: dict[str, str] = field(default_factory=dict)
    #: (source_system_id, external_ref) → rule_key, for identity-by-vendor-key.
    external_index: dict[tuple[str, str], str] = field(default_factory=dict)
    #: (rule_set_id, rule_id) pairs written during this run, so re-importing a rule
    #: already in a set does not trip the unique constraint mid-batch. Per-run
    #: state, never process-global.
    written_memberships: set[tuple[str, str]] = field(default_factory=set)

    # --- Vocabulary --------------------------------------------------------

    async def load_vocabulary(self) -> None:
        """Read the lookup tables once. Cheap — tens of rows, not thousands."""
        rows = (
            await self.db.execute(
                select(RuleStage).where(RuleStage.tenant_id == self.tenant_id)
            )
        ).scalars().all()
        self.stages = {r.code: r.rule_stage_id for r in rows}

        types = (
            await self.db.execute(
                select(RuleTypeRow).where(RuleTypeRow.tenant_id == self.tenant_id)
            )
        ).scalars().all()
        self.rule_types = {r.code: r for r in types}

        policies = (
            await self.db.execute(
                select(RuleStackingPolicyRow).where(
                    RuleStackingPolicyRow.tenant_id == self.tenant_id
                )
            )
        ).scalars().all()
        self.stacking = {r.code: r.stacking_policy_id for r in policies}

        groups = (
            await self.db.execute(
                select(RuleConflictGroupRow).where(
                    RuleConflictGroupRow.tenant_id == self.tenant_id
                )
            )
        ).scalars().all()
        self.conflict_groups = {r.code: r.conflict_group_id for r in groups}

        sets_ = (
            await self.db.execute(
                select(CanonicalRuleSet).where(CanonicalRuleSet.tenant_id == self.tenant_id)
            )
        ).scalars().all()
        self.rule_sets = {r.code: r.rule_set_id for r in sets_}

        from app.modules.connectors.models import SourceSystem

        sources = (
            await self.db.execute(select(SourceSystem.code, SourceSystem.id))
        ).all()
        self.source_systems = dict(sources)

    def source_system_id(self, code: str | None) -> str | None:
        if not code:
            return None
        return self.source_systems.get(str(code).strip())

    async def load_rule_index(self, source_system_id: str | None = None) -> None:
        """Pre-load every rule key and its current behaviour hash.

        One query replaces the per-row ``latest_version()`` the legacy importers
        ran inside their commit loops. Reconciliation then decides
        new/changed/unchanged in memory.
        """
        from app.modules.rules.canonical.rule import CanonicalRuleVersion

        stmt = (
            select(
                CanonicalRule.rule_key,
                CanonicalRule.rule_id,
                CanonicalRule.source_system_id,
                CanonicalRule.external_ref,
                CanonicalRule.status,
                CanonicalRuleVersion.behaviour_hash,
            )
            .outerjoin(
                CanonicalRuleVersion,
                CanonicalRuleVersion.rule_version_id == CanonicalRule.current_version_id,
            )
            .where(CanonicalRule.tenant_id == self.tenant_id)
        )
        for key, rule_id, source_id, external_ref, status, hash_ in (
            await self.db.execute(stmt)
        ).all():
            self.rule_index[key] = (rule_id, hash_)
            self.rule_statuses[key] = status
            if source_id and external_ref:
                self.external_index[(source_id, external_ref)] = key
        del source_system_id  # every key is loaded; the parameter documents intent

    # --- Catalogues --------------------------------------------------------

    async def load_catalogues(self, wanted: dict[str, set[str]]) -> dict[str, set[str]]:
        """Resolve the codes a whole batch needs. Returns unresolved codes by slug."""
        missing: dict[str, set[str]] = {}
        for slug, codes in wanted.items():
            if not codes:
                continue
            model = REFERENCE_MODELS.get(slug)
            if model is None:
                missing[slug] = set(codes)
                continue
            known = self.catalogues.setdefault(slug, {})
            outstanding = {c for c in codes if c not in known}
            if outstanding:
                rows = (
                    await self.db.execute(
                        select(model.code, model.id).where(model.code.in_(outstanding))
                    )
                ).all()
                known.update(dict(rows))
            absent = {c for c in codes if c not in known}
            if absent:
                missing[slug] = absent
        return missing

    def catalogue_id(self, slug: str, code: str | None) -> str | None:
        if not code:
            return None
        return self.catalogues.get(slug, {}).get(str(code).strip().upper())

    def describe_missing(self, slug: str, codes: set[str]) -> str:
        """The message an operator reads. Different when the catalogue itself is
        absent, because those are different problems with different fixes."""
        pretty = slug.replace("-", " ")
        if slug in PENDING_CATALOGUES:
            return (
                f"The {pretty} catalogue is not available in this release, so "
                f"{', '.join(sorted(codes))} cannot be resolved yet."
            )
        if slug not in REFERENCE_MODELS:
            return f"'{slug}' is not a catalogue this system knows about."
        listed = ", ".join(sorted(codes)[:5])
        more = "" if len(codes) <= 5 else f" (and {len(codes) - 5} more)"
        return (
            f"{listed}{more} {'do' if len(codes) > 1 else 'does'} not exist in the "
            f"{pretty} catalogue. Create them under Metadata Catalogue, or correct "
            "the value."
        )


async def build(
    db: AsyncSession, tenant_id: str, *, with_rule_index: bool = True
) -> ResolutionCache:
    cache = ResolutionCache(db=db, tenant_id=tenant_id)
    await cache.load_vocabulary()
    if with_rule_index:
        await cache.load_rule_index()
    return cache
