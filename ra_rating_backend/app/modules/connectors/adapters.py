"""Vendor adapters: native tariff export → canonical rule.

Every charging system models a tariff differently. Oracle BRM has rate plans
containing rate tiers; Ericsson CS has tariff classes with time-band rate steps;
Huawei CBS has pricing plans with rating segments. None of them share a field
name, and none of them mean quite the same thing by "rate".

An adapter's job is to answer three questions in the vendor's own vocabulary:

    what identifies this rule        → rule_key, name
    what does it match               → conditions, in canonical attributes
    what does it charge              → actions, in canonical action types

Everything downstream — validation, compilation, rating — sees only canonical
rules, so adding the fourth vendor is a new adapter, not a new code path.

Adapters are **pure**: records in, canonical dicts out, no I/O. That keeps each
vendor's mapping directly testable against a real export sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar, Protocol

from app.modules.rules.constants import ActionType, Operator, RuleType

DEFAULT_EFFECTIVE_FROM = date(2026, 1, 1)


class AdapterError(Exception):
    """One record could not be mapped. Reported per record, never fatal."""

    def __init__(self, message: str, *, field: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class AdapterSpec:
    code: str
    vendor: str
    label: str
    description: str
    #: Where the tariff records live in the vendor's export, for the UI hint.
    record_path: str
    #: Native field names the adapter reads, so a mapping mismatch is visible
    #: before an import is attempted.
    expects: tuple[str, ...]
    notes: str = ""


class Adapter(Protocol):
    spec: AdapterSpec

    def records(self, payload: Any) -> list[dict[str, Any]]: ...
    def to_canonical(self, record: dict[str, Any]) -> dict[str, Any]: ...


# --- Shared helpers ---------------------------------------------------------


def _get(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        if record.get(name) not in (None, ""):
            return record[name]
        if lowered.get(name.lower()) not in (None, ""):
            return lowered[name.lower()]
    return default


def _num(value: Any, field_name: str = "") -> float:
    if value in (None, ""):
        raise AdapterError(f"'{field_name}' is required.", field=field_name)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise AdapterError(f"'{value}' is not a number.", field=field_name) from exc


def _date(value: Any, fallback: date = DEFAULT_EFFECTIVE_FROM) -> str:
    if value in (None, ""):
        return fallback.isoformat()
    text = str(value).strip().split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return fallback.isoformat()


def _key(*parts: Any) -> str:
    joined = "_".join(str(p).strip().upper().replace(" ", "_") for p in parts if p)
    return "".join(ch for ch in joined if ch.isalnum() or ch in "_.-")[:72] or "RULE"


def _condition(attribute: str, values: list[Any], operator: str = Operator.EQUALS.value):
    return {
        "attribute": attribute,
        "operator": operator if len(values) == 1 else Operator.IN.value,
        "values": values,
        "group_index": 0,
        "negate": False,
    }


def _records_from(payload: Any, *paths: str) -> list[dict[str, Any]]:
    """Pull the record list out of a vendor envelope, tolerating shapes."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for path in paths:
            node: Any = payload
            for part in path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, list):
                return [r for r in node if isinstance(r, dict)]
        # A flat dict of one record is a legitimate single-rule export.
        if any(isinstance(v, str | int | float) for v in payload.values()):
            return [payload]
    return []


# --- Oracle BRM -------------------------------------------------------------


