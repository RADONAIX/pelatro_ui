"""Expected-charge calculation (§13, §15) with a full trace (§16).

The charging sequence, in this order and no other:

    1  billable quantity      6  promotion
    2  minimum charge         7  discount
    3  pulse                  8  surcharge
    4  base charge            9  tax
    5  bundle                10  rounding

Order is not cosmetic — tax on a discounted charge is not the same number as a
discount on a taxed charge, and getting it wrong produces a systematic variance
across every CDR.

**Decimal throughout.** Money is never a float here. ``0.1 + 0.2 != 0.3`` in
binary floating point, and across 1 crore CDRs that error accumulates into a
reported leakage figure that is simply wrong.

Every step appends to a trace, so any charge can be explained line by line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

from app.modules.compiler.models import ExecutableRule
from app.modules.rules.constants import ActionType, ExecutionStage

ENGINE_VERSION = "1.0.0"

_ROUNDING_MODES = {
    "HALF_UP": ROUND_HALF_UP,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "CEILING": ROUND_CEILING,
    "FLOOR": ROUND_FLOOR,
    "TRUNCATE": ROUND_DOWN,
}

#: Seconds per unit, for converting a duration into the unit a rate is quoted in.
_SECONDS_PER_UNIT = {"SECOND": Decimal(1), "MINUTE": Decimal(60)}
#: Bytes per unit, likewise for data.
_BYTES_PER_UNIT = {
    "BYTE": Decimal(1),
    "KILOBYTE": Decimal(1024),
    "MEGABYTE": Decimal(1024 * 1024),
    "GIGABYTE": Decimal(1024 * 1024 * 1024),
}

ZERO = Decimal("0")


def _plain(value: Decimal) -> str:
    """Format a Decimal for a human-readable trace.

    ``Decimal.normalize()`` renders 60 as ``6E+1``, which is correct and
    useless in a line an analyst reads to check a charge.
    """
    normalised = value.normalize()
    exponent = normalised.as_tuple().exponent
    # A positive exponent is what triggers scientific rendering (60 -> 6E+1).
    if isinstance(exponent, int) and exponent > 0:
        normalised = normalised.quantize(Decimal(1))
    return f"{normalised:f}"


def _dec(value: Any, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return default


@dataclass
class TraceStep:
    step: int
    stage: str
    label: str
    detail: str
    value: str | None = None
    rule_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "stage": self.stage,
            "label": self.label,
            "detail": self.detail,
            "value": self.value,
            "rule_key": self.rule_key,
        }


@dataclass
class RatingOutcome:
    billable_quantity: Decimal = ZERO
    billable_unit: str | None = None
    base_charge: Decimal = ZERO
    discount: Decimal = ZERO
    tax: Decimal = ZERO
    final_charge: Decimal = ZERO
    currency: str | None = None
    trace: list[TraceStep] = field(default_factory=list)
    #: Stages that produced no rule — the difference between "priced at zero"
    #: and "nothing priced it" is the whole point of NO_MATCHING_RULE.
    missing_stages: list[str] = field(default_factory=list)
    zero_rated: bool = False
    error: str | None = None
    #: Set when a bundle covered some or all of the usage.
    bundle_code: str | None = None
    bundle_consumed: Decimal = ZERO
    bundle_overflow: Decimal = ZERO
    #: Usage above the top tier that no rung priced.
    unpriced_quantity: Decimal = ZERO

    def trace_dicts(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.trace]


class _Tracer:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def add(
        self,
        stage: str,
        label: str,
        detail: str,
        value: Any = None,
        rule: ExecutableRule | None = None,
    ) -> None:
        self.steps.append(
            TraceStep(
                step=len(self.steps) + 1,
                stage=stage,
                label=label,
                detail=detail,
                value=None if value is None else str(value),
                rule_key=rule.rule_key if rule else None,
            )
        )


@dataclass
class Tier:
    """One rung of a tiered rate ladder."""

    up_to: Decimal | None  # None = the final, open-ended tier
    rate: Decimal


def parse_tiers(raw: Any) -> list[Tier]:
    """Parse ``up_to:rate`` pairs, cheapest bound first.

    Accepts the compact string form an author types and the list form a
    connector produces, so a tier ladder means the same thing however it
    arrived.
    """
    tiers: list[Tier] = []
    if isinstance(raw, list):
        pairs = [
            (item.get("up_to"), item.get("rate")) if isinstance(item, dict) else (None, item)
            for item in raw
        ]
    else:
        pairs = []
        for chunk in str(raw or "").split(","):
            if not chunk.strip():
                continue
            bound, _, rate = chunk.partition(":")
            pairs.append((bound.strip(), rate.strip()))

    for bound, rate in pairs:
        text = str(bound).strip() if bound is not None else "*"
        upper = None if text in {"*", "", "None", "inf"} else _dec(text)
        tiers.append(Tier(up_to=upper, rate=_dec(rate)))

    # Bounded tiers ascend; the open-ended one is always last regardless of how
    # it was written, because it is the fallback for everything above.
    bounded = sorted((x for x in tiers if x.up_to is not None), key=lambda x: x.up_to)
    open_ended = [x for x in tiers if x.up_to is None]
    return [*bounded, *open_ended]


def charge_across_tiers(
    start: Decimal, quantity: Decimal, tiers: list[Tier], per_units: Decimal
) -> tuple[Decimal, list[tuple[Decimal, Decimal, Decimal]]]:
    """Price ``quantity`` starting from cumulative usage ``start``.

    Returns the charge and the per-tier split, because "why is this £4 and not
    £2" is only answerable by showing which tiers the usage crossed.
    """
    if per_units <= 0:
        per_units = Decimal(1)
    remaining = quantity
    position = start
    total = ZERO
    split: list[tuple[Decimal, Decimal, Decimal]] = []

    for tier in tiers:
        if remaining <= 0:
            break
        headroom = (tier.up_to - position) if tier.up_to is not None else remaining
        if headroom <= 0:
            continue
        take = min(remaining, headroom)
        cost = (take / per_units) * tier.rate
        total += cost
        split.append((take, tier.rate, cost))
        remaining -= take
        position += take

    # Usage above the last bounded tier with no open-ended rung is not free —
    # it is unpriced, and the caller must be able to tell the difference.
    if remaining > 0:
        split.append((remaining, Decimal(-1), ZERO))
    return total, split


def _action_of(rule: ExecutableRule | None, action_type: str) -> dict[str, Any] | None:
    if rule is None:
        return None
    for action in rule.actions or []:
        if action.get("action_type") == action_type:
            return action
    return None


def billable_quantity(cdr: Any, unit: str) -> tuple[Decimal, str]:
    """Raw usage expressed in the unit the rate is quoted in (step 1)."""
    unit = (unit or "").upper()
    if unit in _SECONDS_PER_UNIT:
        seconds = _dec(cdr.duration_seconds)
        return seconds / _SECONDS_PER_UNIT[unit], unit
    if unit in _BYTES_PER_UNIT:
        return _dec(cdr.usage_volume) / _BYTES_PER_UNIT[unit], unit
    if unit in {"MESSAGE", "EVENT"}:
        # SMS CDRs often carry no explicit count; one record is one message.
        volume = _dec(cdr.usage_volume, Decimal(1))
        return (volume if volume > 0 else Decimal(1)), unit
    return _dec(cdr.duration_seconds), "SECOND"


def apply_pulse(quantity_seconds: Decimal, pulse: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Round usage up to whole pulses (step 3).

    Returns ``(chargeable_seconds, whole_pulses)``. A 125-second call on a
    60-second pulse is 3 pulses = 180 chargeable seconds.
    """
    initial = _dec(pulse.get("initial_seconds"))
    if initial <= 0:
        return quantity_seconds, ZERO
    subsequent = _dec(pulse.get("subsequent_seconds"), initial)
    if subsequent <= 0:
        subsequent = initial

    if quantity_seconds <= initial:
        return initial, Decimal(1)
    remainder = quantity_seconds - initial
    extra = (remainder / subsequent).to_integral_value(rounding=ROUND_CEILING)
    return initial + (extra * subsequent), Decimal(1) + extra


