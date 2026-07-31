"""A mapped spreadsheet row → :class:`CanonicalDraft`.

Every tariff a vendor sends as a file — CSV, XLSX, a flattened JSON array, an XML
export — arrives here as a dict of headings to strings, plus a mapping from those
headings onto canonical column keys. The column registry itself is *reused* from
``imports/constants.py`` rather than restated: those column definitions, their
aliases and the mapping suggester are correct and in production, and a second
copy would drift within a sprint.

What is **not** reused is ``imports/mapper.py``. It produces the legacy payload,
and it does two things this adapter must not:

**It parses money to ``float``.** ``_parse_number`` returns a Python float, so a
rate of 0.012345 is already inexact before it reaches storage. Here every value
carries its declared type and money is a ``Decimal`` the whole way.

**It infers a rule type from actions and stops there.** The canonical model also
needs a *charging mode*, which no legacy column carries. It is read from an
explicit column where the vendor sends one, then from an ``account_type``
condition, and defaults to ``BOTH`` — the only value that cannot silently narrow
which subscribers a rule applies to.

Aliases are applied on the way in and recorded, so "our file said SET_MINIMUM"
has an answer in the lineage tab rather than an argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.imports.constants import (
    ACTION_GROUPS,
    COLUMN_BY_KEY,
    ColumnKind,
)
from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftTargets,
    DraftValidity,
    FieldProvenance,
    Provenance,
)
from app.modules.rules.constants import ATTRIBUTE_BY_KEY, DataType, Operator
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.aliases import (
    resolve_action,
    resolve_attribute,
    resolve_charging_mode,
    resolve_rule_type,
    resolve_service_type,
)
from app.modules.rules.vocabulary.modes import ChargingMode
from app.modules.rules.vocabulary.types import RULE_TYPE_BY_CODE
from app.modules.rules.vocabulary.values import ValueType

#: Columns the canonical model reads that the legacy registry has no entry for.
#: Accepted under any of these headings, matched the same way the registry
#: matches its own — case-insensitive, ignoring spaces and underscores.
EXTRA_COLUMNS: dict[str, tuple[str, ...]] = {
    "charging_mode": ("charging mode", "chargingmode", "mode", "payment type",
                      "paymenttype", "account type", "accounttype"),
    "execution_mode": ("execution mode", "executionmode", "runtime"),
    "external_ref": ("external ref", "externalref", "external id", "externalid",
                     "vendor id", "vendorid", "source id", "sourceid"),
    "external_version": ("external version", "externalversion", "vendor version"),
}

#: Date formats seen in the wild, most specific first. ISO is tried first because
#: an ambiguous 03/04/2026 should be read as ISO when it can be.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y",
)

_TRUTHY: frozenset[str] = frozenset({"TRUE", "YES", "Y", "1", "T"})
_FALSY: frozenset[str] = frozenset({"FALSE", "NO", "N", "0", "F"})


class RowError(Exception):
    """A row that cannot become a rule. Carries the column so the reject report
    points at a cell rather than at a row number."""

    def __init__(self, message: str, *, column: str = ""):
        super().__init__(message)
        self.message = message
        self.column = column


@dataclass(slots=True)
class Conversion:
    draft: CanonicalDraft
    notes: list[str] = field(default_factory=list)


def normalise_heading(heading: str) -> str:
    return "".join(ch for ch in heading.lower() if ch.isalnum())


def extend_mapping(
    headings: list[str], mapping: dict[str, str | None]
) -> dict[str, str | None]:
    """Add the canonical-only columns to a legacy-suggested mapping.

    Kept separate from ``suggest_mapping`` so the legacy importer's behaviour is
    untouched: it must go on producing exactly the mapping it produces today.
    """
    resolved = dict(mapping)
    lookup = {
        alias: key
        for table in (EXTRA_COLUMNS, CANONICAL_ACTION_ALIASES)
        for key, aliases in table.items()
        for alias in aliases
    }
    lookup.update({key: key for key in EXTRA_COLUMNS})
    lookup.update({key: key for key in CANONICAL_ACTION_ALIASES})
    lookup.update(
        {normalise_heading(key): key for key in CANONICAL_ACTION_ALIASES}
    )
    lookup.update({normalise_heading(a): k
                   for k, aliases in CANONICAL_ACTION_ALIASES.items()
                   for a in aliases})
    for heading in headings:
        if resolved.get(heading):
            continue
        match = lookup.get(normalise_heading(heading)) or lookup.get(heading.lower())
        if match:
            resolved[heading] = match
    return resolved


def convert(
    row: dict[str, Any],
    mapping: dict[str, str | None],
    *,
    default_effective_from: date,
    source_system_code: str | None = None,
    default_charging_mode: str | None = None,
    default_currency: str | None = None,
) -> Conversion:
    """One mapped row → one canonical draft. Raises :class:`RowError` on a bad cell."""
    notes: list[str] = []
    aliases: dict[str, str] = {}
    provenance_fields: dict[str, FieldProvenance] = {}

    cells: dict[str, str] = {}
    unmapped: dict[str, Any] = {}
    for heading, value in row.items():
        text = "" if value is None else str(value).strip()
        key = mapping.get(heading)
        if key and text:
            cells[key] = text
            provenance_fields[key] = FieldProvenance(heading, value, "cell")
        elif not key and text:
            # Never silently dropped. Surfaced in the UI as "3 unmapped fields on
            # this rule", so a vendor column we do not model yet is visible.
            unmapped[heading] = value

    name = cells.get("name", "")
    if not name:
        raise RowError("A rule name is required.", column="name")

    service_type = cells.get("service_type", "").upper()
    if not service_type:
        raise RowError("A service type is required.", column="service_type")
    # A vendor writing "ALL" means our "ANY". Rejecting it would make an operator
    # hand-edit an export to satisfy a spelling preference of ours.
    service_type, service_alias = resolve_service_type(service_type)
    if service_alias:
        aliases[f"service_type:{service_alias}"] = str(service_type)
        notes.append(f"Service type '{service_alias}' read as '{service_type}'.")

    charging_mode = _charging_mode(cells, default_charging_mode, aliases, notes)
    currency = (cells.get("currency_code") or default_currency or "").upper() or None

    conditions = _conditions(cells, aliases)
    actions = _actions(cells, currency)
    if not actions:
        raise RowError(
            "The row produced no actions — add a rate, pulse, tax or discount "
            "column. Without one the rule matches events and changes nothing."
        )

    rule_type = _rule_type(cells, actions, charging_mode, aliases, notes)

    return Conversion(
        draft=CanonicalDraft(
            rule_key=cells.get("rule_key") or None,
            rule_name=name,
            description=cells.get("description", ""),
            charging_mode=charging_mode,
            rule_type_code=rule_type,
            service_type=str(service_type),
            root_group=DraftConditionGroup(logic="AND", conditions=tuple(conditions)),
            actions=tuple(actions),
            behaviour=DraftBehaviour(
                priority=_int(cells.get("priority"), "priority", default=100),
                stacking_policy=(cells.get("stacking_policy") or "EXCLUSIVE").upper(),
                conflict_group=cells.get("conflict_group") or None,
                execution_mode=(cells.get("execution_mode") or "BOTH").upper(),
            ),
            targets=DraftTargets(
                product=_code(cells.get("product")),
                offer=_code(cells.get("offer")),
                tariff_plan=_code(cells.get("tariff_plan")),
            ),
            validity=DraftValidity(
                effective_from=_date(
                    cells.get("effective_from"), "effective_from", default_effective_from
                ),
                effective_to=(
                    _date(cells["effective_to"], "effective_to", None)
                    if cells.get("effective_to")
                    else None
                ),
                currency_code=currency,
            ),
            provenance=Provenance(
                channel="FILE",
                source_system_code=source_system_code,
                external_ref=cells.get("external_ref"),
                external_version=cells.get("external_version"),
                fields=provenance_fields,
                applied_aliases=aliases,
                unmapped=unmapped,
            ),
            owner=cells.get("owner") or None,
        ),
        notes=notes,
    )


# --- Charging mode and rule type --------------------------------------------


def _charging_mode(
    cells: dict[str, str],
    default: str | None,
    aliases: dict[str, str],
    notes: list[str],
) -> str:
    """Explicit column, then an account_type condition, then BOTH.

    ``BOTH`` last and never a guess: a rule wrongly marked PREPAID stops applying
    to half the estate, and nothing in the file would say so.
    """
    raw = cells.get("charging_mode") or cells.get("account_type")
    if raw:
        resolved, alias = resolve_charging_mode(raw)
        if alias:
            aliases[f"charging_mode:{alias}"] = str(resolved)
        if resolved in set(ChargingMode):
            return str(resolved)
        raise RowError(
            f"'{raw}' is not a charging mode. Use PREPAID, POSTPAID or BOTH.",
            column="charging_mode",
        )
    if default:
        return default
    notes.append("No charging mode in the file; defaulted to BOTH.")
    return ChargingMode.BOTH


def _rule_type(
    cells: dict[str, str],
    actions: list[DraftAction],
    charging_mode: str,
    aliases: dict[str, str],
    notes: list[str],
) -> str:
    stated = (cells.get("rule_type") or "").strip().upper()
    if stated:
        resolved, alias = resolve_rule_type(stated)
        if alias:
            aliases[f"rule_type:{alias}"] = str(resolved)
        if resolved not in RULE_TYPE_BY_CODE:
            raise RowError(f"'{stated}' is not a rule type.", column="rule_type")
        return str(resolved)

    # Inferred from what the row *does*. A tariff sheet rarely carries a type
    # column, and the action's declared stage is exactly the information needed
    # to work out which kind of rule this is.
    inferred = _infer_type(actions, charging_mode)
    notes.append(f"Rule type inferred as {inferred} from the row's actions.")
    return inferred


#: Actions that adjust a charge somebody else produced. A row carrying one of
#: these *and* a charge-producing action is a tariff row that also states tax or
#: rounding — it is not a tax rule. Without this distinction a rental row with a
#: rounding column infers ROUNDING, because rounding sits at an earlier stage.
_MODIFIER_ACTIONS: frozenset[str] = frozenset(
    {"APPLY_TAX", "APPLY_ROUNDING", "APPLY_DISCOUNT", "ADD_SURCHARGE",
     "APPLY_PROMOTION", "SET_MINIMUM_CHARGE", "SET_MAXIMUM_CHARGE",
     "SET_CONNECTION_FEE", "APPLY_PRORATION", "SET_PULSE"}
)


def _infer_type(actions: list[DraftAction], charging_mode: str) -> str:
    """Pick the type from what the row principally *does*.

    Charge-producing actions decide; modifiers only decide when there is nothing
    else, which is the case for a genuine tax or rounding rule. Among the
    remaining candidates the earliest stage wins, so a row with a rate and a
    bundle is a tariff rather than a bundle rule.
    """
    principal = [
        a for a in actions if a.action_type not in _MODIFIER_ACTIONS
    ] or actions
    stages = [
        (spec.stage_code, action.action_type)
        for action in principal
        if (spec := ACTION_BY_CODE.get(action.action_type))
    ]
    if not stages:
        raise RowError("The row's actions are not recognised.")

    from app.modules.rules.vocabulary.stages import STAGE_BY_CODE

    stages.sort(key=lambda pair: STAGE_BY_CODE[pair[0]].execution_order)
    stage_code, action_type = stages[0]

    candidates = [
        spec
        for spec in RULE_TYPE_BY_CODE.values()
        if spec.stage_code == stage_code
        and action_type in spec.required_action_types
        and spec.charging_mode in (ChargingMode.BOTH, charging_mode)
        and spec.alias_of is None
    ]
    if not candidates:
        from app.modules.rules.vocabulary.stages import STAGE_BY_CODE

        owner = STAGE_BY_CODE[stage_code].applies_to
        raise RowError(
            f"'{action_type}' runs at the {stage_code} stage, which only "
            f"{owner.lower()} rules reach — this row is {charging_mode}. "
            + (
                f"Set the Charging Mode column to {owner}."
                if owner != "COMMON"
                else "Check the Charging Mode column."
            ),
            column="charging_mode",
        )
    return candidates[0].code


# --- Conditions -------------------------------------------------------------


def _conditions(cells: dict[str, str], aliases: dict[str, str]) -> list[DraftCondition]:
    """Every mapped condition column becomes one predicate.

    A cell with several comma-separated values becomes ``IN`` rather than a
    string containing commas — which is what the legacy mapper does too, and is
    the behaviour every tariff sheet assumes.
    """
    out: list[DraftCondition] = []
    for key, raw in cells.items():
        column = COLUMN_BY_KEY.get(key)
        if column is None or column.kind != ColumnKind.CONDITION:
            continue
        attribute, alias = resolve_attribute(key)
        if alias:
            aliases[f"attribute:{alias}"] = str(attribute)
        attr = ATTRIBUTE_BY_KEY.get(str(attribute))
        if attr is None:
            continue

        operator, values = _condition_values(attr.data_type, raw)
        if values:
            out.append(
                DraftCondition(
                    attribute=str(attribute),
                    operator=operator,
                    values=tuple(values),
                    sequence=len(out),
                )
            )

    # The header's product / offer / plan are predicates too: the sheet saying
    # "this price applies to PREPAID_A" is a condition, not a label. They are
    # also set as targets, which is what the catalogue column reads.
    for key, attribute in (("product", "product"), ("offer", "offer"),
                           ("tariff_plan", "tariff_plan")):
        code = _code(cells.get(key))
        if code:
            out.append(
                DraftCondition(
                    attribute=attribute,
                    operator=Operator.EQUALS.value,
                    values=(code,),
                    sequence=len(out),
                )
            )
    return out


def _condition_values(data_type: str, raw: str) -> tuple[str, list[Any]]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > 1:
        return Operator.IN.value, [_scalar(data_type, p) for p in parts]
    return Operator.EQUALS.value, [_scalar(data_type, raw.strip())]


def _scalar(data_type: str, raw: str) -> Any:
    if data_type == DataType.BOOLEAN:
        upper = raw.upper()
        if upper in _TRUTHY:
            return True
        if upper in _FALSY:
            return False
        return raw
    if data_type == DataType.NUMBER:
        return _decimal(raw, "condition")
    if data_type in (DataType.ENUM, DataType.REFERENCE):
        return raw.upper()
    return raw


# --- Actions ----------------------------------------------------------------

#: Column keys whose value is money. Everything here becomes a ``Decimal`` with a
#: currency; a monetary column without one is rejected by the codec rather than
#: stored as a bare number nobody can interpret later.
_MONEY_COLUMNS: frozenset[str] = frozenset(
    {"rate", "min_charge", "max_charge", "connection_fee", "surcharge_amount",
     "discount_amount", "promotion_amount", "recurring_amount", "one_time_amount"}
)
#: Column keys whose value is a plain number.
_NUMBER_COLUMNS: frozenset[str] = frozenset(
    {"rate_per_units", "pulse_initial", "pulse_subsequent", "discount_percentage",
     "surcharge_percentage", "tax_rate", "rounding_decimals", "min_quantity",
     "bundle_units", "promotion_percentage"}
)


#: Action columns the canonical model understands and the legacy registry has no
#: entry for. Kept here rather than added to ``imports.constants.ACTION_GROUPS``
#: because that registry is keyed by the legacy ``ActionType`` enum, and widening
#: the enum would change what the legacy importer and validator accept — a change
#: to a working feature, in service of a different one.
#:
#: Each is ``(action_code, ((param_key, column_key), ...), trigger_column)``, the
#: same shape as the legacy groups so one loop reads both.
CANONICAL_ACTION_COLUMNS: tuple[tuple[str, tuple[tuple[str, str], ...], str], ...] = (
    # Rounding stated as mode + decimals rather than as a catalogue rule. The
    # legacy group only accepts `rounding_rule`, so a sheet saying "HALF_UP, 2"
    # produced no rounding action at all.
    ("APPLY_ROUNDING",
     (("mode", "rounding_mode"), ("decimals", "rounding_decimals")),
     "rounding_mode"),
    # Scale alone, with no mode. Mapped so the row reaches validation and is told
    # exactly what is missing, rather than being rejected as "produced no
    # actions" — which sends an operator looking for a rate column they never
    # meant to supply. The mode is *not* defaulted: HALF_UP versus HALF_EVEN is a
    # penny per invoice, and a silent default here is the kind of difference this
    # product exists to detect rather than create.
    ("APPLY_ROUNDING", (("decimals", "rounding_decimals"),), "rounding_decimals"),
    # Pulse by profile, for the estates that keep 60/60 in one place.
    ("SET_PULSE", (("pulse_profile", "pulse_profile"),), "pulse_profile"),
    # A single pulse column means the same figure both ways — a sheet saying
    # "pulse 60" means 60/60, which is what the spec's `subsequent_seconds`
    # default already does.
    ("SET_PULSE", (("initial_seconds", "pulse_seconds"),), "pulse_seconds"),
    # Tax stated as a rate rather than as a catalogue rule.
    ("APPLY_TAX", (("rate_percent", "tax_percentage"),), "tax_percentage"),
    # Postpaid, none of which the legacy vocabulary can express at all.
    ("ADD_RECURRING_CHARGE",
     (("recurring_charge", "recurring_charge"), ("amount", "recurring_amount"),
      ("currency", "currency_code"), ("advance_flag", "recurring_in_advance"),
      ("invoice_component", "invoice_component")),
     "recurring_charge"),
    ("ADD_RECURRING_CHARGE",
     (("amount", "recurring_amount"), ("currency", "currency_code"),
      ("advance_flag", "recurring_in_advance"),
      ("invoice_component", "invoice_component")),
     "recurring_amount"),
    ("ADD_ONE_TIME_CHARGE",
     (("one_time_charge", "one_time_charge"), ("amount", "one_time_amount"),
      ("currency", "currency_code"), ("trigger_event", "one_time_trigger"),
      ("invoice_component", "invoice_component")),
     "one_time_charge"),
    ("ADD_ONE_TIME_CHARGE",
     (("amount", "one_time_amount"), ("currency", "currency_code"),
      ("trigger_event", "one_time_trigger")),
     "one_time_amount"),
    # Prepaid.
    ("DEDUCT_BALANCE",
     (("balance_type", "balance_type"), ("amount_source", "deduct_from"),
      ("unit", "balance_unit"), ("allow_partial", "allow_partial")),
     "balance_type"),
)

#: Headings for the canonical-only action columns, matched the same way the
#: legacy registry matches its own.
CANONICAL_ACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "rounding_mode": ("rounding mode", "round mode", "roundingmode"),
    "rounding_decimals": ("rounding decimals", "decimals", "round decimals",
                          "rounding scale", "roundingscale", "scale"),
    "pulse_seconds": ("pulse seconds", "pulseseconds", "pulse", "pulse size"),
    "tax_percentage": ("tax percentage", "taxpercentage", "tax rate", "tax %",
                       "vat", "vat percentage", "vat rate"),
    "pulse_profile": ("pulse profile", "pulseprofile"),
    "recurring_charge": ("recurring charge", "rental", "monthly rental",
                         "recurringcharge"),
    "recurring_amount": ("recurring amount", "rental amount", "monthly amount",
                         "rental"),
    "recurring_in_advance": ("in advance", "advance", "bill in advance"),
    "one_time_charge": ("one time charge", "onetimecharge", "otc"),
    "one_time_amount": ("one time amount", "otc amount"),
    "one_time_trigger": ("trigger event", "one time trigger", "otc trigger"),
    "invoice_component": ("invoice component", "invoicecomponent", "gl component"),
    "balance_type": ("balance type", "balancetype"),
    "deduct_from": ("deduct from", "amount source", "deduct source"),
    "balance_unit": ("balance unit", "deduct unit"),
    "allow_partial": ("allow partial", "partial allowed", "allow partial usage"),
}


def _actions(cells: dict[str, str], currency: str | None) -> list[DraftAction]:
    out: list[DraftAction] = []
    for code, params, trigger in _all_action_groups():
        raw_trigger = cells.get(trigger)
        if not raw_trigger:
            continue

        code, _alias = resolve_action(str(code))
        spec = ACTION_BY_CODE.get(str(code))
        if spec is None:
            continue

        # A boolean trigger with no parameters — "zero rate this row: yes".
        if not params:
            if str(raw_trigger).upper() in _FALSY:
                continue
            out.append(DraftAction(action_type=str(code), sequence=len(out)))
            continue

        declared = {p.key: p for p in spec.params}
        parameters: list[DraftParameter] = []
        for param_key, column_key in params:
            raw = cells.get(column_key)
            if not raw:
                continue
            parameters.append(
                _parameter(param_key, column_key, raw, declared, currency)
            )
        if parameters and not any(a.action_type == str(code) for a in out):
            out.append(
                DraftAction(
                    action_type=str(code),
                    parameters=tuple(parameters),
                    sequence=len(out),
                )
            )
    return out


def _all_action_groups() -> list[tuple[str, tuple[tuple[str, str], ...], str]]:
    """The legacy groups first, then the canonical-only ones.

    Order matters for the duplicate guard above: a sheet carrying both a
    catalogue rounding rule and a rounding mode should produce one rounding
    action, built from the more specific of the two, and the legacy group is the
    more specific because it names an entity rather than restating its contents.
    """
    return [
        (str(g.action_type), tuple(g.params), g.trigger) for g in ACTION_GROUPS
    ] + list(CANONICAL_ACTION_COLUMNS)


def _parameter(
    param_key: str,
    column_key: str,
    raw: str,
    declared: dict[str, Any],
    currency: str | None,
) -> DraftParameter:
    spec = declared.get(param_key)
    value_type = getattr(spec, "value_type", None)

    if value_type is None:
        value_type = (
            ValueType.MONEY
            if column_key in _MONEY_COLUMNS
            else ValueType.NUMBER
            if column_key in _NUMBER_COLUMNS
            else ValueType.STRING
        )

    if value_type == ValueType.MONEY:
        return DraftParameter(
            param_key, _decimal(raw, column_key), ValueType.MONEY, currency=currency
        )
    if value_type == ValueType.NUMBER:
        return DraftParameter(param_key, _decimal(raw, column_key), ValueType.NUMBER)
    if value_type == ValueType.BOOLEAN:
        return DraftParameter(param_key, raw.upper() in _TRUTHY, ValueType.BOOLEAN)
    if value_type in (ValueType.ENUM, ValueType.REFERENCE):
        return DraftParameter(param_key, raw.upper(), value_type)
    return DraftParameter(param_key, raw, value_type)


# --- Cell parsing -----------------------------------------------------------


def _decimal(raw: str, column: str) -> Decimal:
    """Never ``float``. A rate that becomes a float on the way in is a rate that
    was damaged before anything else got a chance to be careful with it."""
    text = str(raw).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RowError(f"'{raw}' is not a number.", column=column) from exc


def _int(raw: str | None, column: str, *, default: int) -> int:
    if not raw:
        return default
    try:
        return int(_decimal(raw, column))
    except RowError:
        raise
    except (ValueError, TypeError) as exc:
        raise RowError(f"'{raw}' is not a whole number.", column=column) from exc


def _date(raw: str | None, column: str, default: date | None) -> date:
    if not raw:
        if default is None:
            raise RowError("A date is required.", column=column)
        return default
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RowError(
            f"'{raw}' is not a date this system recognises. Use YYYY-MM-DD.",
            column=column,
        ) from exc


def _code(raw: str | None) -> str | None:
    return raw.strip().upper() if raw and raw.strip() else None
