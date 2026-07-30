"""Expected versus actual — the assurance verdict (§22).

    variance = actual - expected

**The sign is the finding.** Positive means the customer paid more than the
tariff allows: a refund, a regulator's interest, and reputational damage.
Negative means revenue was never collected. They are opposite problems with
opposite owners, and an absolute value hides which one you have. Nothing here
ever returns an unsigned variance.

**Two tolerances, either one sufficient (§33).** An absolute floor stops
sub-cent rounding noise being reported as leakage across a million records; a
percentage floor stops a large charge being flagged for a difference that is
immaterial to it. Requiring *both* would suppress a real 40% variance on a small
charge, so the test is deliberately a disjunction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.modules.cdr.bulk_enrich import EnrichmentStatus
from app.modules.rating.actual_charge import ActualCharge
from app.modules.rating.audit_models import ActualChargeStatus, RatingStatus

ZERO = Decimal("0")


@dataclass
class Comparison:
    status: str
    expected: Decimal
    actual: Decimal | None
    variance: Decimal
    absolute_variance: Decimal
    variance_percentage: Decimal | None
    explanation: str
    within_tolerance: bool = False


def _percentage(variance: Decimal, expected: Decimal) -> Decimal | None:
    """Variance as a percentage of what was expected.

    Undefined when nothing was expected: a charge of 0.60 against an expected 0
    is not "infinity per cent over", it is a charge that should not exist, and
    the status already says so.
    """
    if expected == ZERO:
        return None
    return (variance / expected * Decimal(100)).quantize(Decimal("0.0001"))


def compare(
    *,
    expected: Decimal,
    actual_charge: ActualCharge,
    enrichment_status: str,
    has_base_rule: bool,
    is_ambiguous: bool = False,
    calculation_failed: bool = False,
    currency_scale: int | None = None,
) -> Comparison:
    """Classify one usage record.

    The failure states are checked before the arithmetic, because a variance
    computed from an expected charge we could not legitimately produce is a
    fabricated number, however arithmetically correct it is.
    """
    scale = Decimal(1).scaleb(-(currency_scale or settings.default_currency_scale))
    expected = expected.quantize(scale)
    actual = actual_charge.amount

    def verdict(status: str, explanation: str) -> Comparison:
        return Comparison(
            status=status,
            expected=expected,
            actual=actual,
            variance=ZERO,
            absolute_variance=ZERO,
            variance_percentage=None,
            explanation=explanation,
        )

    # --- States where no comparison is legitimate --------------------------
    if calculation_failed:
        return verdict(
            RatingStatus.CALCULATION_FAILED,
            "The expected charge could not be calculated, so no comparison was made.",
        )
    if is_ambiguous:
        return verdict(
            RatingStatus.AMBIGUOUS_RULE,
            "Two or more rules were equally entitled to price this record. "
            "No charge was calculated, because choosing between them would be a guess.",
        )
    if enrichment_status not in {
        EnrichmentStatus.ENRICHED,
        EnrichmentStatus.PARTIALLY_ENRICHED,
    }:
        return verdict(
            RatingStatus.ENRICHMENT_FAILED,
            f"The record could not be enriched ({enrichment_status}), so it could not be rated.",
        )
    if not has_base_rule:
        return verdict(
            RatingStatus.NO_RULE_FOUND,
            "No active rule prices this combination of service, destination and plan.",
        )
    if not actual_charge.is_usable or actual is None:
        # Several candidate charges is still "no usable actual": comparing
        # against an arbitrary one of them would invent or erase a variance.
        if actual_charge.status == ActualChargeStatus.MULTIPLE_ACTUAL_MATCHES:
            return verdict(
                RatingStatus.NO_ACTUAL_CHARGE,
                f"Expected {expected}. {actual_charge.detail}",
            )
        return verdict(
            RatingStatus.NO_ACTUAL_CHARGE,
            f"Expected {expected}, but no billed amount could be established. "
            f"{actual_charge.detail}".strip(),
        )

    # --- The comparison -----------------------------------------------------
    actual = actual.quantize(scale)
    variance = actual - expected
    absolute = abs(variance)
    percentage = _percentage(variance, expected)

    within = absolute <= Decimal(str(settings.absolute_variance_tolerance)) or (
        percentage is not None
        and abs(percentage) <= Decimal(str(settings.percentage_variance_tolerance))
    )

    if within:
        status = RatingStatus.MATCHED
        explanation = (
            f"Billed {actual} against an expected {expected}"
            + (" — an exact match." if variance == ZERO else f"; the {absolute} difference is "
               "within tolerance.")
        )
    elif expected == ZERO and actual > ZERO:
        status = RatingStatus.OVERCHARGED
        explanation = (
            f"Billed {actual} where the rules expect no charge at all. "
            "The whole amount is an overcharge."
        )
    elif actual == ZERO:
        status = RatingStatus.ZERO_CHARGED
        explanation = (
            f"Nothing was billed, but the rules expect {expected}. "
            "The usage was rateable and went uncharged."
        )
    elif variance > ZERO:
        status = RatingStatus.OVERCHARGED
        explanation = (
            f"Billed {actual} against an expected {expected} — the customer was "
            f"charged {absolute} too much"
            + (f" ({percentage}%)." if percentage is not None else ".")
        )
    else:
        status = RatingStatus.UNDERCHARGED
        explanation = (
            f"Billed {actual} against an expected {expected} — {absolute} of revenue "
            "was not collected"
            + (f" ({percentage}%)." if percentage is not None else ".")
        )

    return Comparison(
        status=status,
        expected=expected,
        actual=actual,
        variance=variance,
        absolute_variance=absolute,
        variance_percentage=percentage,
        explanation=explanation,
        within_tolerance=within,
    )
