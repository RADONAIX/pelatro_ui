"""The target's OWN vocabulary, and the translation into it.

`canonical_rating` is not an empty scheme waiting for whatever this platform
writes — it has its own reference model: short operator codes (``EQ``, ``GT``),
its own attribute names (``duration_sec``, ``volume_kb``, ``bundle_code``), a
14-action registry with handler names, five rule types and two match
strategies. The mirror's job is to speak that vocabulary, not to replace it
with the platform's.

Two kinds of content live here:

**Reference rows** — the target's lookup tables, verbatim from the operator's
own specification. Seeding writes these, never the platform registries. The
supplemental attribute rows are the one addition: platform attributes with no
target equivalent (``tariff_plan`` above all) are appended as extra ACTIVE rows
rather than silently dropping every rule that conditions on them.

**Translation maps** — platform code → target code, applied on every mirrored
row. A platform value with no entry is passed through (attributes, rule types)
or refuses the rule (actions), depending on which failure is safer; each case
says which and why.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.rules.constants import Operator

# --- Operators ---------------------------------------------------------------

#: Platform operator → target operator_code. EQ and GT are confirmed from the
#: operator's own sample conditions; the remaining short forms follow the same
#: naming scheme. Every code here is seeded into `rule_operator`, so the FK
#: cannot be broken by this map's own output.
OPERATOR_MAP: dict[str, str] = {
    Operator.EQUALS: "EQ",
    Operator.NOT_EQUALS: "NEQ",
    Operator.GREATER_THAN: "GT",
    Operator.LESS_THAN: "LT",
    Operator.GREATER_OR_EQUAL: "GTE",
    Operator.LESS_OR_EQUAL: "LTE",
    Operator.IN: "IN",
    Operator.NOT_IN: "NOT_IN",
    Operator.BETWEEN: "BETWEEN",
    Operator.STARTS_WITH: "STARTS_WITH",
    Operator.CONTAINS: "CONTAINS",
    Operator.EXISTS: "EXISTS",
    Operator.NOT_EXISTS: "NOT_EXISTS",
}

#: Seed rows for `rule_operator` — code, name, description, supported types.
OPERATOR_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("EQ", "Equals", "Left side equals the value", "STRING,INTEGER,DECIMAL,BOOLEAN,TIMESTAMP"),
    ("NEQ", "Not equals", "Left side differs from the value",
     "STRING,INTEGER,DECIMAL,BOOLEAN,TIMESTAMP"),
    ("GT", "Greater than", "Numeric or date comparison", "INTEGER,DECIMAL,TIMESTAMP"),
    ("LT", "Less than", "Numeric or date comparison", "INTEGER,DECIMAL,TIMESTAMP"),
    ("GTE", "Greater than or equal", "Numeric or date comparison", "INTEGER,DECIMAL,TIMESTAMP"),
    ("LTE", "Less than or equal", "Numeric or date comparison", "INTEGER,DECIMAL,TIMESTAMP"),
    ("IN", "In", "Value is one of a set", "STRING,INTEGER,DECIMAL"),
    ("NOT_IN", "Not in", "Value is outside a set", "STRING,INTEGER,DECIMAL"),
    ("BETWEEN", "Between", "Value lies inside an inclusive range",
     "INTEGER,DECIMAL,TIMESTAMP"),
    ("STARTS_WITH", "Starts with", "Text prefix match", "STRING"),
    ("CONTAINS", "Contains", "Text substring match", "STRING"),
    ("EXISTS", "Exists", "The attribute is present", "STRING,INTEGER,DECIMAL,BOOLEAN"),
    ("NOT_EXISTS", "Does not exist", "The attribute is absent",
     "STRING,INTEGER,DECIMAL,BOOLEAN"),
)


# --- Attributes --------------------------------------------------------------

#: Platform attribute key → (target attribute_name, target value_type, scale).
#: `scale` divides numeric comparison values — the one real unit conversion is
#: usage_volume (bytes, per the balance engine's own conversion table) into
#: volume_kb (kilobytes).
#:
#: `time_band` is declared ARRAY in the target's attribute table, but the
#: operator's own sample conditions store it as STRING ("time_band EQ PEAK"),
#: so the sample wins.
ATTRIBUTE_MAP: dict[str, tuple[str, str, Decimal | None]] = {
    "service_type": ("service_type", "STRING", None),
    "destination_zone": ("destination_zone", "STRING", None),
    "destination_type": ("destination_type", "STRING", None),
    "country_code": ("destination_country", "STRING", None),
    "time_band": ("time_band", "STRING", None),
    "day_type": ("day_type", "STRING", None),
    "account_type": ("account_type", "STRING", None),
    "offer": ("offer_code", "STRING", None),
    "customer_segment": ("customer_segment", "STRING", None),
    "apn": ("apn", "STRING", None),
    "bundle": ("bundle_code", "STRING", None),
    "duration_seconds": ("duration_sec", "INTEGER", None),
    "usage_volume": ("volume_kb", "DECIMAL", Decimal(1024)),
    "called_number": ("called_number", "STRING", None),
    "calling_number": ("calling_number", "STRING", None),
    # Target-native names now authorable in the UI directly (2026-07). They map
    # onto themselves with the type the operator's table declares — and NO
    # scale: a rule authored as volume_kb already states kilobytes.
    "destination_country": ("destination_country", "STRING", None),
    "destination_operator": ("destination_operator", "STRING", None),
    "offer_code": ("offer_code", "STRING", None),
    "bundle_code": ("bundle_code", "STRING", None),
    "bundle_remaining": ("bundle_remaining", "DECIMAL", None),
    "tax_country": ("tax_country", "STRING", None),
    "tax_exempt": ("tax_exempt", "BOOLEAN", None),
    "duration_sec": ("duration_sec", "INTEGER", None),
    "volume_kb": ("volume_kb", "DECIMAL", None),
}

#: The operator's own `rule_attribute` rows, verbatim:
#: (attribute_name, description, data_type, source_entity).
TARGET_ATTRIBUTE_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("service_type", "Usage service type such as VOICE, SMS or DATA", "STRING",
     "msc_usage_event"),
    ("destination_zone", "Resolved destination tariff zone", "STRING", "destination_prefix"),
    ("destination_type", "Destination type such as MOBILE or PREMIUM", "STRING",
     "destination_prefix"),
    ("destination_country", "Resolved destination country", "STRING", "destination_prefix"),
    ("destination_operator", "Resolved destination operator", "STRING", "destination_prefix"),
    ("time_band", "Applicable tariff time band", "ARRAY", "time_band"),
    ("day_type", "Weekday, weekend or holiday classification", "STRING", "time_band"),
    ("account_type", "PREPAID, POSTPAID or HYBRID account type", "STRING", "subscriber"),
    ("offer_code", "Active subscriber offer code", "STRING", "subscriber_offer"),
    ("customer_segment", "Subscriber customer segment", "STRING", "subscriber"),
    ("apn", "Access point name for data usage", "STRING", "msc_usage_event"),
    ("bundle_code", "Applicable subscriber bundle code", "STRING", "subscriber_bundle_balance"),
    ("bundle_remaining", "Remaining bundle balance", "DECIMAL", "subscriber_bundle_balance"),
    ("tax_country", "Tax jurisdiction country", "STRING", "subscriber"),
    ("tax_exempt", "Whether the subscriber is tax exempt", "BOOLEAN", "subscriber"),
    ("duration_sec", "Voice event duration in seconds", "INTEGER", "msc_usage_event"),
    ("volume_kb", "Data event volume in kilobytes", "DECIMAL", "msc_usage_event"),
    ("called_number", "Called or destination number", "STRING", "msc_usage_event"),
    ("calling_number", "Calling or originating number", "STRING", "msc_usage_event"),
)

#: Names in the operator's own table — a mapped attribute must land on one.
TARGET_ATTRIBUTE_NAMES: frozenset[str] = frozenset(r[0] for r in TARGET_ATTRIBUTE_ROWS)


def attribute_target(platform_key: str) -> tuple[str, str, Decimal | None]:
    """Target (name, value_type, scale) for a platform attribute.

    A key with no mapping passes through under its own name — those names are
    seeded as supplemental `rule_attribute` rows, so the FK holds. Passing
    through beats skipping: `tariff_plan` has no target equivalent, and it is
    the single most common condition the rule wizard writes.
    """
    mapped = ATTRIBUTE_MAP.get(platform_key)
    if mapped is not None:
        return mapped
    return platform_key, "", None  # value_type resolved from the platform type


# --- Rule types --------------------------------------------------------------

#: Platform rule type → the target's five. A type outside the map passes
#: through verbatim (the column carries no CHECK), logged so the estate's
#: unmapped types are enumerable; inventing a wrong bucket would misclassify
#: the rule where merely being verbatim only leaves it foreign-labelled.
RULE_TYPE_MAP: dict[str, str] = {
    "BASE_TARIFF": "USAGE_RATE",
    "PULSE": "USAGE_RATE",
    "MINIMUM_CHARGE": "USAGE_RATE",
    "QUANTITY": "USAGE_RATE",
    "ZERO_RATE": "USAGE_RATE",
    "DISCOUNT": "PERCENTAGE_DISCOUNT",
    "PROMOTION": "PERCENTAGE_DISCOUNT",
    "BUNDLE": "FREE_UNIT",
    "TAX": "PERCENTAGE_TAX",
}

#: Action codes whose presence makes the rule TIERED_USAGE_RATE regardless of
#: its declared type.
_TIERED_ACTIONS: frozenset[str] = frozenset({"SET_TIERED_RATE"})


def rule_type_target(platform_type: str, action_codes: set[str]) -> str:
    if action_codes & _TIERED_ACTIONS:
        return "TIERED_USAGE_RATE"
    return RULE_TYPE_MAP.get(platform_type, platform_type)


# --- Match strategy ----------------------------------------------------------

#: The target uses exactly two strategies. BEST_MATCH does not exist there:
#: the platform resolves "best" by specificity before priority, which the
#: target model cannot express, so the honest reduction is FIRST_MATCH — the
#: winner-takes-one semantics survive even though the ranking rule does not.
LEGACY_STRATEGY_MAP: dict[str, str] = {
    "EXCLUSIVE": "FIRST_MATCH",
    "OVERRIDE": "FIRST_MATCH",
    "STACKABLE": "ALL_MATCHES",
}


# --- Actions -----------------------------------------------------------------


class UnsupportedAction(Exception):
    """The target's 14-action registry cannot express this platform action."""


