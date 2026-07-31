"""A legacy rule payload dict → :class:`CanonicalDraft`.

The third and last adapter, and the narrowest. The vendor connectors in
``connectors/adapters.py`` — Ericsson, Oracle BRM, Huawei, Amdocs — already know
how to read their own exports, and they emit a dict in the legacy rule shape:
``{name, service_type, conditions: [...], actions: [{action_type, params}]}``.
That mapping work is real, tested and vendor-specific, and rewriting it to emit
drafts directly would mean re-deriving four vendors' quirks for no gain.

So this converts their output rather than replacing it. What it adds is what the
legacy shape cannot carry:

**Decimal money.** The connector adapters coerce with ``float`` (``_num``), which
is the same defect as everywhere else. Values are re-read here through the codec
with the action's declared type, so a rate reaches the column exact.

**A charging mode.** No legacy payload has one. Read from an ``account_type``
condition where there is one, otherwise defaulted to ``BOTH``.

**Vendor identity.** ``external_ref`` is what makes a re-import an update rather
than a duplicate, so it is lifted out of wherever the adapter put it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftTargets,
    DraftValidity,
    Provenance,
)
from app.modules.rules.constants import ATTRIBUTE_BY_KEY, DataType
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.aliases import resolve_action, resolve_rule_type
from app.modules.rules.vocabulary.modes import ChargingMode
from app.modules.rules.vocabulary.values import ValueType

#: Where a vendor adapter might have put its own identifier. Checked in order.
_EXTERNAL_REF_KEYS: tuple[str, ...] = (
    "external_ref", "external_id", "source_ref", "vendor_id", "rule_key",
)

_MODE_FROM_ACCOUNT_TYPE: dict[str, str] = {
    "PREPAID": ChargingMode.PREPAID,
    "POSTPAID": ChargingMode.POSTPAID,
    "HYBRID": ChargingMode.BOTH,
    "ANY": ChargingMode.BOTH,
}


class PayloadError(Exception):
    def __init__(self, message: str, *, field_name: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field_name


@dataclass(slots=True)
class Conversion:
    draft: CanonicalDraft
    notes: list[str] = field(default_factory=list)


def convert(
    payload: dict[str, Any],
    *,
    source_system_code: str | None = None,
    default_effective_from: date | None = None,
    default_charging_mode: str | None = None,
    default_currency: str | None = None,
    raw: dict[str, Any] | None = None,
) -> Conversion:
    notes: list[str] = []

    name = str(payload.get("name") or "").strip()
    if not name:
        raise PayloadError("The record has no rule name.", field_name="name")

    service_type = str(payload.get("service_type") or "").strip().upper()
    if not service_type:
        raise PayloadError("The record has no service type.", field_name="service_type")

    conditions = _conditions(payload.get("conditions") or [])
    charging_mode = (
        default_charging_mode
        or _mode_from_conditions(conditions, notes)
    )
    currency = (payload.get("currency_code") or default_currency or "") or None
    currency = currency.upper() if currency else None

    actions = _actions(payload.get("actions") or [], currency)
    if not actions:
        raise PayloadError(
            "The record produced no actions, so the rule would match events and "
            "change nothing.",
            field_name="actions",
        )

    rule_type, alias = resolve_rule_type(payload.get("rule_type"))
    if alias:
        notes.append(f"Rule type '{alias}' mapped to '{rule_type}'.")
    if not rule_type:
        raise PayloadError("The record has no rule type.", field_name="rule_type")

    effective_from = _date(payload.get("effective_from")) or default_effective_from
    if effective_from is None:
        raise PayloadError(
            "The record has no effective-from date.", field_name="effective_from"
        )

    return Conversion(
        draft=CanonicalDraft(
            rule_key=None,
            rule_name=name,
            description=str(payload.get("description") or ""),
            charging_mode=str(charging_mode),
            rule_type_code=str(rule_type),
            service_type=service_type,
            root_group=DraftConditionGroup(logic="AND", conditions=tuple(conditions)),
            actions=tuple(actions),
            behaviour=DraftBehaviour(
                priority=int(payload.get("priority") or 100),
                stacking_policy=str(
                    payload.get("stacking_policy") or "EXCLUSIVE"
                ).upper(),
                conflict_group=payload.get("conflict_group") or None,
            ),
            targets=DraftTargets(
                product=_code(payload.get("product")),
                offer=_code(payload.get("offer")),
                tariff_plan=_code(payload.get("tariff_plan")),
            ),
            validity=DraftValidity(
                effective_from=effective_from,
                effective_to=_date(payload.get("effective_to")),
                currency_code=currency,
            ),
            provenance=Provenance(
                channel="CONNECTOR",
                source_system_code=source_system_code,
                external_ref=_external_ref(payload, raw),
                external_version=_str(payload.get("external_version")),
                unmapped=dict(payload.get("attributes") or {}),
            ),
            owner=payload.get("owner") or None,
        ),
        notes=notes,
    )


def _external_ref(payload: dict[str, Any], raw: dict[str, Any] | None) -> str | None:
    """The vendor's own key, from wherever the adapter left it.

    Worth looking hard for: with it, a nightly re-import updates the rule it
    created last night. Without it, identity falls back to a hash of the rule's
    predicates, which is correct but cannot survive the vendor re-pricing and
    re-targeting the same rule in one release.
    """
    for source in (payload, payload.get("attributes") or {}, raw or {}):
        for key in _EXTERNAL_REF_KEYS:
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    return None


def _mode_from_conditions(
    conditions: list[DraftCondition], notes: list[str]
) -> str:
    for condition in conditions:
        if condition.attribute != "account_type":
            continue
        modes = {
            _MODE_FROM_ACCOUNT_TYPE.get(str(v).strip().upper())
            for v in condition.values
        } - {None}
        if len(modes) == 1:
            mode = str(modes.pop())
            notes.append(f"Charging mode inferred as {mode} from account_type.")
            return mode
    notes.append("No charging mode in the export; defaulted to BOTH.")
    return ChargingMode.BOTH


def _conditions(raw: list[dict[str, Any]]) -> list[DraftCondition]:
    out: list[DraftCondition] = []
    for index, entry in enumerate(raw):
        attribute = str(entry.get("attribute") or "").strip()
        if not attribute:
            continue
        attr = ATTRIBUTE_BY_KEY.get(attribute)
        values = list(entry.get("values") or [])
        if attr is not None and attr.data_type == DataType.NUMBER:
            values = [_decimal(v) for v in values]
        out.append(
            DraftCondition(
                attribute=attribute,
                operator=str(entry.get("operator") or "EQUALS"),
                values=tuple(values),
                negated=bool(entry.get("negate")),
                sequence=index,
            )
        )
    return out


def _actions(raw: list[dict[str, Any]], currency: str | None) -> list[DraftAction]:
    """Re-type every parameter against the action spec.

    This is where the connector's floats stop. ``params`` arrives as
    ``{"rate": 0.012345}`` and leaves as a MONEY parameter holding a Decimal — the
    declared type decides, not the Python type the JSON parser happened to pick.
    """
    out: list[DraftAction] = []
    for index, entry in enumerate(raw):
        code, _alias = resolve_action(entry.get("action_type"))
        spec = ACTION_BY_CODE.get(str(code))
        if spec is None:
            continue
        declared = {p.key: p for p in spec.params}

        parameters: list[DraftParameter] = []
        for name, value in sorted((entry.get("params") or {}).items()):
            if value in (None, ""):
                continue
            param_spec = declared.get(name)
            value_type = getattr(param_spec, "value_type", ValueType.STRING)
            parameters.append(
                DraftParameter(
                    name=name,
                    raw=_typed(value, value_type),
                    value_type=value_type,
                    currency=currency if value_type == ValueType.MONEY else None,
                )
            )
        out.append(
            DraftAction(
                action_type=str(code), parameters=tuple(parameters), sequence=index
            )
        )
    return out


def _typed(value: Any, value_type: str) -> Any:
    if value_type in (ValueType.MONEY, ValueType.NUMBER):
        return _decimal(value)
    if value_type == ValueType.BOOLEAN:
        return str(value).strip().upper() in {"TRUE", "YES", "Y", "1"}
    if value_type in (ValueType.ENUM, ValueType.REFERENCE):
        return str(value).strip().upper()
    return value


def _decimal(value: Any) -> Any:
    """``repr`` for a float, so the shortest round-tripping decimal is recovered
    rather than the float's full binary expansion."""
    try:
        return Decimal(repr(value) if isinstance(value, float) else str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return value


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _code(value: Any) -> str | None:
    return str(value).strip().upper() if value not in (None, "") else None


def _str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