#: Seconds/bytes per unit, for translating a bundle's unit into the rate's.
_UNIT_SCALE: dict[str, Decimal] = {
    "SECOND": Decimal(1), "MINUTE": Decimal(60),
    "BYTE": Decimal(1), "KILOBYTE": Decimal(1024),
    "MEGABYTE": Decimal(1024 * 1024), "GIGABYTE": Decimal(1024 * 1024 * 1024),
    "MESSAGE": Decimal(1), "EVENT": Decimal(1),
}


def _from_bundle_unit(quantity: Decimal, bundle_unit: str, rate_unit: str) -> Decimal:
    """Express a bundle's covered amount in the rate's unit.

    A bundle is quoted in minutes or megabytes; a rate may be quoted per second
    or per byte. Comparing them without converting silently gives a 60x or
    1024x error in the customer's favour.
    """
    scale_from = _UNIT_SCALE.get((bundle_unit or "").upper(), Decimal(1))
    scale_to = _UNIT_SCALE.get((rate_unit or "").upper(), Decimal(1))
    if scale_to == 0:
        return quantity
    return quantity * scale_from / scale_to


def rate_charge(quantity: Decimal, rate: Decimal, per_units: Decimal) -> Decimal:
    if per_units <= 0:
        per_units = Decimal(1)
    return (quantity / per_units) * rate