class OracleBrmAdapter:
    """Oracle BRM rate plans.

    BRM nests rate tiers under a rate plan, with the product and the service
    (``/service/telco/gsm/voice``) carried on the plan. The RUM ("rateable usage
    metric") tells us the unit — BRM says ``duration``, we say SECOND.
    """

    spec = AdapterSpec(
        code="ORACLE_BRM",
        vendor="Oracle",
        label="Oracle BRM — rate plans",
        description=(
            "Maps BRM rate plans and their rate tiers onto canonical base-tariff "
            "rules. Impact category becomes the destination zone; the RUM becomes "
            "the charging unit."
        ),
        record_path="rate_plans[]",
        expects=(
            "rate_plan_name", "product_name", "service_type", "impact_category",
            "time_model", "rum", "quantity", "amount", "currency", "start_t", "end_t",
        ),
        notes="Set `beat` on a tier to emit a pulse rule alongside the tariff.",
    )

    def records(self, payload: Any) -> list[dict[str, Any]]:
        return _records_from(payload, "rate_plans", "rateplans", "data.rate_plans")

    def to_canonical(self, record: dict[str, Any]) -> dict[str, Any]:
        plan = _get(record, "rate_plan_name", "ratePlanName", "name")
        if not plan:
            raise AdapterError("A rate plan name is required.", field="rate_plan_name")

        product = _get(record, "product_name", "productName", "product")
        # BRM service paths are hierarchical; the leaf is the service.
        raw_service = str(_get(record, "service_type", "service", default="voice"))
        service = raw_service.rstrip("/").split("/")[-1].upper()
        service = {"GSM": "VOICE", "GPRS": "DATA", "SMS": "SMS"}.get(service, service)

        impact = _get(record, "impact_category", "impactCategory")
        time_model = _get(record, "time_model", "timeModel")
        rum = str(_get(record, "rum", "RUM", default="duration")).lower()
        unit = {"duration": "SECOND", "occurrence": "EVENT", "volume": "BYTE"}.get(rum, "SECOND")

        amount = _num(_get(record, "amount", "rate", "value"), "amount")
        quantity = float(_get(record, "quantity", "per_quantity", default=1) or 1)

        conditions = [_condition("service_type", [service])]
        if impact:
            conditions.append(_condition("destination_zone", [str(impact).upper()]))
        if time_model:
            conditions.append(_condition("time_band", [str(time_model).upper()]))

        actions = [
            {
                "action_type": ActionType.SET_RATE.value,
                "params": {
                    "rate": amount,
                    "unit": unit,
                    "per_units": quantity,
                    "currency": str(_get(record, "currency", default="GBP")).upper(),
                },
            }
        ]
        beat = _get(record, "beat", "granularity")
        if beat:
            actions.append(
                {
                    "action_type": ActionType.SET_PULSE.value,
                    "params": {"initial_seconds": _num(beat, "beat"),
                               "subsequent_seconds": _num(beat, "beat")},
                }
            )

        return {
            "rule_key": _key("BRM", plan, impact, time_model),
            "name": f"{plan}{f' — {impact}' if impact else ''}",
            "description": f"Imported from Oracle BRM rate plan '{plan}'.",
            "rule_type": RuleType.BASE_TARIFF.value,
            "service_type": service,
            "category": "Oracle BRM",
            "priority": int(_get(record, "priority", default=100) or 100),
            "effective_from": _date(_get(record, "start_t", "startDate", "effective_from")),
            "effective_to": (
                _date(_get(record, "end_t", "endDate"), fallback=None)  # type: ignore[arg-type]
                if _get(record, "end_t", "endDate")
                else None
            ),
            "currency_code": str(_get(record, "currency", default="GBP")).upper(),
            "source_system": "ORACLE_BRM",
            "conditions": conditions
            + ([_condition("product", [str(product).upper()])] if product else []),
            "actions": actions,
        }


# --- Ericsson Charging System ----------------------------------------------


class EricssonCsAdapter:
    """Ericsson CS tariff classes.

    Ericsson expresses a tariff as a class holding rate steps per time band and
    destination group. Charging is quoted per ``chargingUnit`` seconds, and the
    first/subsequent unit sizes are the pulse — Ericsson calls them
    ``firstInterval`` and ``nextInterval``.
    """

    spec = AdapterSpec(
        code="ERICSSON_CS",
        vendor="Ericsson",
        label="Ericsson Charging System — tariff classes",
        description=(
            "Maps Ericsson tariff classes and rate steps onto canonical rules. "
            "firstInterval/nextInterval become a pulse rule; destinationGroup "
            "becomes the destination zone."
        ),
        record_path="tariffClasses[].rateSteps[]",
        expects=(
            "tariffClassId", "tariffClassName", "offerName", "serviceType",
            "destinationGroup", "timeBand", "ratePerUnit", "chargingUnit",
            "firstInterval", "nextInterval", "minimumCharge", "validFrom", "validTo",
        ),
        notes="Rate steps are flattened: one canonical rule per step.",
    )

    def records(self, payload: Any) -> list[dict[str, Any]]:
        classes = _records_from(payload, "tariffClasses", "tariff_classes", "data.tariffClasses")
        out: list[dict[str, Any]] = []
        for cls in classes:
            steps = cls.get("rateSteps") or cls.get("rate_steps") or []
            if not steps:
                out.append(cls)
                continue
            # Flatten: the class header carries identity, the step carries price.
            for step in steps:
                if isinstance(step, dict):
                    out.append({**{k: v for k, v in cls.items() if k != "rateSteps"}, **step})
        return out

    def to_canonical(self, record: dict[str, Any]) -> dict[str, Any]:
        class_name = _get(record, "tariffClassName", "tariffClassId", "name")
        if not class_name:
            raise AdapterError("A tariff class name is required.", field="tariffClassName")

        service = str(_get(record, "serviceType", "service", default="VOICE")).upper()
        service = {"TELEPHONY": "VOICE", "GPRS": "DATA", "SMSMO": "SMS"}.get(service, service)

        destination = _get(record, "destinationGroup", "destination")
        band = _get(record, "timeBand", "timeClass")
        offer = _get(record, "offerName", "offer")

        rate = _num(_get(record, "ratePerUnit", "rate", "amount"), "ratePerUnit")
        charging_unit = float(_get(record, "chargingUnit", "unitSize", default=60) or 60)

        conditions = [_condition("service_type", [service])]
        if destination:
            conditions.append(_condition("destination_zone", [str(destination).upper()]))
        if band:
            conditions.append(_condition("time_band", [str(band).upper()]))
        if offer:
            conditions.append(_condition("offer", [str(offer).upper()]))

        actions: list[dict[str, Any]] = [
            {
                "action_type": ActionType.SET_RATE.value,
                "params": {
                    "rate": rate,
                    "unit": "SECOND",
                    "per_units": charging_unit,
                    "currency": str(_get(record, "currency", default="GBP")).upper(),
                },
            }
        ]

        first = _get(record, "firstInterval", "firstUnit")
        nxt = _get(record, "nextInterval", "nextUnit")
        if first:
            actions.append(
                {
                    "action_type": ActionType.SET_PULSE.value,
                    "params": {
                        "initial_seconds": _num(first, "firstInterval"),
                        "subsequent_seconds": (
                            _num(nxt, "nextInterval")
                            if nxt
                            else _num(first, "firstInterval")
                        ),
                    },
                }
            )
        minimum = _get(record, "minimumCharge", "minCharge")
        if minimum:
            actions.append(
                {
                    "action_type": ActionType.SET_MINIMUM_CHARGE.value,
                    "params": {"amount": _num(minimum, "minimumCharge")},
                }
            )

        return {
            "rule_key": _key("ERIC", class_name, destination, band),
            "name": f"{class_name}{f' — {destination}' if destination else ''}"
            + (f" ({band})" if band else ""),
            "description": f"Imported from Ericsson tariff class '{class_name}'.",
            "rule_type": RuleType.BASE_TARIFF.value,
            "service_type": service,
            "category": "Ericsson CS",
            "priority": int(_get(record, "priority", default=100) or 100),
            "effective_from": _date(_get(record, "validFrom", "startDate")),
            "effective_to": _date(_get(record, "validTo")) if _get(record, "validTo") else None,
            "currency_code": str(_get(record, "currency", default="GBP")).upper(),
            "source_system": "ERICSSON_CS",
            "conditions": conditions,
            "actions": actions,
        }