#: The operator's `rule_action_type` table, verbatim:
#: (action_type, action_name, rule_stage, handler_name, description).
ACTION_TYPE_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    ("SET_RATE", "Set Usage Rate", "BASE_RATE", "BaseRateHandler",
     "Defines the monetary rate"),
    ("SET_RATING_UNIT", "Set Rating Unit", "BASE_RATE", "RatingUnitHandler",
     "Defines the charging unit such as seconds or KB"),
    ("SET_PULSE", "Set Charging Pulse", "BASE_RATE", "PulseHandler",
     "Defines the charging pulse"),
    ("SET_ROUNDING", "Set Pulse Rounding", "ROUNDING", "RoundingHandler",
     "Defines ceiling, floor or nearest rounding"),
    ("SET_MINIMUM_CHARGE", "Set Minimum Charge", "BASE_RATE", "MinimumChargeHandler",
     "Defines the minimum monetary event charge"),
    ("SET_MAXIMUM_CHARGE", "Set Maximum Charge", "BASE_RATE", "MaximumChargeHandler",
     "Defines the maximum monetary event charge"),
    ("CONSUME_ALLOWANCE", "Consume Allowance", "ALLOWANCE", "AllowanceHandler",
     "Consumes free units from a balance bucket"),
    ("SET_FREE_QUANTITY", "Set Free Quantity", "ALLOWANCE", "FreeQuantityHandler",
     "Defines the maximum free quantity"),
    ("APPLY_PERCENT_DISCOUNT", "Apply Percentage Discount", "DISCOUNT",
     "PercentageDiscountHandler", "Applies a percentage discount"),
    ("APPLY_FIXED_DISCOUNT", "Apply Fixed Discount", "DISCOUNT", "FixedDiscountHandler",
     "Applies a fixed monetary discount"),
    ("APPLY_PERCENT_SURCHARGE", "Apply Percentage Surcharge", "SURCHARGE",
     "PercentageSurchargeHandler", "Applies a percentage surcharge"),
    ("APPLY_FIXED_SURCHARGE", "Apply Fixed Surcharge", "SURCHARGE", "FixedSurchargeHandler",
     "Applies a fixed monetary surcharge"),
    ("APPLY_PERCENT_TAX", "Apply Percentage Tax", "TAX", "PercentageTaxHandler",
     "Applies a percentage tax"),
    ("APPLY_FIXED_TAX", "Apply Fixed Tax", "TAX", "FixedTaxHandler",
     "Applies a fixed monetary tax"),
)

