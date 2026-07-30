"""Units of measure, and the one thing this module refuses to do.

**Conversion happens in the engine, never at ingest.** A vendor quotes a rate per
minute, or per 360 KB block, or per message. That unit is stored *as authored*, so
the rate an operator can point at in the vendor's own document is the rate they see
on our screen. Silently normalising to seconds-and-bytes on the way in makes every
imported rule unrecognisable to the team that sent it, and turns "is this right?"
into an archaeology exercise.

What this module provides is the vocabulary and the factors, so the engine and the
validator agree on what a unit *means*. The factors mirror
``rating/engine.py::_UNIT_SCALE`` exactly — binary multiples for data, because that
is what the live engine already uses and a 1024-vs-1000 disagreement between the
two is a 2.4% revenue error at gigabyte scale, always in the customer's favour.
``test_canonical_values.py`` asserts the two tables agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.rules.vocabulary.values import UnitDimension


@dataclass(frozen=True, slots=True)
class UnitSpec:
    code: str
    label: str
    dimension: str
    #: Multiples of the dimension's base unit.
    factor: Decimal
    description: str = ""


_U = UnitSpec
_D = UnitDimension

CANONICAL_UNITS: tuple[UnitSpec, ...] = (
    # TIME — base SECOND
    _U("SECOND", "Second", _D.TIME, Decimal(1)),
    _U("MINUTE", "Minute", _D.TIME, Decimal(60)),
    _U("HOUR", "Hour", _D.TIME, Decimal(3600)),
    # VOLUME — base BYTE, binary multiples (matching the engine)
    _U("BYTE", "Byte", _D.VOLUME, Decimal(1)),
    _U("KILOBYTE", "Kilobyte", _D.VOLUME, Decimal(1024)),
    _U("MEGABYTE", "Megabyte", _D.VOLUME, Decimal(1024 * 1024)),
    _U("GIGABYTE", "Gigabyte", _D.VOLUME, Decimal(1024 * 1024 * 1024)),
    # Countable
    _U("MESSAGE", "Message", _D.MESSAGE, Decimal(1)),
    _U("EVENT", "Event", _D.EVENT, Decimal(1)),
)

UNIT_BY_CODE: dict[str, UnitSpec] = {u.code: u for u in CANONICAL_UNITS}

#: The base unit of each dimension.
BASE_UNIT: dict[str, str] = {
    _D.TIME: "SECOND",
    _D.VOLUME: "BYTE",
    _D.MESSAGE: "MESSAGE",
    _D.EVENT: "EVENT",
}

#: Vendor spellings, resolved on ingest. Kept small and explicit: a unit guessed
#: wrongly is a silent 60x or 1024x error, so anything not listed here is a
#: rejected row rather than a best effort.
UNIT_ALIASES: dict[str, str] = {
    "SEC": "SECOND", "SECS": "SECOND", "S": "SECOND",
    "MIN": "MINUTE", "MINS": "MINUTE", "M": "MINUTE",
    "HR": "HOUR", "HRS": "HOUR", "H": "HOUR",
    "B": "BYTE", "BYTES": "BYTE",
    "KB": "KILOBYTE", "KBYTE": "KILOBYTE", "KILOBYTES": "KILOBYTE",
    "MB": "MEGABYTE", "MBYTE": "MEGABYTE", "MEGABYTES": "MEGABYTE",
    "GB": "GIGABYTE", "GBYTE": "GIGABYTE", "GIGABYTES": "GIGABYTE",
    "SMS": "MESSAGE", "MSG": "MESSAGE", "MESSAGES": "MESSAGE",
    "EVT": "EVENT", "EVENTS": "EVENT", "CALL": "EVENT", "SESSION": "EVENT",
}


def resolve_unit(code: str | None) -> tuple[str | None, str | None]:
    """Return ``(canonical_unit, alias_applied)``; ``(None, None)`` for no unit."""
    if code is None:
        return None, None
    raw = str(code).strip()
    if not raw:
        return None, None
    upper = raw.upper()
    if upper in UNIT_BY_CODE:
        return upper, raw if raw != upper else None
    target = UNIT_ALIASES.get(upper)
    if target is None:
        return None, raw
    return target, raw


def dimension_of(unit_code: str | None) -> str | None:
    spec = UNIT_BY_CODE.get((unit_code or "").upper())
    return spec.dimension if spec else None


def same_dimension(left: str | None, right: str | None) -> bool:
    """Whether two units are comparable at all.

    Charging per MEGABYTE for a voice call is a modelling error rather than a
    preference, and the validator says so instead of letting the engine divide
    seconds by bytes.
    """
    a, b = dimension_of(left), dimension_of(right)
    return a is not None and a == b


def convert(quantity: Decimal, from_unit: str, to_unit: str) -> Decimal:
    """Express a quantity in another unit of the same dimension.

    Used by the engine and by simulation — never by the ingest path.
    """
    source = UNIT_BY_CODE.get((from_unit or "").upper())
    target = UNIT_BY_CODE.get((to_unit or "").upper())
    if source is None or target is None:
        raise KeyError(f"Unknown unit in conversion {from_unit!r} → {to_unit!r}.")
    if source.dimension != target.dimension:
        raise ValueError(
            f"Cannot convert {source.code} ({source.dimension}) to "
            f"{target.code} ({target.dimension})."
        )
    return quantity * source.factor / target.factor