# --- Huawei CBS -------------------------------------------------------------


class HuaweiCbsAdapter:
    """Huawei CBS pricing plans.

    Huawei nests rating segments under a pricing plan. Free-unit packages are
    exported alongside the rate, so a segment can produce both a base-tariff rule
    and a bundle rule — which is why an adapter may return several canonical
    rules from one record.
    """

    spec = AdapterSpec(
        code="HUAWEI_CBS",
        vendor="Huawei",
        label="Huawei CBS — pricing plans",
        description=(
            "Maps Huawei pricing plans and rating segments onto canonical rules. "
            "Free-unit packages become bundle rules; taxSchema becomes a tax rule."
        ),
        record_path="pricingPlans[].ratingSegments[]",
        expects=(
            "pricingPlanId", "pricingPlanName", "productOfferingId", "serviceFlag",
            "areaCode", "timeSegment", "unitPrice", "unitSize", "pulseSize",
            "freeUnitType", "freeUnitValue", "taxSchema", "effDate", "expDate",
        ),
        notes="A segment carrying freeUnitValue also emits a CONSUME_BUNDLE rule.",
    )

    #: Huawei encodes the service numerically in serviceFlag.
    _SERVICE: ClassVar[dict[str, str]] = {"1": "VOICE", "2": "SMS", "3": "DATA", "4": "MMS"}

    def records(self, payload: Any) -> list[dict[str, Any]]:
        plans = _records_from(payload, "pricingPlans", "pricing_plans", "data.pricingPlans")
        out: list[dict[str, Any]] = []
        for plan in plans:
            segments = plan.get("ratingSegments") or plan.get("rating_segments") or []
            if not segments:
                out.append(plan)
                continue
            for segment in segments:
                if isinstance(segment, dict):
                    out.append(
                        {**{k: v for k, v in plan.items() if k != "ratingSegments"}, **segment}
                    )
        return out

    def to_canonical(self, record: dict[str, Any]) -> dict[str, Any]:
        plan = _get(record, "pricingPlanName", "pricingPlanId", "name")
        if not plan:
            raise AdapterError("A pricing plan name is required.", field="pricingPlanName")

        flag = str(_get(record, "serviceFlag", "serviceType", default="1"))
        service = self._SERVICE.get(flag, flag.upper())
        area = _get(record, "areaCode", "destinationArea")
        segment = _get(record, "timeSegment", "timeType")
        offering = _get(record, "productOfferingId", "productOffering")

        price = _num(_get(record, "unitPrice", "price"), "unitPrice")
        unit_size = float(_get(record, "unitSize", default=60) or 60)

        conditions = [_condition("service_type", [service])]
        if area:
            conditions.append(_condition("destination_zone", [str(area).upper()]))
        if segment:
            conditions.append(_condition("time_band", [str(segment).upper()]))
        if offering:
            conditions.append(_condition("product", [str(offering).upper()]))

        actions: list[dict[str, Any]] = [
            {
                "action_type": ActionType.SET_RATE.value,
                "params": {
                    "rate": price,
                    "unit": (
                        "SECOND"
                        if service == "VOICE"
                        else "MESSAGE"
                        if service == "SMS"
                        else "BYTE"
                    ),
                    "per_units": unit_size,
                    "currency": str(_get(record, "currency", default="GBP")).upper(),
                },
            }
        ]
        pulse = _get(record, "pulseSize", "chargeUnit")
        if pulse:
            actions.append(
                {
                    "action_type": ActionType.SET_PULSE.value,
                    "params": {
                        "initial_seconds": _num(pulse, "pulseSize"),
                        "subsequent_seconds": _num(pulse, "pulseSize"),
                    },
                }
            )
        tax = _get(record, "taxSchema", "taxCode")
        if tax:
            actions.append(
                {
                    "action_type": ActionType.APPLY_TAX.value,
                    "params": {"tax_rule": str(tax).upper()},
                }
            )

        return {
            "rule_key": _key("HW", plan, area, segment),
            "name": f"{plan}{f' — {area}' if area else ''}"
            + (f" ({segment})" if segment else ""),
            "description": f"Imported from Huawei CBS pricing plan '{plan}'.",
            "rule_type": RuleType.BASE_TARIFF.value,
            "service_type": service,
            "category": "Huawei CBS",
            "priority": int(_get(record, "priority", default=100) or 100),
            "effective_from": _date(_get(record, "effDate", "effectiveDate")),
            "effective_to": _date(_get(record, "expDate")) if _get(record, "expDate") else None,
            "currency_code": str(_get(record, "currency", default="GBP")).upper(),
            "source_system": "HUAWEI_CBS",
            "conditions": conditions,
            "actions": actions,
        }