TARGET_ACTION_TYPES: frozenset[str] = frozenset(r[0] for r in ACTION_TYPE_ROWS)


def _num(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _percentage(value: Any) -> str:
    """Plain percentage without insignificant trailing zeroes."""
    text = format(value, "f") if isinstance(value, Decimal) else format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _money(value: Any) -> str:
    """Plain decimal with at least two fractional digits, without rounding."""
    text = format(value, "f") if isinstance(value, Decimal) else format(Decimal(str(value)), "f")
    if "." not in text:
        return f"{text}.00"
    whole, fraction = text.split(".", 1)
    return f"{whole}.{fraction.ljust(2, '0')}"


def _rating_unit_parameter(
    params: dict[str, Any], context: dict[str, Any]
) -> tuple[str, str] | None:
    """Target parameter name/value for a source unit selection.

    The target stores the concrete base-unit block instead of separate `unit`
    and `per_units` rows. For voice, an authored pulse is the charging block;
    for count and volume units the unit's base conversion supplies the block.
    """
    raw_unit = params.get("unit") or context.get("unit")
    if not raw_unit:
        return None
    unit = str(raw_unit).strip().upper()
    if unit in {"SECOND", "MINUTE"}:
        # The target contract stores the charge block in seconds. A selected
        # SECOND/MINUTE unit without a separate pulse means a 60-second block;
        # legacy `per_units` is intentionally ignored and never mirrored.
        seconds = Decimal(60)
        pulse = context.get("pulse_seconds")
        if pulse not in (None, ""):
            seconds = Decimal(str(pulse))
        return "unit_seconds", _num(seconds)
    if unit in {"MESSAGE", "EVENT"}:
        return "unit_count", "1"
    if unit == "BYTE":
        return "unit_bytes", "1"
    if unit == "KILOBYTE":
        return "unit_kb", "1024"
    if unit == "MEGABYTE":
        return "unit_mb", str(1024 * 1024)
    if unit == "GIGABYTE":
        return "unit_gb", str(1024 * 1024 * 1024)
    return None


def _rounding_parameter_name(context: dict[str, Any]) -> str:
    unit = str(context.get("unit") or "").strip().upper()
    if unit in {"BYTE", "KILOBYTE", "MEGABYTE", "GIGABYTE"}:
        return "volume_rounding"
    return "pulse_rounding"


#: Value types for parameters of target-native actions authored in the UI.
#: Anything absent is STRING.
_NATIVE_PARAM_TYPES: dict[str, str] = {
    "rate": "DECIMAL",
    "percentage": "DECIMAL",
    "amount": "DECIMAL",
    "quantity": "DECIMAL",
    "per_units": "INTEGER",
    "decimals": "INTEGER",
    "consume_order": "INTEGER",
    "initial_seconds": "INTEGER",
    "subsequent_seconds": "INTEGER",
}


def translate_action(
    code: str,
    params: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Platform action + params → [(target action_type, param, value, value_type)].

    One platform action can fan out across several target actions — the target
    separates "the monetary rate" (SET_RATE) from "the charging unit"
    (SET_RATING_UNIT), where the platform holds both on one action.

    Raises :class:`UnsupportedAction` when the 14-action registry has no honest
    home for the behaviour. The caller skips the whole rule then: mirroring a
    rule minus one of its actions produces a tariff that matches and then
    charges differently, which is worse than an absence.
    """
    p = {k: v for k, v in params.items() if v is not None and v != ""}
    ctx = context or {}
    out: list[tuple[str, str, str, str]] = []

    if code in ("SET_RATE", "SET_TIERED_RATE"):
        if "rate" in p:
            out.append(("SET_RATE", "rate", _money(p["rate"]), "DECIMAL"))
        if "tiers" in p:
            out.append(("SET_RATE", "tiers", str(p["tiers"]), "STRING"))
        rating_unit = _rating_unit_parameter(p, ctx)
        if rating_unit is not None:
            name, value = rating_unit
            out.append(("SET_RATING_UNIT", name, value, "INTEGER"))
    elif code == "SET_ZERO_CHARGE":
        # "Forces the expected charge to zero" — a zero rate says exactly that.
        out.append(("SET_RATE", "rate", "0.00", "DECIMAL"))
    elif code == "SET_PULSE":
        if "initial_seconds" in p:
            out.append(("SET_PULSE", "pulse_seconds", _num(p["initial_seconds"]), "INTEGER"))
    elif code in ("SET_MINIMUM_CHARGE", "SET_MAXIMUM_CHARGE"):
        if "amount" in p:
            out.append((code, "amount", _num(p["amount"]), "DECIMAL"))
        if "currency" in p:
            out.append((code, "currency", str(p["currency"]), "STRING"))
    elif code == "SET_CONNECTION_FEE":
        # A per-event fixed monetary addition is precisely a fixed surcharge.
        if "amount" in p:
            out.append(("APPLY_FIXED_SURCHARGE", "amount", _num(p["amount"]), "DECIMAL"))
        if "currency" in p:
            out.append(("APPLY_FIXED_SURCHARGE", "currency", str(p["currency"]), "STRING"))
    elif code in ("APPLY_DISCOUNT", "ADD_SURCHARGE"):
        family = "DISCOUNT" if code == "APPLY_DISCOUNT" else "SURCHARGE"
        if "percentage" in p:
            out.append(
                (f"APPLY_PERCENT_{family}", "percentage", _num(p["percentage"]), "DECIMAL")
            )
        elif "amount" in p:
            out.append((f"APPLY_FIXED_{family}", "amount", _num(p["amount"]), "DECIMAL"))
            if "currency" in p:
                out.append((f"APPLY_FIXED_{family}", "currency", str(p["currency"]), "STRING"))
        else:
            # A catalogue reference with neither percentage nor amount cannot be
            # expressed in the target's percent/fixed split.
            raise UnsupportedAction(f"{code} carries neither a percentage nor an amount")
    elif code == "CONSUME_BUNDLE":
        if "bundle" in p:
            out.append(("CONSUME_ALLOWANCE", "balance_type", str(p["bundle"]), "STRING"))
    elif code == "APPLY_TAX":
        percentage = p.get("percentage", p.get("rate_percent"))
        if percentage in (None, "") and "tax_rule" in p:
            tax_code = str(p["tax_rule"])
            tax_rates = ctx.get("tax_rates", {})
            percentage = tax_rates.get(tax_code)
            if percentage in (None, ""):
                percentage = tax_rates.get(tax_code.upper())
            if percentage in (None, ""):
                raise UnsupportedAction(
                    f"APPLY_TAX references tax rule '{tax_code}' without a resolved percentage"
                )
        if percentage in (None, ""):
            raise UnsupportedAction("APPLY_TAX carries neither a percentage nor a tax rule")
        out.append(
            ("APPLY_PERCENT_TAX", "percentage", _percentage(percentage), "DECIMAL")
        )
    elif code == "APPLY_ROUNDING":
        value = p.get("mode", p.get("rounding_rule"))
        if value not in (None, ""):
            out.append(
                ("SET_ROUNDING", _rounding_parameter_name(ctx), str(value), "STRING")
            )
    elif code == "SET_RATING_UNIT":
        rating_unit = _rating_unit_parameter(p, ctx)
        if rating_unit is not None:
            name, value = rating_unit
            out.append(("SET_RATING_UNIT", name, value, "INTEGER"))
    elif code == "SET_ROUNDING":
        value = p.get("mode")
        if value not in (None, ""):
            out.append(
                ("SET_ROUNDING", _rounding_parameter_name(ctx), str(value), "STRING")
            )
    elif code == "CONSUME_ALLOWANCE":
        balance_type = p.get("balance_type", p.get("bundle_code"))
        if balance_type not in (None, ""):
            out.append(("CONSUME_ALLOWANCE", "balance_type", str(balance_type), "STRING"))
    elif code == "SET_FREE_QUANTITY":
        maximum = p.get("maximum_units", p.get("quantity"))
        if maximum not in (None, ""):
            out.append(("SET_FREE_QUANTITY", "maximum_units", _num(maximum), "DECIMAL"))
    elif code in TARGET_ACTION_TYPES:
        # Target-native codes authored in the UI directly (SET_RATING_UNIT,
        # APPLY_PERCENT_DISCOUNT, …): no renaming needed, each parameter simply
        # becomes a row under its own action type.
        for key in sorted(p):
            vtype = _NATIVE_PARAM_TYPES.get(key, "STRING")
            value = _num(p[key]) if vtype in ("DECIMAL", "INTEGER") else str(p[key])
            out.append((code, key, value, vtype))
    else:
        # SELECT_TARIFF, SET_MINIMUM_QUANTITY, APPLY_PROMOTION-by-reference and
        # every canonical-only code: no target action says what these do.
        raise UnsupportedAction(f"'{code}' has no equivalent in the target's action registry")

    if not out:
        raise UnsupportedAction(f"'{code}' produced no mappable parameters")
    return out
