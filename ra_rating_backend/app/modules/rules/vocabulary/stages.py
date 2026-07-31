"""The canonical execution-stage registry — mode-scoped and gap-numbered.

Ordering here IS the charging sequence. Two properties are load-bearing:

**Mode scoping.** ``applies_to`` puts each stage in COMMON, PREPAID or POSTPAID,
so ``pipeline_for(mode)`` derives the sequence for a charging mode instead of a
branch somewhere in the engine. Adding a postpaid stage cannot perturb prepaid.

**Gap numbering.** ``execution_order`` steps by 10 so a stage can be inserted
between two existing ones without renumbering anything. Renumbering would
silently reorder a live pipeline, which is the kind of change that produces a
month of wrong invoices before anyone notices.

Relationship to the legacy ``constants.STAGE_ORDER``: the eleven stages that
exist today keep their codes and their *relative* order here, and their legacy
tuple is left untouched — the compiler derives ``executable_rules.stage_order``
from that tuple's positions, so extending it would renumber every snapshot.
``test_canonical_vocabulary.py`` asserts the two agree on the overlap.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rules.vocabulary.modes import CATEGORIES_FOR_MODE, RuleCategory


@dataclass(frozen=True, slots=True)
class StageSpec:
    code: str
    name: str
    applies_to: str
    execution_order: int
    #: Needs per-subscriber state (balance, counter, reservation) to evaluate.
    #: The offline assurance engine can only reproduce these when the state
    #: store is populated, so it is worth knowing which stages they are.
    is_stateful: bool = False
    #: True for the eleven stages that predate the canonical model.
    legacy: bool = False
    description: str = ""


_S = StageSpec

CANONICAL_STAGES: tuple[StageSpec, ...] = (
    # --- Common: classification ------------------------------------------
    _S("ELIGIBILITY", "Eligibility", RuleCategory.COMMON, 10,
       description="Is this product/subscriber entitled to be charged by these rules at all."),
    _S("SERVICE_CLASSIFICATION", "Service classification", RuleCategory.COMMON, 20,
       description="Resolve the event to a service and rating group."),
    _S("DESTINATION_CLASSIFICATION", "Destination classification", RuleCategory.COMMON, 30,
       description="Resolve the called number to a destination zone."),
    _S("TIME_BAND", "Time band", RuleCategory.COMMON, 40,
       description="Resolve the event timestamp to a peak/off-peak/weekend band."),
    # --- Common: quantity and rate ---------------------------------------
    _S("QUANTITY", "Quantity", RuleCategory.COMMON, 50, legacy=True,
       description="Floor or adjust billable quantity before any rate applies."),
    _S("TARIFF_SELECTION", "Tariff selection", RuleCategory.COMMON, 60, legacy=True),
    _S("MINIMUM_CHARGE", "Minimum charge", RuleCategory.COMMON, 70, legacy=True),
    _S("PULSE", "Pulse", RuleCategory.COMMON, 80, legacy=True),
    _S("BASE_CHARGE", "Base charge", RuleCategory.COMMON, 90, legacy=True),
    _S("BUNDLE", "Bundle", RuleCategory.COMMON, 100, legacy=True, is_stateful=True),
    _S("PROMOTION", "Promotion", RuleCategory.COMMON, 110, legacy=True, is_stateful=True),
    _S("DISCOUNT", "Discount", RuleCategory.COMMON, 120, legacy=True),
    _S("SURCHARGE", "Surcharge", RuleCategory.COMMON, 130, legacy=True),
    # A cap evaluated in the same pass as the rate it caps is order-dependent.
    # The stage exists from R1; SET_MAXIMUM_CHARGE only moves onto it in R4,
    # together with a before/after parity report — see the plan's D12.
    _S("MAXIMUM_CHARGE", "Maximum charge", RuleCategory.COMMON, 140,
       description="Cap applied after discounts and surcharges have settled."),
    _S("TAX", "Tax", RuleCategory.COMMON, 150, legacy=True),
    _S("ROUNDING", "Rounding", RuleCategory.COMMON, 160, legacy=True),
    # --- Prepaid ----------------------------------------------------------
    _S("BALANCE_SELECTION", "Balance selection", RuleCategory.PREPAID, 200, is_stateful=True,
       description="Which bucket pays, and in what order — main vs promotional vs bundle."),
    _S("BALANCE_RESERVATION", "Balance reservation", RuleCategory.PREPAID, 210, is_stateful=True,
       description="Online quota reservation and release. Session-scoped."),
    _S("BALANCE_DEDUCTION", "Balance deduction", RuleCategory.PREPAID, 220, is_stateful=True),
    _S("BALANCE_EXCEPTION", "Balance exception", RuleCategory.PREPAID, 230, is_stateful=True,
       description="Insufficient, zero and negative balance; partial-session charging."),
    _S("SESSION_CONTROL", "Session control", RuleCategory.PREPAID, 240,
       description="Terminate, redirect or throttle once the balance cannot pay."),
    # --- Postpaid ---------------------------------------------------------
    _S("BILLING_CYCLE_ASSIGNMENT", "Billing cycle assignment", RuleCategory.POSTPAID, 300),
    _S("USAGE_AGGREGATION", "Usage aggregation", RuleCategory.POSTPAID, 310, is_stateful=True,
       description="Roll rated usage up to the subscriber, account or group for the period."),
    _S("RECURRING_CHARGE", "Recurring charge", RuleCategory.POSTPAID, 320,
       description="Monthly rental and any other cyclic charge."),
    _S("ONE_TIME_CHARGE", "One-time charge", RuleCategory.POSTPAID, 330),
    _S("PRORATION", "Proration", RuleCategory.POSTPAID, 340),
    _S("CREDIT_CHECK", "Credit check", RuleCategory.POSTPAID, 350, is_stateful=True),
    _S("INVOICE_COMPONENT", "Invoice component", RuleCategory.POSTPAID, 360,
       description="Assemble the charge into a named invoice line."),
    _S("INVOICE_TAX", "Invoice tax", RuleCategory.POSTPAID, 370),
    _S("LATE_FEE", "Late fee", RuleCategory.POSTPAID, 380),
    _S("INVOICE_ROUNDING", "Invoice rounding", RuleCategory.POSTPAID, 390),
)

STAGE_BY_CODE: dict[str, StageSpec] = {s.code: s for s in CANONICAL_STAGES}

#: Codes of the stages that existed before the canonical model. Used by the
#: parity test and by the R4 cut-over to map legacy rows onto canonical stages.
LEGACY_STAGE_CODES: tuple[str, ...] = tuple(s.code for s in CANONICAL_STAGES if s.legacy)


def pipeline_for(charging_mode: str) -> tuple[StageSpec, ...]:
    """The ordered stage sequence a rule of this charging mode runs through.

    This is the whole of "do not keep prepaid and postpaid in separate engines":
    one registry, one ordering, filtered by category.
    """
    allowed = CATEGORIES_FOR_MODE.get(charging_mode)
    if allowed is None:
        raise KeyError(f"Unknown charging mode '{charging_mode}'.")
    return tuple(
        s for s in sorted(CANONICAL_STAGES, key=lambda s: s.execution_order)
        if s.applies_to in allowed
    )


def stage_codes_for(charging_mode: str) -> frozenset[str]:
    """Stage codes reachable from a charging mode — the mode-coherence check."""
    return frozenset(s.code for s in pipeline_for(charging_mode))
