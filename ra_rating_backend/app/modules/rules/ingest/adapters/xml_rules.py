"""Structured rule XML → :class:`CanonicalDraft`, without going through a table.

A tariff XML export is not a spreadsheet with angle brackets. Its whole point is
that a rule *contains* a list of conditions and a list of actions, and flattening
that into `parent.child` columns does not merely lose the structure — it loses
data. The generic flattener writes every repeated sibling to the same key, so:

    <Conditions>
      <Condition><Field>destination_zone</Field>…</Condition>
      <Condition><Field>time_band</Field>…</Condition>
    </Conditions>

arrives as one column, `Conditions.Condition.Field`, holding `time_band` — the
first condition is gone, silently, and the resulting rule prices traffic the
author never intended it to. The same happens to actions, which is why a
structured export produces "the row produced no actions" for every rule: the
column the action landed in is not one the tabular mapper knows.

So this adapter reads the tree as a tree. It is deliberately tolerant about
*names* — every vendor calls the container something different — and strict about
*shape*: a `<Condition>` must yield an attribute, an operator and a value, or it
is a rejection with the rule and element named.

Values still go through the canonical codec, so a rate in an XML file is the same
``Decimal`` as a rate in a CSV. There is one type system, not one per format.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.errors import ValidationFailedError
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
from app.modules.rules.constants import ATTRIBUTE_BY_KEY, Operator
from app.modules.rules.ingest.adapters import tabular
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.aliases import (
    resolve_action,
    resolve_attribute,
    resolve_charging_mode,
    resolve_rule_type,
)
from app.modules.rules.vocabulary.modes import ChargingMode
from app.modules.rules.vocabulary.types import RULE_TYPE_BY_CODE
from app.modules.rules.vocabulary.values import ValueType

#: Element names that hold the list of conditions. Tolerant because the name is
#: the one thing every vendor spells differently and the one thing that carries
#: no meaning — what matters is what is inside it.
_CONDITION_CONTAINERS: frozenset[str] = frozenset(
    {"conditions", "conditionlist", "predicates", "criteria", "criterias",
     "matchcriteria", "filters", "when"}
)
_CONDITION_ELEMENTS: frozenset[str] = frozenset(
    {"condition", "predicate", "criterion", "criteria", "filter", "match"}
)
_ACTION_CONTAINERS: frozenset[str] = frozenset(
    {"actions", "actionlist", "effects", "outcomes", "then", "charges"}
)
_ACTION_ELEMENTS: frozenset[str] = frozenset(
    {"action", "effect", "outcome", "charge"}
)

#: Within a <Condition>, which child names carry which part of the predicate.
_FIELD_NAMES: frozenset[str] = frozenset(
    {"field", "attribute", "name", "key", "parameter", "lhs"}
)
_OPERATOR_NAMES: frozenset[str] = frozenset(
    {"operator", "op", "comparison", "comparator", "match"}
)
_VALUE_NAMES: frozenset[str] = frozenset(
    {"value", "values", "val", "rhs", "operand"}
)
#: Within an <Action>, which child names carry the action's code.
_TYPE_NAMES: frozenset[str] = frozenset(
    {"type", "actiontype", "action", "code", "kind", "name"}
)

#: Header fields, by the names a vendor is likely to use. Everything else in a
#: rule element becomes an unmapped extra rather than being dropped.
_HEADER_FIELDS: dict[str, frozenset[str]] = {
    "rule_key": frozenset({"rulekey", "key", "id", "ruleid", "code", "rulecode"}),
    "name": frozenset({"name", "rulename", "title", "description2"}),
    "description": frozenset({"description", "desc", "comment", "notes"}),
    "charging_mode": frozenset({"chargingmode", "mode", "paymenttype", "accounttype",
                                "billingtype"}),
    "rule_type": frozenset({"ruletype", "type", "category", "kind"}),
    "service_type": frozenset({"service", "servicetype", "servicename", "bearer"}),
    "priority": frozenset({"priority", "rank", "order", "sequence", "precedence"}),
    "effective_from": frozenset({"effectivefrom", "validfrom", "startdate", "from",
                                 "datefrom", "activationdate"}),
    "effective_to": frozenset({"effectiveto", "validto", "enddate", "to", "dateto",
                               "expirydate"}),
    "currency_code": frozenset({"currency", "currencycode", "ccy"}),
    "product": frozenset({"product", "productcode", "productid"}),
    "offer": frozenset({"offer", "offercode", "offerid"}),
    "tariff_plan": frozenset({"tariffplan", "plan", "tariff", "tariffplancode",
                              "rateplan"}),
    "external_ref": frozenset({"externalref", "externalid", "vendorid", "sourceid",
                               "reference"}),
    "external_version": frozenset({"externalversion", "version", "revision"}),
    "owner": frozenset({"owner", "author", "responsible"}),
    "conflict_group": frozenset({"conflictgroup", "exclusiongroup"}),
    "stacking_policy": frozenset({"stackingpolicy", "stacking"}),
    "execution_mode": frozenset({"executionmode", "runtime"}),
}

_HEADER_BY_NAME: dict[str, str] = {
    alias: field_name
    for field_name, aliases in _HEADER_FIELDS.items()
    for alias in aliases
}


class XmlRuleError(Exception):
    """One rule element that cannot be read. Carries where, so the reject report
    points at an element rather than at a row number."""

    def __init__(self, message: str, *, element: str = ""):
        super().__init__(message)
        self.message = message
        self.element = element


@dataclass(slots=True)
class Rejection:
    offset: int
    message: str
    column: str = ""
    row: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedXml:
    drafts: list[CanonicalDraft] = field(default_factory=list)
    #: The rule element as a nested dict, aligned to `drafts` by index, for the
    #: forensic record. Nested — flattening it here would lose exactly what this
    #: adapter exists to preserve.
    raw: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rule_element: str = ""


# --- Detection ---------------------------------------------------------------


def looks_structured(data: bytes) -> bool:
    """Whether this XML nests conditions or actions inside its rule elements.

    The test is cheap and specific: if no rule element contains a recognised
    condition or action container, the file really is tabular-shaped XML and the
    generic flattener handles it correctly. Only nesting needs this adapter, and
    guessing wrong in either direction is worse than not guessing.
    """
    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except ET.ParseError:
        return False
    known = (
        _CONDITION_CONTAINERS | _ACTION_CONTAINERS
        # <Action> repeated directly under the rule, with no container.
        | _CONDITION_ELEMENTS | _ACTION_ELEMENTS
    )
    for element in list(root)[:20]:
        for child in element:
            # Normalised, not raw: the sets are lower-case and every real export
            # writes <Conditions>, not <conditions>. Comparing the raw tag makes
            # this return False for every file it exists to catch.
            if _normalise(_local(child.tag)) in known:
                return True
    return False


# --- Parsing -----------------------------------------------------------------


def parse_rules(
    data: bytes,
    *,
    default_effective_from: date | None = None,
    source_system_code: str | None = None,
    default_charging_mode: str | None = None,
    default_currency: str | None = None,
) -> ParsedXml:
    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except ET.ParseError as exc:
        raise ValidationFailedError(f"Invalid XML: {exc}") from exc

    elements = list(root)
    if not elements:
        raise ValidationFailedError("The XML file contains no rule elements.")

    parsed = ParsedXml(rule_element=_local(elements[0].tag))
    effective_from = default_effective_from or date.today()

    for offset, element in enumerate(elements):
        raw = _as_dict(element)
        try:
            draft, notes = _convert(
                element,
                effective_from=effective_from,
                source_system_code=source_system_code,
                default_charging_mode=default_charging_mode,
                default_currency=default_currency,
            )
        except XmlRuleError as exc:
            parsed.rejections.append(
                Rejection(offset=offset, message=exc.message, column=exc.element,
                          row=raw)
            )
            continue
        except tabular.RowError as exc:
            parsed.rejections.append(
                Rejection(offset=offset, message=exc.message, column=exc.column,
                          row=raw)
            )
            continue
        parsed.drafts.append(draft)
        parsed.raw.append(raw)
        parsed.notes.extend(notes)

    return parsed


def _convert(
    element: ET.Element,
    *,
    effective_from: date,
    source_system_code: str | None,
    default_charging_mode: str | None,
    default_currency: str | None,
) -> tuple[CanonicalDraft, list[str]]:
    notes: list[str] = []
    aliases: dict[str, str] = {}
    header, extras, provenance = _header(element)

    name = header.get("name") or header.get("rule_key") or ""
    if not name:
        raise XmlRuleError("The rule element has no name.", element="Name")

    service_type = _service_type(header, notes)
    charging_mode = _charging_mode(header, default_charging_mode, aliases, notes)
    currency = (header.get("currency_code") or default_currency or "").upper() or None

    conditions = _conditions(element, aliases)
    actions = _actions(element, currency)
    if not actions:
        raise XmlRuleError(
            "The rule has no readable actions. Expected an <Actions> block "
            "containing <Action> elements, each with a type and a value.",
            element="Actions",
        )

    rule_type = _rule_type(header, actions, charging_mode, aliases, notes)

    return (
        CanonicalDraft(
            rule_key=header.get("rule_key") or None,
            rule_name=name,
            description=header.get("description", ""),
            charging_mode=charging_mode,
            rule_type_code=rule_type,
            service_type=service_type,
            root_group=DraftConditionGroup(logic="AND", conditions=tuple(conditions)),
            actions=tuple(actions),
            behaviour=DraftBehaviour(
                priority=_int(header.get("priority"), default=100),
                stacking_policy=(header.get("stacking_policy") or "EXCLUSIVE").upper(),
                conflict_group=header.get("conflict_group") or None,
                execution_mode=(header.get("execution_mode") or "BOTH").upper(),
            ),
            targets=DraftTargets(
                product=_code(header.get("product")),
                offer=_code(header.get("offer")),
                tariff_plan=_code(header.get("tariff_plan")),
            ),
            validity=DraftValidity(
                effective_from=tabular._date(
                    header.get("effective_from"), "effective_from", effective_from
                ),
                effective_to=(
                    tabular._date(header["effective_to"], "effective_to", None)
                    if header.get("effective_to")
                    else None
                ),
                currency_code=currency,
            ),
            provenance=Provenance(
                channel="FILE",
                source_system_code=source_system_code,
                external_ref=header.get("external_ref") or header.get("rule_key"),
                external_version=header.get("external_version"),
                fields=provenance,
                applied_aliases=aliases,
                unmapped=extras,
            ),
            owner=header.get("owner") or None,
        ),
        notes,
    )


# --- Header ------------------------------------------------------------------


def _header(
    element: ET.Element,
) -> tuple[dict[str, str], dict[str, Any], dict[str, FieldProvenance]]:
    """Read the rule's own fields, from attributes and non-container children.

    Anything unrecognised becomes an extra rather than being dropped, so a vendor
    field we do not model yet shows up as "3 unmapped fields on this rule".
    """
    header: dict[str, str] = {}
    extras: dict[str, Any] = {}
    provenance: dict[str, FieldProvenance] = {}

    def take(source_name: str, value: str) -> None:
        text = value.strip()
        if not text:
            return
        canonical = _HEADER_BY_NAME.get(_normalise(source_name))
        if canonical and canonical not in header:
            header[canonical] = text
            provenance[canonical] = FieldProvenance(source_name, value, "xml")
        elif canonical is None:
            extras[source_name] = text

    for attribute, value in element.attrib.items():
        take(_local(attribute), value)

    for child in element:
        tag = _local(child.tag)
        if _normalise(tag) in _CONDITION_CONTAINERS | _ACTION_CONTAINERS:
            continue
        if _normalise(tag) in _CONDITION_ELEMENTS | _ACTION_ELEMENTS:
            continue
        if len(child):
            # A nested block we do not recognise. Kept whole rather than
            # flattened, because flattening is the bug this adapter exists for.
            extras[tag] = _as_dict(child)
            continue
        take(tag, child.text or "")

    return header, extras, provenance


def _service_type(header: dict[str, str], notes: list[str]) -> str:
    raw = (header.get("service_type") or "").strip().upper()
    if not raw:
        raise XmlRuleError("The rule has no service type.", element="Service")
    from app.modules.rules.vocabulary.aliases import resolve_service_type

    resolved, alias = resolve_service_type(raw)
    if alias:
        notes.append(f"Service type '{alias}' read as '{resolved}'.")
    return str(resolved)


def _charging_mode(
    header: dict[str, str],
    default: str | None,
    aliases: dict[str, str],
    notes: list[str],
) -> str:
    raw = header.get("charging_mode")
    if raw:
        resolved, alias = resolve_charging_mode(raw)
        if alias:
            aliases[f"charging_mode:{alias}"] = str(resolved)
        if resolved in set(ChargingMode):
            return str(resolved)
        raise XmlRuleError(
            f"'{raw}' is not a charging mode. Use PREPAID, POSTPAID or BOTH.",
            element="ChargingMode",
        )
    if default:
        return default
    notes.append("No charging mode in the export; defaulted to BOTH.")
    return ChargingMode.BOTH


def _rule_type(
    header: dict[str, str],
    actions: list[DraftAction],
    charging_mode: str,
    aliases: dict[str, str],
    notes: list[str],
) -> str:
    stated = (header.get("rule_type") or "").strip().upper()
    if stated:
        resolved, alias = resolve_rule_type(stated)
        if alias:
            aliases[f"rule_type:{alias}"] = str(resolved)
        if resolved not in RULE_TYPE_BY_CODE:
            raise XmlRuleError(f"'{stated}' is not a rule type.", element="RuleType")
        return str(resolved)
    inferred = tabular._infer_type(actions, charging_mode)
    notes.append(f"Rule type inferred as {inferred} from the rule's actions.")
    return inferred


# --- Conditions --------------------------------------------------------------


def _conditions(element: ET.Element, aliases: dict[str, str]) -> list[DraftCondition]:
    """Every <Condition> becomes one predicate. All of them, not the last one."""
    out: list[DraftCondition] = []
    for node in _collect(element, _CONDITION_CONTAINERS, _CONDITION_ELEMENTS):
        parts = _parts(node)
        attribute_raw = _first(parts, _FIELD_NAMES)
        if not attribute_raw:
            raise XmlRuleError(
                "A condition has no attribute. Expected a child such as "
                "<Field>, <Attribute> or <Name>.",
                element="Condition",
            )
        attribute, alias = resolve_attribute(attribute_raw)
        if alias:
            aliases[f"attribute:{alias}"] = str(attribute)

        attr = ATTRIBUTE_BY_KEY.get(str(attribute))
        if attr is None:
            raise XmlRuleError(
                f"'{attribute_raw}' is not a rating attribute.", element="Condition"
            )

        operator = (_first(parts, _OPERATOR_NAMES) or Operator.EQUALS.value).upper()
        raw_value = _first(parts, _VALUE_NAMES)
        values = _values(node, raw_value, attr.data_type)
        if not values and operator not in (Operator.EXISTS, Operator.NOT_EXISTS):
            raise XmlRuleError(
                f"The condition on '{attribute}' has no value.", element="Condition"
            )
        out.append(
            DraftCondition(
                attribute=str(attribute),
                operator=operator,
                values=tuple(values),
                negated=_flag(parts.get("negate") or parts.get("negated")),
                sequence=len(out),
            )
        )
    return out


def _values(node: ET.Element, raw: str | None, data_type: str) -> list[Any]:
    """A condition's values, from repeated <Value> elements or one delimited cell.

    Repeated elements win: ``<Value>A</Value><Value>B</Value>`` is unambiguous,
    whereas a comma inside a single value is a real possibility in a description.
    """
    repeated = [
        (child.text or "").strip()
        for child in node
        if _normalise(_local(child.tag)) in _VALUE_NAMES and (child.text or "").strip()
    ]
    if len(repeated) > 1:
        return [tabular._scalar(data_type, v) for v in repeated]
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > 1:
        return [tabular._scalar(data_type, p) for p in parts]
    return [tabular._scalar(data_type, raw.strip())]


# --- Actions -----------------------------------------------------------------


def _actions(element: ET.Element, currency: str | None) -> list[DraftAction]:
    """Every <Action> becomes one action, with its parameters typed.

    Parameters are matched against the action's declared spec by name, so
    ``<Unit>SECOND</Unit>`` lands on the ``unit`` parameter of ``SET_RATE`` and a
    stray ``<Unit>`` on an action that has no unit is preserved as an extra rather
    than silently attached to something it does not belong to.
    """
    out: list[DraftAction] = []
    for node in _collect(element, _ACTION_CONTAINERS, _ACTION_ELEMENTS):
        parts = _parts(node)
        type_raw = _first(parts, _TYPE_NAMES)
        if not type_raw:
            raise XmlRuleError(
                "An action has no type. Expected a child such as <Type> or "
                "<ActionType>.",
                element="Action",
            )
        code, alias = resolve_action(type_raw.upper())
        del alias
        spec = ACTION_BY_CODE.get(str(code))
        if spec is None:
            raise XmlRuleError(
                f"'{type_raw}' is not a known action.", element="Action"
            )

        declared = {p.key: p for p in spec.params}
        by_normalised = {_normalise(key): key for key in declared}
        parameters: list[DraftParameter] = []
        seen: set[str] = set()

        for source_name, value in parts.items():
            if _normalise(source_name) in _TYPE_NAMES:
                continue
            key = by_normalised.get(_normalise(source_name))
            if key is None:
                continue
            parameters.append(_parameter(key, value, declared[key], currency))
            seen.add(key)

        # A bare <Value> is the action's principal parameter — the rate for
        # SET_RATE, the percentage for APPLY_DISCOUNT. Vendors write it that way
        # far more often than they name the parameter.
        principal = spec.value_param
        if principal and principal not in seen:
            raw_value = _first(parts, _VALUE_NAMES)
            if raw_value:
                parameters.append(
                    _parameter(principal, raw_value, declared[principal], currency)
                )

        out.append(
            DraftAction(
                action_type=str(code), parameters=tuple(parameters), sequence=len(out)
            )
        )
    return out


def _parameter(
    key: str, raw: str, spec: Any, currency: str | None
) -> DraftParameter:
    value_type = getattr(spec, "value_type", ValueType.STRING)
    if value_type == ValueType.MONEY:
        return DraftParameter(
            key, tabular._decimal(raw, key), ValueType.MONEY, currency=currency
        )
    if value_type == ValueType.NUMBER:
        return DraftParameter(key, tabular._decimal(raw, key), ValueType.NUMBER)
    if value_type == ValueType.BOOLEAN:
        return DraftParameter(key, _flag(raw), ValueType.BOOLEAN)
    if value_type in (ValueType.ENUM, ValueType.REFERENCE):
        return DraftParameter(key, raw.strip().upper(), value_type)
    return DraftParameter(key, raw.strip(), value_type)


# --- Tree helpers ------------------------------------------------------------


def _collect(
    element: ET.Element, containers: frozenset[str], items: frozenset[str]
) -> list[ET.Element]:
    """The item elements, whether they sit in a container or directly on the rule.

    Both shapes are common and neither is wrong, so both are read rather than one
    being declared the standard.
    """
    found: list[ET.Element] = []
    for child in element:
        tag = _normalise(_local(child.tag))
        if tag in containers:
            found.extend(
                grandchild
                for grandchild in child
                if _normalise(_local(grandchild.tag)) in items
            )
        elif tag in items:
            found.append(child)
    return found


def _parts(node: ET.Element) -> dict[str, str]:
    """One element's attributes and leaf children, by local name.

    Attributes first so ``<Condition field="x"><Field>y</Field></Condition>``
    resolves deterministically rather than by document order.
    """
    parts: dict[str, str] = {}
    for name, value in node.attrib.items():
        text = value.strip()
        if text:
            parts[_local(name)] = text
    for child in node:
        text = (child.text or "").strip()
        name = _local(child.tag)
        if text and name not in parts:
            parts[name] = text
    return parts


def _as_dict(element: ET.Element) -> dict[str, Any]:
    """The element as nested JSON, for the forensic record.

    Repeated siblings become a list. That is the whole difference from the
    generic flattener, and it is why the raw payload of an XML import can be read
    back and understood.
    """
    out: dict[str, Any] = {}
    for name, value in element.attrib.items():
        out[f"@{_local(name)}"] = value
    for child in element:
        name = _local(child.tag)
        value: Any = _as_dict(child) if (len(child) or child.attrib) else (
            (child.text or "").strip()
        )
        if name in out:
            existing = out[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                out[name] = [existing, value]
        else:
            out[name] = value
    if not out and element.text and element.text.strip():
        return {"_text": element.text.strip()}
    return out


def _local(tag: str) -> str:
    """Strip the namespace: ``{urn:vendor}Rate`` → ``Rate``."""
    return tag.rsplit("}", 1)[-1]


def _normalise(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _first(parts: dict[str, str], names: frozenset[str]) -> str | None:
    for source_name, value in parts.items():
        if _normalise(source_name) in names:
            return value
    return None


def _flag(value: Any) -> bool:
    return str(value or "").strip().upper() in {"TRUE", "YES", "Y", "1"}


def _int(raw: str | None, *, default: int) -> int:
    if not raw:
        return default
    try:
        return int(tabular._decimal(raw, "priority"))
    except (tabular.RowError, ValueError, TypeError):
        return default


def _code(value: str | None) -> str | None:
    return value.strip().upper() if value and value.strip() else None