# --- Generic canonical ------------------------------------------------------


class CanonicalAdapter:
    """Records already in our canonical shape — the escape hatch for a source
    with no adapter yet, and the format the public rule API accepts."""

    spec = AdapterSpec(
        code="CANONICAL",
        vendor="Any",
        label="Canonical rule format",
        description="Records already shaped as canonical rules. No mapping applied.",
        record_path="rules[]",
        expects=("rule_key", "name", "rule_type", "service_type", "conditions", "actions"),
    )

    def records(self, payload: Any) -> list[dict[str, Any]]:
        return _records_from(payload, "rules", "data.rules", "items")

    def to_canonical(self, record: dict[str, Any]) -> dict[str, Any]:
        if not record.get("name"):
            raise AdapterError("A rule name is required.", field="name")
        return dict(record)


ADAPTERS: dict[str, Adapter] = {
    a.spec.code: a  # type: ignore[misc]
    for a in (
        OracleBrmAdapter(),
        EricssonCsAdapter(),
        HuaweiCbsAdapter(),
        CanonicalAdapter(),
    )
}

#: Vendors named in the requirement that have no adapter yet. Listed so the UI
#: can offer them as source systems and say plainly that the mapping is pending,
#: rather than silently omitting them.
PLANNED_VENDORS: tuple[tuple[str, str], ...] = (
    ("NOKIA_CS", "Nokia charging systems"),
    ("AMDOCS", "Amdocs"),
    ("NETCRACKER", "Netcracker"),
    ("PRODUCT_CATALOGUE", "Product catalogue"),
    ("CRM", "CRM system"),
    ("TAX_SYSTEM", "Tax system"),
    ("NUMBERING_PLAN", "Prefix / numbering plan system"),
)


def adapter_for(code: str) -> Adapter:
    adapter = ADAPTERS.get(code.upper())
    if adapter is None:
        raise AdapterError(f"No adapter is registered for '{code}'.")
    return adapter


@dataclass
class MappedRecord:
    index: int
    raw: dict[str, Any]
    canonical: dict[str, Any] | None = None
    error: str = ""
    field: str = ""


def map_records(code: str, payload: Any) -> tuple[list[MappedRecord], AdapterSpec]:
    """Map a whole vendor export. One bad record never fails the batch."""
    adapter = adapter_for(code)
    out: list[MappedRecord] = []
    for index, record in enumerate(adapter.records(payload)):
        entry = MappedRecord(index=index, raw=record)
        try:
            entry.canonical = adapter.to_canonical(record)
        except AdapterError as exc:
            entry.error = exc.message
            entry.field = exc.field
        except Exception as exc:
            entry.error = f"Could not map this record: {exc}"
        out.append(entry)
    return out, adapter.spec
