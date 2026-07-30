"""Expected vs actual (§17), root cause (§18) and exception grouping (§19).

Two decisions shape everything here:

**Undercharge and overcharge are never netted.** They are different failures
with different owners — one is revenue leakage, the other is customer harm and
a regulatory exposure. A run that undercharges £1m and overcharges £1m is not
a clean run.

**Exceptions are grouped, not per-CDR.** One broken rule mispricing 40,000 calls
is one investigation. Handing an analyst 40,000 tickets guarantees the cause is
never found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.modules.cdr.enrich import QualityStatus
from app.modules.rating.constants import (
    DEFAULT_TOLERANCE,
    AssuranceStatus,
    RootCause,
    Severity,
)
from app.modules.rating.engine import RatingOutcome
from app.modules.rules.constants import ExecutionStage

ZERO = Decimal("0")

#: One minor unit of a 2-decimal currency. A variance at or below this is a
#: rounding difference between two correct calculations, not a component fault.
ROUNDING_SCALE = Decimal("0.01")


@dataclass
class Verdict:
    status: str
    root_cause: str | None
    variance: Decimal
    explanation: str = ""


def classify(
    cdr: Any,
    outcome: RatingOutcome,
    *,
    ambiguous: list[str] | None = None,
    tolerance: Decimal = Decimal(str(DEFAULT_TOLERANCE)),
) -> Verdict:
    """Compare one expected charge against what was billed."""
    actual = cdr.actual_charge
    actual_dec = Decimal(str(actual)) if actual is not None else None

    # --- Failures that make the comparison meaningless ---------------------
    if outcome.error:
        return Verdict(AssuranceStatus.ENGINE_ERROR, RootCause.UNKNOWN, ZERO, outcome.error)

    if cdr.quality_status == QualityStatus.PRODUCT_NOT_FOUND:
        return Verdict(
            AssuranceStatus.PRODUCT_NOT_FOUND,
            RootCause.WRONG_PRODUCT_MAPPING,
            ZERO,
            f"Product '{cdr.product_code or '—'}' is not in the catalogue, so no tariff applies.",
        )
    if cdr.quality_status == QualityStatus.SUBSCRIBER_NOT_FOUND:
        return Verdict(
            AssuranceStatus.PRODUCT_NOT_FOUND,
            RootCause.REFERENCE_DATA_ERROR,
            ZERO,
            f"No product assignment found for {cdr.msisdn or 'this subscriber'} on "
            f"{cdr.event_date}.",
        )
    if cdr.quality_status == QualityStatus.DESTINATION_NOT_FOUND:
        return Verdict(
            AssuranceStatus.NO_MATCHING_RULE,
            RootCause.WRONG_DESTINATION,
            ZERO,
            f"'{cdr.called_number}' matches no prefix, so no destination zone was resolved.",
        )

    if ExecutionStage.BASE_CHARGE.value in outcome.missing_stages:
        return Verdict(
            AssuranceStatus.NO_MATCHING_RULE,
            RootCause.NO_TARIFF_DEFINED,
            ZERO,
            "No rule in the active snapshot prices this context.",
        )

    if ambiguous:
        return Verdict(
            AssuranceStatus.MULTIPLE_RULE_MATCH,
            RootCause.INCORRECT_TARIFF,
            ZERO,
            f"Several equally-ranked rules matched at: {', '.join(ambiguous)}.",
        )

    expected = outcome.final_charge

    if actual_dec is None:
        # Rateable usage the billing system produced no charge for. That is the
        # purest form of leakage: the full expected amount is missing.
        return Verdict(
            AssuranceStatus.UNRATED,
            RootCause.BILLING_DEPLOYMENT_ISSUE,
            expected,
            f"Expected {expected} but the source carried no charge at all.",
        )

    if cdr.currency and outcome.currency and cdr.currency.upper() != outcome.currency.upper():
        return Verdict(
            AssuranceStatus.CURRENCY_MISMATCH,
            RootCause.REFERENCE_DATA_ERROR,
            ZERO,
            f"Billed in {cdr.currency}, tariff is priced in {outcome.currency}.",
        )

    variance = expected - actual_dec

    if outcome.zero_rated:
        if abs(actual_dec) <= tolerance:
            return Verdict(AssuranceStatus.ZERO_CHARGE, None, ZERO, "Free usage, billed as free.")
        return Verdict(
            AssuranceStatus.OVERCHARGED,
            RootCause.INCORRECT_TARIFF,
            variance,
            f"Usage should be free but {actual_dec} was billed.",
        )

    if abs(variance) <= tolerance:
        return Verdict(AssuranceStatus.MATCHED, None, ZERO, "Billed as expected.")

    cause = infer_root_cause(cdr, outcome, variance)
    if variance > 0:
        return Verdict(
            AssuranceStatus.UNDERCHARGED,
            cause,
            variance,
            f"Expected {expected}, billed {actual_dec} — {variance} under.",
        )
    return Verdict(
        AssuranceStatus.OVERCHARGED,
        cause,
        variance,
        f"Expected {expected}, billed {actual_dec} — {abs(variance)} over.",
    )


def infer_root_cause(cdr: Any, outcome: RatingOutcome, variance: Decimal) -> str:
    """First-component-mismatch reasoning (§18).

    Rather than guessing, test the ratio between expected and actual against the
    signatures each component failure leaves. A missing 15% tax leaves the actual
    at exactly 1/1.15 of expected; a missing discount leaves it above expected by
    the discount rate. Those are recognisable.
    """
    actual = Decimal(str(cdr.actual_charge))
    expected = outcome.final_charge
    if expected == ZERO:
        return RootCause.INCORRECT_TARIFF

    # Checked FIRST, and deliberately so. A variance of one minor unit means
    # every component agreed and the two sides rounded differently — attributing
    # it to tax or pulse would send an analyst chasing a component that is
    # actually correct. Only once the difference is bigger than rounding can it
    # be evidence about which component diverged.
    if abs(variance) <= ROUNDING_SCALE:
        return RootCause.ROUNDING_ISSUE

    ratio = actual / expected

    # Tax omitted by billing: actual ≈ expected minus exactly the tax component.
    if outcome.tax > ZERO:
        without_tax = expected - outcome.tax
        if without_tax > ZERO and abs(actual - without_tax) <= Decimal("0.01"):
            return RootCause.TAX_INCORRECT

    # Discount not applied: actual ≈ what the charge would be before discount.
    if outcome.discount > ZERO:
        without_discount = expected + outcome.discount
        if abs(actual - without_discount) <= Decimal("0.01"):
            return RootCause.DISCOUNT_MISSING

    # Pulse ignored: billing charged the measured duration, we charged whole
    # pulses — the ratio lands near the un-pulsed fraction, always below 1.
    if (
        ExecutionStage.PULSE.value not in outcome.missing_stages
        and Decimal("0.5") < ratio < Decimal("1")
    ):
        return RootCause.WRONG_PULSE

    # A clean multiple of the expected charge is a rate that is simply wrong.
    if ratio > Decimal("1.05") or ratio < Decimal("0.95"):
        return RootCause.INCORRECT_TARIFF

    return RootCause.UNKNOWN


def severity_for(revenue_impact: Decimal, cdr_count: int) -> str:
    """Severity from money first, volume second.

    A large absolute impact is what gets escalated; a high-volume, low-value
    pattern still matters because it usually means a systematic rule fault.
    """
    impact = abs(revenue_impact)
    if impact >= Decimal("10000") or cdr_count >= 100_000:
        return Severity.CRITICAL
    if impact >= Decimal("1000") or cdr_count >= 10_000:
        return Severity.HIGH
    if impact >= Decimal("100") or cdr_count >= 1_000:
        return Severity.MEDIUM
    return Severity.LOW


@dataclass
class ExceptionGroup:
    """Accumulates the results that failed the same way."""

    group_key: str
    assurance_status: str
    root_cause: str
    service_type: str | None = None
    product_code: str | None = None
    destination_zone: str | None = None
    rule_key: str | None = None
    cdr_count: int = 0
    subscribers: set[str] = field(default_factory=set)
    expected_total: Decimal = ZERO
    actual_total: Decimal = ZERO
    revenue_impact: Decimal = ZERO
    first_event_date: date | None = None
    last_event_date: date | None = None
    sample_result_id: str | None = None
    sample_explanation: str = ""

    def add(
        self,
        *,
        result_id: str,
        subscriber: str | None,
        expected: Decimal,
        actual: Decimal | None,
        variance: Decimal,
        event_date: date,
        explanation: str,
    ) -> None:
        self.cdr_count += 1
        if subscriber:
            self.subscribers.add(subscriber)
        self.expected_total += expected
        self.actual_total += actual if actual is not None else ZERO
        self.revenue_impact += variance
        if self.first_event_date is None or event_date < self.first_event_date:
            self.first_event_date = event_date
        if self.last_event_date is None or event_date > self.last_event_date:
            self.last_event_date = event_date
        if self.sample_result_id is None:
            self.sample_result_id = result_id
            self.sample_explanation = explanation


def group_key_for(verdict: Verdict, cdr: Any, rule_key: str | None) -> str:
    """What makes two failures "the same problem".

    Deliberately excludes the subscriber and the CDR: grouping on those would
    produce one exception per subscriber and hide the shared cause.
    """
    return "|".join(
        [
            verdict.status,
            verdict.root_cause or RootCause.UNKNOWN,
            cdr.service_type or "*",
            cdr.product_code or "*",
            cdr.destination_zone or "*",
            rule_key or "*",
        ]
    )


def title_for(group: ExceptionGroup) -> str:
    scope = " · ".join(
        p for p in (group.product_code, group.service_type, group.destination_zone) if p
    )
    readable = group.assurance_status.replace("_", " ").lower()
    return f"{readable.capitalize()} — {scope or 'all traffic'}"


#: What to tell the analyst, per root cause. Written as the next action to take,
#: not as a restatement of the problem.
_ADVICE: dict[str, tuple[str, str]] = {
    RootCause.TAX_INCORRECT: (
        "The billed amount matches the charge before tax, so the billing system "
        "is not applying the tax this tariff defines.",
        "Check the tax configuration on the charging system for this product.",
    ),
    RootCause.DISCOUNT_MISSING: (
        "The billed amount matches the undiscounted charge — the discount is "
        "defined here but not being applied downstream.",
        "Verify the discount is deployed and active in the charging system.",
    ),
    RootCause.WRONG_PULSE: (
        "The billed amount tracks measured duration rather than whole pulses.",
        "Confirm the pulse configured in the charging system matches the tariff.",
    ),
    RootCause.INCORRECT_TARIFF: (
        "The billed amount is a different rate, not a different calculation.",
        "Compare the rate deployed in the charging system against the published snapshot.",
    ),
    RootCause.NO_TARIFF_DEFINED: (
        "No rule in the active snapshot prices this traffic, so no expected "
        "charge could be produced.",
        "Author a rule for this context, or add a service-wide default.",
    ),
    RootCause.WRONG_DESTINATION: (
        "The dialled number matches no prefix in the numbering plan.",
        "Add the prefix to the right destination zone and replay the batch.",
    ),
    RootCause.WRONG_PRODUCT_MAPPING: (
        "The product on the CDR is not in the catalogue, so no tariff could apply.",
        "Correct the product mapping, or import the missing product.",
    ),
    RootCause.REFERENCE_DATA_ERROR: (
        "Reference data needed to rate this usage is missing.",
        "Load the missing reference data, then replay.",
    ),
    RootCause.BILLING_DEPLOYMENT_ISSUE: (
        "Rateable usage arrived with no charge at all, which usually means the "
        "rating step did not run for it.",
        "Check the charging system for failed or skipped records in this period.",
    ),
    RootCause.ROUNDING_ISSUE: (
        "The difference is at the rounding boundary.",
        "Confirm both sides use the same rounding mode and precision.",
    ),
}


def advice_for(root_cause: str) -> tuple[str, str]:
    return _ADVICE.get(
        root_cause,
        (
            "The expected and billed amounts differ for a reason the engine "
            "could not attribute to a single component.",
            "Open the calculation trace on a sample CDR to see which step diverges.",
        ),
    )
