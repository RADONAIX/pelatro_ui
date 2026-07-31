"""Turn one mapped spreadsheet row into a canonical ``RuleCreate`` payload.

Pure and synchronous — no database, no I/O. That keeps it directly testable and
lets the preview endpoint show an author exactly what a row will become before
anything is written.

Reference values (product, zone, tax rule …) are carried through as **codes**;
whether those codes exist is settled later by the structural validator, which
already does that lookup in one batched query per catalogue.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.modules.catalog.constants import ServiceType
from app.modules.imports.constants import (
    ACTION_GROUPS,
    COLUMN_BY_KEY,
    FALSY,
    TRUTHY,
    ColumnKind,
)
from app.modules.rules.constants import (
    ATTRIBUTE_BY_KEY,
    RULE_TYPE_STAGE,
    ActionType,
    DataType,
    Operator,
    RuleType,
)

#: Accepted date formats, most-specific first. ISO wins; the rest cover what
#: Excel and European exports actually emit.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d")

#: Action columns whose cell is a quantity rather than a code or enum member.
_NUMERIC_ACTION_COLUMNS = frozenset({
    "rate", "rate_per_units", "pulse_initial", "pulse_subsequent",
    "min_charge", "max_charge", "connection_fee",
    "discount_percentage", "surcharge_percentage",
})


class RowError(Exception):
    """A row-level problem that stops this row importing, but not the batch."""

    def __init__(self, message: str, *, column: str = ""):
        super().__init__(message)
        self.message = message
        self.column = column


def _parse_date(value: str, column: str) -> date:
    text = value.strip()
    # Excel hands back a full datetime for a date cell.
    if " " in text:
        text = text.split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise RowError(f"'{value}' is not a recognised date (try YYYY-MM-DD).", column=column)


def _parse_bool(value: str, column: str) -> bool:
    upper = value.strip().upper()
    if upper in TRUTHY:
        return True
    if upper in FALSY:
        return False
    raise RowError(f"'{value}' is not a yes/no value.", column=column)


def _parse_number(value: str, column: str) -> float:
    # Tolerate thousands separators and a trailing currency symbol.
    cleaned = value.replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    try:
        return float(cleaned)
    except ValueError as exc:
        raise RowError(f"'{value}' is not a number.", column=column) from exc


def _split_values(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _coerce_condition_values(attribute: str, raw: str) -> tuple[str, list[Any]]:
    """Return ``(operator, values)`` for a condition column's cell."""
    attr = ATTRIBUTE_BY_KEY[attribute]
    parts = _split_values(raw)
    if not parts:
        return Operator.EQUALS.value, []

    if attr.data_type == DataType.BOOLEAN:
        return Operator.EQUALS.value, [_parse_bool(parts[0], attribute)]
    if attr.data_type == DataType.NUMBER:
        return (
            (Operator.EQUALS.value, [_parse_number(parts[0], attribute)])
            if len(parts) == 1
            else (Operator.IN.value, [_parse_number(p, attribute) for p in parts])
        )
    if attr.data_type == DataType.ENUM:
        upper = [p.upper() for p in parts]
        for p in upper:
            if p not in attr.values:
                raise RowError(
                    f"'{p}' is not a valid {attr.label}. Allowed: {', '.join(attr.values)}.",
                    column=attribute,
                )
        return (Operator.EQUALS.value if len(upper) == 1 else Operator.IN.value), upper

    # STRING / REFERENCE / DATETIME — a single value is an equality, a list is
    # set membership. Reference codes are upper-cased to match the catalogue.
    values = [p.upper() for p in parts] if attr.data_type == DataType.REFERENCE else parts
    return (Operator.EQUALS.value if len(values) == 1 else Operator.IN.value), values


def _infer_rule_type(actions: list[dict[str, Any]]) -> str:
    """Pick the rule type from the actions the row produced.

    A tariff sheet rarely carries a `rule_type` column, but the columns it does
    carry say exactly what the row is: a `rate` makes it a base tariff, a lone
    `tax_rule` makes it a tax rule. Getting this right matters because the type
    decides which compiler stage the rule lands in.
    """
    if not actions:
        return RuleType.BASE_TARIFF.value
    types = {a["action_type"] for a in actions}
    for action, rule_type in (
        (ActionType.SET_RATE, RuleType.BASE_TARIFF),
        (ActionType.SET_ZERO_CHARGE, RuleType.ZERO_RATE),
        (ActionType.SET_PULSE, RuleType.PULSE),
        (ActionType.SET_MINIMUM_CHARGE, RuleType.MINIMUM_CHARGE),
        (ActionType.CONSUME_BUNDLE, RuleType.BUNDLE),
        (ActionType.APPLY_PROMOTION, RuleType.PROMOTION),
        (ActionType.APPLY_DISCOUNT, RuleType.DISCOUNT),
        (ActionType.ADD_SURCHARGE, RuleType.SURCHARGE),
        (ActionType.APPLY_TAX, RuleType.TAX),
        (ActionType.APPLY_ROUNDING, RuleType.ROUNDING),
    ):
        if action.value in types:
            return rule_type.value
    return RuleType.BASE_TARIFF.value


