"""The import column catalogue — how a flat tariff sheet becomes a canonical rule.

Operators hand over tariffs as spreadsheets, one row per priced combination:

    name              | product   | destination_zone | time_band | rate | rate_unit | ...
    On-net peak       | PREPAID_A | LOCAL_ONNET      | PEAK      | 0.10 | SECOND    | ...

So the importer treats **one row as one rule** and splits its columns three ways:

* HEADER    → fields on the rule itself (name, priority, effective dates …)
* CONDITION → a predicate, keyed by a real rating attribute
* ACTION    → a charging instruction, assembled from a group of related columns
              (``rate`` + ``rate_unit`` + ``rate_per_units`` → one SET_RATE)

Condition and action column keys are validated against the rule vocabulary at
import time, so this table can never drift from what the engine executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.rules.constants import ATTRIBUTE_BY_KEY, ActionType


class ColumnKind(StrEnum):
    HEADER = "HEADER"
    CONDITION = "CONDITION"
    ACTION = "ACTION"


@dataclass(frozen=True)
class ImportColumn:
    key: str
    label: str
    kind: str
    required: bool = False
    description: str = ""
    #: Other spreadsheet headings that mean the same thing. Matching is
    #: case-insensitive and ignores spaces/underscores, so "Rule Name",
    #: "rule_name" and "RULENAME" all land on `name`.
    aliases: tuple[str, ...] = ()


HEADER_COLUMNS: tuple[ImportColumn, ...] = (
    ImportColumn("name", "Rule name", ColumnKind.HEADER, True,
                 "Required. The rule key is derived from it when not supplied.",
                 ("rule name", "rule_name", "title", "description of rule")),
    ImportColumn("rule_key", "Rule key", ColumnKind.HEADER, False,
                 "Stable identifier. Supply it to re-import the same logical rule.",
                 ("key", "rule id", "rule_id", "code")),
    ImportColumn("description", "Description", ColumnKind.HEADER, False, "",
                 ("notes", "comment", "remarks")),
    ImportColumn("rule_type", "Rule type", ColumnKind.HEADER, False,
                 "Defaults to BASE_TARIFF, or is inferred from the action columns present.",
                 ("type",)),
    ImportColumn("service_type", "Service type", ColumnKind.HEADER, True,
                 "VOICE / SMS / DATA / MMS / ROAMING / DIGITAL.",
                 ("service", "svc")),
    ImportColumn("category", "Category", ColumnKind.HEADER, False, "", ()),
    ImportColumn("priority", "Priority", ColumnKind.HEADER, False,
                 "Defaults to 100. Specificity is computed and breaks ties first.",
                 ("prio", "seq", "sequence")),
    ImportColumn("effective_from", "Effective from", ColumnKind.HEADER, False,
                 "Defaults to today when absent.",
                 ("valid from", "start date", "from date", "start")),
    ImportColumn("effective_to", "Effective to", ColumnKind.HEADER, False,
                 "Blank means open-ended.",
                 ("valid to", "end date", "to date", "end")),
    ImportColumn("currency_code", "Currency", ColumnKind.HEADER, False, "",
                 ("currency", "ccy")),
    ImportColumn("product", "Product", ColumnKind.HEADER, False,
                 "Product CODE. Also emits a product condition.",
                 ("product code", "product_code", "plan", "tariff")),
    ImportColumn("offer", "Offer", ColumnKind.HEADER, False, "Offer CODE.",
                 ("offer code", "offer_code")),
    ImportColumn("tariff_plan", "Tariff plan", ColumnKind.HEADER, False,
                 "Tariff plan CODE.", ("tariff plan code", "plan code")),
    ImportColumn("conflict_group", "Conflict group", ColumnKind.HEADER, False, "", ()),
    ImportColumn("stacking_policy", "Stacking policy", ColumnKind.HEADER, False,
                 "EXCLUSIVE (default), STACKABLE or OVERRIDE.", ()),
    ImportColumn("owner", "Owner", ColumnKind.HEADER, False, "", ()),
)

#: Condition columns are generated from the rule vocabulary itself — every
#: rating attribute is importable, and adding one to `rules/constants.py`
#: makes it importable with no change here.
_CONDITION_ALIASES: dict[str, tuple[str, ...]] = {
    "destination_zone": ("destination", "zone", "dest zone", "destination zone code"),
    "origin_zone": ("origin", "origin zone code"),
    "time_band": ("timeband", "time band code", "peak/off-peak", "band"),
    "roaming": ("roaming flag", "is roaming"),
    "on_net": ("onnet", "on net", "on-net"),
    "account_type": ("account", "subscriber type"),
    "network_type": ("network", "rat"),
    "called_number": ("b number", "b-number", "called", "msisdn b"),
    "calling_number": ("a number", "a-number", "calling", "msisdn a"),
    "rating_group": ("rating group code",),
    "visited_operator": ("plmn", "visited plmn", "partner"),
}


def _condition_columns() -> tuple[ImportColumn, ...]:
    out: list[ImportColumn] = []
    for attr in ATTRIBUTE_BY_KEY.values():
        # `service_type` and `currency` are carried as header fields instead —
        # importing them twice would produce a redundant condition on every row.
        if attr.key in {"service_type", "currency", "product", "offer", "tariff_plan"}:
            continue
        out.append(
            ImportColumn(
                key=attr.key,
                label=attr.label,
                kind=ColumnKind.CONDITION,
                description=(
                    f"{attr.description or attr.label}. One value → EQUALS; "
                    "comma-separated → IN."
                ),
                aliases=_CONDITION_ALIASES.get(attr.key, ()),
            )
        )
    return tuple(out)


CONDITION_COLUMNS: tuple[ImportColumn, ...] = _condition_columns()


@dataclass(frozen=True)
class ActionColumnGroup:
    """A set of columns that together produce one rule action."""

    action_type: str
    #: action param key -> import column key
    params: tuple[tuple[str, str], ...]
    #: The column whose presence triggers the action. Without it the group is skipped.
    trigger: str


ACTION_GROUPS: tuple[ActionColumnGroup, ...] = (
    ActionColumnGroup(
        ActionType.SET_RATE,
        (("rate", "rate"), ("unit", "rate_unit"), ("per_units", "rate_per_units"),
         ("currency", "currency_code")),
        trigger="rate",
    ),
    ActionColumnGroup(
        ActionType.SET_PULSE,
        (("initial_seconds", "pulse_initial"), ("subsequent_seconds", "pulse_subsequent")),
        trigger="pulse_initial",
    ),
    ActionColumnGroup(
        ActionType.SET_MINIMUM_CHARGE,
        (("amount", "min_charge"), ("currency", "currency_code")),
        trigger="min_charge",
    ),
    ActionColumnGroup(
        ActionType.SET_MAXIMUM_CHARGE,
        (("amount", "max_charge"), ("currency", "currency_code")),
        trigger="max_charge",
    ),
    ActionColumnGroup(
        ActionType.SET_CONNECTION_FEE,
        (("amount", "connection_fee"), ("currency", "currency_code")),
        trigger="connection_fee",
    ),
    ActionColumnGroup(
        ActionType.APPLY_DISCOUNT,
        (("percentage", "discount_percentage"),),
        trigger="discount_percentage",
    ),
    ActionColumnGroup(
        ActionType.APPLY_DISCOUNT,
        (("discount", "discount_code"),),
        trigger="discount_code",
    ),
    ActionColumnGroup(
        ActionType.APPLY_TAX, (("tax_rule", "tax_rule"),), trigger="tax_rule",
    ),
    ActionColumnGroup(
        ActionType.APPLY_ROUNDING, (("rounding_rule", "rounding_rule"),),
        trigger="rounding_rule",
    ),
    ActionColumnGroup(
        ActionType.CONSUME_BUNDLE, (("bundle", "bundle"),), trigger="bundle",
    ),
    ActionColumnGroup(
        ActionType.APPLY_PROMOTION, (("promotion", "promotion"),), trigger="promotion",
    ),
    ActionColumnGroup(
        ActionType.ADD_SURCHARGE, (("percentage", "surcharge_percentage"),),
        trigger="surcharge_percentage",
    ),
    ActionColumnGroup(
        ActionType.SET_ZERO_CHARGE, (), trigger="zero_charge",
    ),
)

_ACTION_COLUMN_META: dict[str, tuple[str, str]] = {
    "rate": ("Rate", "Charge per `rate_per_units` of `rate_unit`."),
    "rate_unit": ("Rate unit", "SECOND / MINUTE / MESSAGE / BYTE / KILOBYTE / MEGABYTE / EVENT."),
    "rate_per_units": ("Rate per units", "Defaults to 1. e.g. 0.10 per 60 SECOND."),
    "pulse_initial": ("Initial pulse", "First chargeable block, in seconds."),
    "pulse_subsequent": ("Subsequent pulse", "Block size after the first."),
    "min_charge": ("Minimum charge", "Floor applied to the calculated charge."),
    "max_charge": ("Maximum charge", "Cap applied to the calculated charge."),
    "connection_fee": ("Connection fee", "One-off setup charge per call."),
    "discount_percentage": ("Discount %", "0 to 100."),
    "discount_code": ("Discount code", "A discount from the catalogue."),
    "tax_rule": ("Tax rule", "A tax rule CODE from the catalogue."),
    "rounding_rule": ("Rounding rule", "A rounding rule CODE from the catalogue."),
    "bundle": ("Bundle", "A bundle CODE to consume from."),
    "promotion": ("Promotion", "A promotion CODE."),
    "surcharge_percentage": ("Surcharge %", ""),
    "zero_charge": ("Zero charge", "Any truthy value (Y/YES/TRUE/1) forces a zero charge."),
}

_ACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "rate": ("price", "tariff rate", "charge", "amount per unit"),
    "rate_unit": ("unit", "charge unit", "uom"),
    "rate_per_units": ("per", "per unit", "block size"),
    "pulse_initial": ("pulse", "initial pulse", "first pulse"),
    "pulse_subsequent": ("next pulse", "subsequent pulse"),
    "min_charge": ("minimum charge", "min call charge", "mcc"),
    "tax_rule": ("tax", "tax code", "vat"),
    "discount_percentage": ("discount", "discount pct", "disc %"),
}


def _action_columns() -> tuple[ImportColumn, ...]:
    seen: dict[str, ImportColumn] = {}
    for group in ACTION_GROUPS:
        for _, column_key in group.params:
            if column_key in seen or column_key == "currency_code":
                continue  # currency_code is a header column, reused by actions
            label, desc = _ACTION_COLUMN_META.get(column_key, (column_key, ""))
            seen[column_key] = ImportColumn(
                column_key, label, ColumnKind.ACTION, False, desc,
                _ACTION_ALIASES.get(column_key, ()),
            )
        if group.trigger not in seen and group.trigger != "currency_code":
            label, desc = _ACTION_COLUMN_META.get(group.trigger, (group.trigger, ""))
            seen[group.trigger] = ImportColumn(
                group.trigger, label, ColumnKind.ACTION, False, desc,
                _ACTION_ALIASES.get(group.trigger, ()),
            )
    return tuple(seen.values())


ACTION_COLUMNS: tuple[ImportColumn, ...] = _action_columns()

ALL_COLUMNS: tuple[ImportColumn, ...] = HEADER_COLUMNS + CONDITION_COLUMNS + ACTION_COLUMNS
COLUMN_BY_KEY: dict[str, ImportColumn] = {c.key: c for c in ALL_COLUMNS}


def _normalise(heading: str) -> str:
    return "".join(ch for ch in heading.lower() if ch.isalnum())


#: Normalised heading -> canonical column key, for auto-mapping an uploaded file.
_LOOKUP: dict[str, str] = {}
for _col in ALL_COLUMNS:
    _LOOKUP[_normalise(_col.key)] = _col.key
    _LOOKUP[_normalise(_col.label)] = _col.key
    for _alias in _col.aliases:
        _LOOKUP.setdefault(_normalise(_alias), _col.key)


def suggest_mapping(headings: list[str]) -> dict[str, str | None]:
    """Best-guess mapping of a file's headings onto canonical column keys.

    Returns every heading, with ``None`` where nothing matched, so the UI can
    show unmapped columns rather than silently dropping them.
    """
    return {h: _LOOKUP.get(_normalise(h)) for h in headings}


class ImportStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RowStatus(StrEnum):
    VALID = "VALID"
    IMPORTED = "IMPORTED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


TRUTHY = {"Y", "YES", "TRUE", "T", "1", "X"}
FALSY = {"N", "NO", "FALSE", "F", "0", ""}