def round_money(amount: Decimal, mode: str, decimals: int) -> Decimal:
    exponent = Decimal(1).scaleb(-decimals)
    return amount.quantize(exponent, rounding=_ROUNDING_MODES.get(mode, ROUND_HALF_UP))


def rate_cdr(
    cdr: Any,
    selected: dict[str, ExecutableRule],
    *,
    bundle: Any = None,
    tier_start: Decimal | None = None,
    bundle_before_pulse: bool = False,
) -> RatingOutcome:
    """Run the charging sequence for one CDR against its selected rules.

    ``bundle`` is a ``balances.Consumption`` when a bundle rule matched and the
    stateful pass already drew from it; ``tier_start`` is that subscriber's
    cumulative usage before this event, for a tiered rate. Both are supplied by
    the caller rather than fetched here, because the engine must stay pure —
    the same inputs must always produce the same charge, which is what makes a
    historical result reproducible.

    ``bundle_before_pulse`` selects which of two defensible conventions applies
    when a call has *both* an allowance and a pulse:

        False (default)  pulse the whole call, then deduct the allowance
        True             deduct the allowance, then pulse what remains

    They are genuinely different amounts — a 100-second call with a 50-second
    allowance on a 60/30 pulse is 70 chargeable seconds under the first and 60
    under the second — and which one is correct is the operator's tariff policy,
    not a fact. The default preserves the behaviour every existing caller
    already relies on; the rating-assurance batch opts in to the second because
    that is the sequence its requirement specifies.
    """
    outcome = RatingOutcome()
    tracer = _Tracer()

    base_rule = selected.get(ExecutionStage.BASE_CHARGE)
    pulse_rule = selected.get(ExecutionStage.PULSE)
    minimum_rule = selected.get(ExecutionStage.MINIMUM_CHARGE)
    bundle_rule = selected.get(ExecutionStage.BUNDLE)
    promotion_rule = selected.get(ExecutionStage.PROMOTION)
    discount_rule = selected.get(ExecutionStage.DISCOUNT)
    surcharge_rule = selected.get(ExecutionStage.SURCHARGE)
    tax_rule = selected.get(ExecutionStage.TAX)
    rounding_rule = selected.get(ExecutionStage.ROUNDING)

    tracer.add(
        "CONTEXT",
        "Rating context",
        f"{cdr.service_type} · product {cdr.product_code or '—'} · "
        f"destination {cdr.destination_zone or '—'} · band {cdr.time_band or '—'}",
    )

    if base_rule is None:
        outcome.missing_stages.append(ExecutionStage.BASE_CHARGE.value)
        tracer.add(
            ExecutionStage.BASE_CHARGE,
            "No base tariff",
            "No rule in the active snapshot prices this context.",
        )
        outcome.trace = tracer.steps
        return outcome

    # --- Zero rating: short-circuits everything before it -------------------
    if _action_of(base_rule, ActionType.SET_ZERO_CHARGE.value) is not None:
        outcome.zero_rated = True
        outcome.currency = base_rule.currency_code or cdr.currency
        tracer.add(
            ExecutionStage.BASE_CHARGE,
            "Zero charge",
            f"'{base_rule.rule_name}' forces a zero charge.",
            "0",
            base_rule,
        )
        outcome.trace = tracer.steps
        return outcome

    # A flat rate and a tier ladder are alternative ways of pricing the same
    # quantity, and both carry the unit and per_units the quantity is measured
    # against — so either one is a valid base action. Requiring SET_RATE
    # specifically would make every tiered rule look like a rule with no price.
    rate_action = _action_of(base_rule, ActionType.SET_RATE.value) or _action_of(
        base_rule, ActionType.SET_TIERED_RATE.value
    )
    if rate_action is None:
        outcome.error = f"Rule '{base_rule.rule_key}' sets no rate."
        outcome.missing_stages.append(ExecutionStage.BASE_CHARGE.value)
        outcome.trace = tracer.steps
        return outcome

    params = rate_action.get("params") or {}
    unit = str(params.get("unit") or "SECOND").upper()
    rate = _dec(params.get("rate"))
    per_units = _dec(params.get("per_units"), Decimal(1))
    outcome.currency = (
        str(params.get("currency") or "").upper() or base_rule.currency_code or cdr.currency
    )

    # --- 1. Billable quantity ----------------------------------------------
    quantity, resolved_unit = billable_quantity(cdr, unit)
    outcome.billable_unit = resolved_unit
    tracer.add(
        ExecutionStage.QUANTITY,
        "Billable quantity",
        f"{_plain(quantity)} {resolved_unit} of raw usage.",
        quantity,
    )

    minimum_quantity = _dec(
        (_action_of(base_rule, ActionType.SET_MINIMUM_QUANTITY.value) or {})
        .get("params", {})
        .get("quantity")
    )
    if minimum_quantity > 0 and quantity < minimum_quantity:
        tracer.add(
            ExecutionStage.QUANTITY,
            "Minimum quantity applied",
            f"{_plain(quantity)} raised to the {_plain(minimum_quantity)} "
            f"{resolved_unit} session minimum.",
            minimum_quantity,
            base_rule,
        )
        quantity = minimum_quantity

    charged_quantity = quantity

    # --- 3. Pulse -----------------------------------------------------------
    def apply_pulse_stage(current: Decimal) -> Decimal:
        if pulse_rule is None:
            tracer.add(ExecutionStage.PULSE, "No pulse", "Usage is charged as measured.")
            return current
        pulse_action = _action_of(pulse_rule, ActionType.SET_PULSE.value)
        if not pulse_action:
            return current
        pulse_params = pulse_action.get("params") or {}
        # Pulse is defined in seconds, so convert, round, convert back.
        scale = _SECONDS_PER_UNIT.get(resolved_unit, Decimal(1))
        seconds = current * scale
        charged_seconds, pulses = apply_pulse(seconds, pulse_params)
        tracer.add(
            ExecutionStage.PULSE,
            "Pulse applied",
            (
                f"{_plain(seconds)}s rounded up to {_plain(pulses)} pulse(s) of "
                f"{_plain(_dec(pulse_params.get('initial_seconds')))}s "
                f"= {_plain(charged_seconds)}s."
            ),
            charged_seconds,
            pulse_rule,
        )
        return charged_seconds / scale

    # --- 5. Bundle ----------------------------------------------------------
    # Applied before the rate, not after it. The documented sequence lists
    # bundles at step 5, but an allowance reduces the quantity that gets
    # charged — pricing the full quantity and discounting afterwards gives a
    # different (wrong) answer the moment the rate is tiered.
    def apply_bundle_stage(current: Decimal) -> Decimal:
        if bundle is not None and bundle_rule is not None:
            covered_native = _from_bundle_unit(bundle.consumed, bundle.unit, resolved_unit)
            # Floored at zero: an allowance larger than the call leaves nothing
            # to charge, never a negative quantity that would credit the
            # customer for not making a longer call.
            chargeable = max(ZERO, current - covered_native)
            outcome.bundle_code = bundle.bucket.bundle_code
            outcome.bundle_consumed = bundle.consumed
            outcome.bundle_overflow = bundle.overflow
            tracer.add(
                ExecutionStage.BUNDLE,
                "Bundle consumed" if bundle.consumed > 0 else "Bundle exhausted",
                (
                    f"{bundle.bucket.bundle_code}: {_plain(bundle.consumed)} of "
                    f"{_plain(bundle.requested)} {bundle.unit} covered, "
                    f"{_plain(bundle.balance_after)} remaining. "
                    f"Charging {_plain(chargeable)} {resolved_unit}."
                ),
                chargeable,
                bundle_rule,
            )
            return chargeable
        if bundle_rule is not None:
            tracer.add(
                ExecutionStage.BUNDLE,
                "Bundle rule matched but no balance",
                "No allowance could be resolved for this subscriber, so nothing was covered.",
                None,
                bundle_rule,
            )
        return current

    stages = (
        (apply_bundle_stage, apply_pulse_stage)
        if bundle_before_pulse
        else (apply_pulse_stage, apply_bundle_stage)
    )
    for stage in stages:
        charged_quantity = stage(charged_quantity)
    outcome.billable_quantity = charged_quantity

    # --- 4. Base charge -----------------------------------------------------
    tier_action = _action_of(base_rule, ActionType.SET_TIERED_RATE.value)
    if tier_action is not None:
        tiers = parse_tiers((tier_action.get("params") or {}).get("tiers"))
        start = tier_start if tier_start is not None else ZERO
        base, split = charge_across_tiers(start, charged_quantity, tiers, per_units)
        for taken, tier_rate, cost in split:
            if tier_rate < 0:
                outcome.unpriced_quantity += taken
                tracer.add(
                    ExecutionStage.BASE_CHARGE,
                    "Usage above the top tier",
                    f"{_plain(taken)} {resolved_unit} fell above every tier and was not priced.",
                    None,
                    base_rule,
                )
            else:
                tracer.add(
                    ExecutionStage.BASE_CHARGE,
                    "Tier applied",
                    (
                        f"{_plain(taken)} {resolved_unit} at {_plain(tier_rate)} "
                        f"per {_plain(per_units)} = {_plain(cost)}"
                    ),
                    cost,
                    base_rule,
                )
        tracer.add(
            ExecutionStage.BASE_CHARGE,
            "Base charge",
            (
                f"Cumulative usage started at {_plain(start)} {resolved_unit}; "
                f"tiered total {_plain(base)}."
            ),
            base,
            base_rule,
        )
    else:
        base = rate_charge(charged_quantity, rate, per_units)
        tracer.add(
            ExecutionStage.BASE_CHARGE,
            "Base charge",
            (
                f"{_plain(charged_quantity)} {resolved_unit} / {_plain(per_units)} "
                f"x {_plain(rate)} = {_plain(base)}"
            ),
            base,
            base_rule,
        )
    outcome.base_charge = base

    connection_fee = _dec((_action_of(base_rule, ActionType.SET_CONNECTION_FEE.value) or {})
                          .get("params", {}).get("amount"))
    if connection_fee > 0:
        base += connection_fee
        outcome.base_charge = base
        tracer.add(
            ExecutionStage.BASE_CHARGE, "Connection fee",
            f"+{_plain(connection_fee)} setup charge.", base, base_rule,
        )

    # --- 2. Minimum charge (a floor, so it applies to the computed base) ----
    if minimum_rule is not None:
        minimum_action = _action_of(minimum_rule, ActionType.SET_MINIMUM_CHARGE.value)
        if minimum_action:
            floor = _dec((minimum_action.get("params") or {}).get("amount"))
            if base < floor:
                tracer.add(
                    ExecutionStage.MINIMUM_CHARGE, "Minimum charge applied",
                    f"{_plain(base)} is below the {_plain(floor)} minimum.", floor, minimum_rule,
                )
                base = floor
                outcome.base_charge = base
            else:
                tracer.add(
                    ExecutionStage.MINIMUM_CHARGE,
                    "Minimum charge not needed",
                    f"{_plain(base)} already exceeds the {_plain(floor)} minimum.",
                    base,
                    minimum_rule,
                )

    maximum_action = _action_of(base_rule, ActionType.SET_MAXIMUM_CHARGE.value)
    if maximum_action:
        cap = _dec((maximum_action.get("params") or {}).get("amount"))
        if cap > 0 and base > cap:
            tracer.add(
                ExecutionStage.BASE_CHARGE, "Maximum charge applied",
                f"{_plain(base)} capped at {_plain(cap)}.", cap, base_rule,
            )
            base = cap
            outcome.base_charge = base

    running = base

    # --- 6. Promotion -------------------------------------------------------
    if promotion_rule is not None:
        promo_action = _action_of(promotion_rule, ActionType.APPLY_PROMOTION.value)
        payload = (promo_action or {}).get("resolved", {}).get("promotion") or {}
        promo_type = str(payload.get("promotion_type") or "").upper()
        value = _dec(payload.get("value"))
        if promo_type in {"FREE_USAGE", "ZERO_RATE"}:
            tracer.add(
                ExecutionStage.PROMOTION, "Promotion — usage free",
                f"{payload.get('code', 'Promotion')} makes this usage free.",
                ZERO, promotion_rule,
            )
            running = ZERO
        elif promo_type in {"PERCENTAGE", "DISCOUNT"} and value > 0:
            reduction = running * value / Decimal(100)
            running -= reduction
            tracer.add(
                ExecutionStage.PROMOTION, "Promotion applied",
                f"{payload.get('code', 'Promotion')}: less {_plain(value)}% = -{_plain(reduction)}",
                running, promotion_rule,
            )
        elif promo_type == "FIXED_AMOUNT" and value > 0:
            reduction = min(value, running)
            running -= reduction
            tracer.add(
                ExecutionStage.PROMOTION, "Promotion applied",
                f"{payload.get('code', 'Promotion')}: less {_plain(reduction)}",
                running, promotion_rule,
            )
        else:
            tracer.add(
                ExecutionStage.PROMOTION, "Promotion matched but not priced",
                f"Promotion type '{promo_type or 'unknown'}' has no charging effect defined.",
                None, promotion_rule,
            )

    # --- 7. Discount --------------------------------------------------------
    discount = ZERO
    if discount_rule is not None:
        discount_action = _action_of(discount_rule, ActionType.APPLY_DISCOUNT.value)
        if discount_action:
            d_params = discount_action.get("params") or {}
            d_resolved = (discount_action.get("resolved") or {}).get("discount") or {}
            percentage = _dec(d_params.get("percentage"))
            amount = _dec(d_params.get("amount"))
            if not percentage and not amount and d_resolved:
                if d_resolved.get("discount_type") == "PERCENTAGE":
                    percentage = _dec(d_resolved.get("value"))
                elif d_resolved.get("discount_type") == "FIXED_AMOUNT":
                    amount = _dec(d_resolved.get("value"))
            discount = (running * percentage / Decimal(100)) if percentage else amount
            discount = min(discount, running)  # never turn a charge negative
            running -= discount
            tracer.add(
                ExecutionStage.DISCOUNT,
                "Discount applied",
                (
                    f"less {_plain(percentage)}% = -{_plain(discount)}"
                    if percentage
                    else f"less {_plain(discount)} fixed discount"
                ),
                running,
                discount_rule,
            )
    outcome.discount = discount

    # --- 8. Surcharge -------------------------------------------------------
    if surcharge_rule is not None:
        surcharge_action = _action_of(surcharge_rule, ActionType.ADD_SURCHARGE.value)
        if surcharge_action:
            s_params = surcharge_action.get("params") or {}
            percentage = _dec(s_params.get("percentage"))
            surcharge = (
                running * percentage / Decimal(100) if percentage else _dec(s_params.get("amount"))
            )
            running += surcharge
            tracer.add(
                ExecutionStage.SURCHARGE, "Surcharge applied",
                f"+{_plain(surcharge)}", running, surcharge_rule,
            )

    # --- 9. Tax -------------------------------------------------------------
    tax = ZERO
    if tax_rule is not None:
        tax_action = _action_of(tax_rule, ActionType.APPLY_TAX.value)
        if tax_action:
            payload = (tax_action.get("resolved") or {}).get("tax_rule") or {}
            rate_percent = _dec(payload.get("rate_percent"))
            if payload.get("inclusive"):
                # Tax-inclusive pricing: the running total already contains it,
                # so extract rather than add — otherwise it is charged twice.
                tax = running - (running / (Decimal(1) + rate_percent / Decimal(100)))
                tracer.add(
                    ExecutionStage.TAX,
                    "Tax extracted (inclusive)",
                    f"{_plain(rate_percent)}% of {_plain(running)} is already "
                    f"included = {_plain(tax)}",
                    running,
                    tax_rule,
                )
            else:
                tax = running * rate_percent / Decimal(100)
                running += tax
                tracer.add(
                    ExecutionStage.TAX,
                    "Tax applied",
                    f"+{_plain(rate_percent)}% = +{_plain(tax)}",
                    running,
                    tax_rule,
                )
    else:
        outcome.missing_stages.append(ExecutionStage.TAX.value)
    outcome.tax = tax

    # --- 10. Rounding -------------------------------------------------------
    decimals = 2
    mode = "HALF_UP"
    if rounding_rule is not None:
        rounding_action = _action_of(rounding_rule, ActionType.APPLY_ROUNDING.value)
        if rounding_action:
            payload = (rounding_action.get("resolved") or {}).get("rounding_rule") or {}
            r_params = rounding_action.get("params") or {}
            mode = str(payload.get("mode") or r_params.get("mode") or mode).upper()
            decimals = int(payload.get("decimals") or r_params.get("decimals") or decimals)

    final = round_money(running, mode, decimals)
    tracer.add(
        ExecutionStage.ROUNDING,
        "Final charge rounded",
        f"{_plain(running)} -> {_plain(final)} ({mode}, {decimals}dp)",
        final,
        rounding_rule,
    )

    outcome.base_charge = round_money(outcome.base_charge, mode, 6)
    outcome.discount = round_money(outcome.discount, mode, 6)
    outcome.tax = round_money(outcome.tax, mode, 6)
    outcome.final_charge = final
    outcome.trace = tracer.steps
    return outcome