def map_row(
    row: dict[str, str],
    mapping: dict[str, str | None],
    *,
    default_effective_from: date,
    source_system: str = "FILE_IMPORT",
) -> dict[str, Any]:
    """Map one raw row to a rule payload. Raises ``RowError`` on a bad cell."""
    # Collapse the file's headings onto canonical keys, dropping unmapped and
    # blank cells so an empty column never produces an empty condition.
    cells: dict[str, str] = {}
    for heading, value in row.items():
        key = mapping.get(heading)
        if key and str(value).strip():
            cells[key] = str(value).strip()

    header: dict[str, Any] = {}
    conditions: list[dict[str, Any]] = []

    for key, value in cells.items():
        column = COLUMN_BY_KEY.get(key)
        if column is None or column.kind != ColumnKind.CONDITION:
            continue
        operator, values = _coerce_condition_values(key, value)
        if values:
            conditions.append(
                {
                    "attribute": key,
                    "operator": operator,
                    "values": values,
                    "group_index": 0,
                    "negate": False,
                }
            )

    # --- Header fields -------------------------------------------------------
    name = cells.get("name", "").strip()
    if not name:
        raise RowError("A rule name is required.", column="name")
    header["name"] = name

    if cells.get("rule_key"):
        header["rule_key"] = cells["rule_key"].strip().upper().replace(" ", "_")

    service = (cells.get("service_type") or "").strip().upper()
    if not service:
        raise RowError("A service type is required.", column="service_type")
    if service not in {s.value for s in ServiceType}:
        raise RowError(
            f"'{service}' is not a valid service type.", column="service_type"
        )
    header["service_type"] = service

    header["description"] = cells.get("description", "")
    header["category"] = cells.get("category", "")
    header["owner"] = cells.get("owner") or None
    header["conflict_group"] = cells.get("conflict_group") or None
    header["source_system"] = source_system

    if cells.get("stacking_policy"):
        header["stacking_policy"] = cells["stacking_policy"].strip().upper()

    header["priority"] = (
        int(_parse_number(cells["priority"], "priority")) if cells.get("priority") else 100
    )

    header["effective_from"] = (
        _parse_date(cells["effective_from"], "effective_from").isoformat()
        if cells.get("effective_from")
        else default_effective_from.isoformat()
    )
    header["effective_to"] = (
        _parse_date(cells["effective_to"], "effective_to").isoformat()
        if cells.get("effective_to")
        else None
    )
    header["currency_code"] = (cells.get("currency_code") or "").upper() or None

    # Product / offer / tariff plan arrive as codes. They are also emitted as
    # conditions: the sheet says "this price applies to PREPAID_A", which is a
    # predicate, not just a label.
    for column_key, attribute in (
        ("product", "product"),
        ("offer", "offer"),
        ("tariff_plan", "tariff_plan"),
    ):
        code = cells.get(column_key)
        if code:
            conditions.append(
                {
                    "attribute": attribute,
                    "operator": Operator.EQUALS.value,
                    "values": [code.upper()],
                    "group_index": 0,
                    "negate": False,
                }
            )

    # --- Actions -------------------------------------------------------------
    actions: list[dict[str, Any]] = []
    for group in ACTION_GROUPS:
        trigger = cells.get(group.trigger)
        if not trigger:
            continue
        if group.action_type == ActionType.SET_ZERO_CHARGE:
            if not _parse_bool(trigger, group.trigger):
                continue
            actions.append({"action_type": group.action_type.value, "params": {}})
            continue

        params: dict[str, Any] = {}
        for param_key, column_key in group.params:
            raw = cells.get(column_key)
            if not raw:
                continue
            if column_key in _NUMERIC_ACTION_COLUMNS:
                params[param_key] = _parse_number(raw, column_key)
            else:
                # Everything else is an enum member or a catalogue code, both of
                # which are upper-case by convention.
                params[param_key] = raw.upper()
        if params:
            actions.append({"action_type": group.action_type.value, "params": params})

    if not actions:
        raise RowError(
            "The row produced no actions — add a rate, pulse, tax or discount column."
        )

    rule_type = (cells.get("rule_type") or "").strip().upper() or _infer_rule_type(actions)
    if rule_type not in RULE_TYPE_STAGE:
        raise RowError(f"'{rule_type}' is not a valid rule type.", column="rule_type")
    header["rule_type"] = rule_type

    header["conditions"] = conditions
    header["actions"] = actions
    return header
