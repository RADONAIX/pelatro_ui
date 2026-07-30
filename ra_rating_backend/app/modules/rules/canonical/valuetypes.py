"""The typed-value codec: one place where a raw token becomes a stored value.

Every adapter, the validator, the writer and the projection go through here. That
is the point — the legacy path had three write paths each doing its own coercion,
and the file importer's was ``float(cleaned)``.

Three rules this module enforces, none of them negotiable:

**Money is ``Decimal``, always, and never touches ``float``.** ``Decimal(str(x))``
even when the input is already numeric, because ``Decimal(0.1)`` is
``0.1000000000000000055511151231257827``. A tariff at six decimal places, times a
few hundred million CDRs a month, is a reconciliation gap nobody can explain.

**Money without a currency is rejected.** An amount whose currency is unknown
cannot be summed, compared, or put on an invoice. Storing it anyway defers the
error to whoever reads it.

**A quantized value is exact or it is an error.** ``quantize`` with
``ROUND_HALF_UP`` is applied only after checking the value has no significance
beyond six decimals; anything finer is a rejected cell, not a silent truncation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.rules.canonical.units import resolve_unit
from app.modules.rules.vocabulary.values import (
    CURRENCY_BEARING_TYPES,
    NUMERIC_VALUE_TYPES,
    ValueType,
)

#: Matches the numeric(20,6) columns. Six decimals is not decoration: a per-byte
#: data rate in a major currency needs every one of them.
SCALE = Decimal("0.000001")
#: numeric(20,6) → 14 integer digits.
MAX_INTEGER_DIGITS = 14

#: Accepted date formats, most specific first. ISO wins; the rest are what Excel
#: and European vendor exports actually emit.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%d-%b-%Y", "%d %b %Y", "%Y%m%d",
)

TRUTHY: frozenset[str] = frozenset(
    {"TRUE", "T", "YES", "Y", "1", "ON", "ENABLED", "ACTIVE"}
)
FALSY: frozenset[str] = frozenset(
    {"FALSE", "F", "NO", "N", "0", "OFF", "DISABLED", "INACTIVE"}
)

#: Currency symbols and whitespace, removed before parsing. Digit separators are
#: deliberately NOT here — which character is the decimal point depends on the
#: file's locale, and guessing wrongly is a 100x error. See `_normalise_numeric`.
_STRIP = str.maketrans({"£": "", "$": "", "€": "", "¥": "",  " ": "", " ": ""})

#: A run of three-digit groups — the only unambiguous thousands grouping.
_GROUPED = re.compile(r"^-?\d{1,3}(?:([.,])\d{3})+$")


def _normalise_numeric(text: str, *, field: str, raw: Any) -> str:
    """Resolve which separator is the decimal point, or refuse to guess.

    Vendor files arrive in both conventions and almost never declare which. The
    cases are decidable more often than they look:

    ``1,234.56``  comma groups of three plus a period → period is the decimal point.
    ``1.234,56``  period groups of three plus a comma → comma is the decimal point.
    ``1,50``      a comma with fewer than three following digits cannot be a
                  thousands group, so it is a decimal comma.
    ``1,234``     genuinely ambiguous: 1234 under one convention, 1.234 under the
                  other, a 1000x difference. Rejected rather than guessed.
    """
    has_comma, has_dot = "," in text, "." in text

    if has_comma and has_dot:
        # Whichever appears last is the decimal point; the other groups digits.
        if text.rindex(",") > text.rindex("."):
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")

    if not has_comma:
        return text

    # Comma only. A three-digit tail is a thousands group; anything else is decimal.
    tail = text.rsplit(",", 1)[1]
    if len(tail) == 3 and tail.isdigit():
        if _GROUPED.match(text):
            raise ValueError_(
                f"'{raw}' is ambiguous: '{text}' could be "
                f"{text.replace(',', '')} with a thousands separator or "
                f"{text.replace(',', '.')} with a decimal comma, a 1000x difference. "
                "Write it without separators, or declare the profile's locale.",
                field=field, raw=raw,
            )
        return text.replace(",", "")
    return text.replace(",", ".")


class ValueError_(ValueError):
    """A value that cannot be represented canonically.

    Carries the field so the caller can reject one cell rather than one file.
    """

    def __init__(self, message: str, *, field: str = "", raw: Any = None):
        super().__init__(message)
        self.message = message
        self.field = field
        self.raw = raw


@dataclass(frozen=True, slots=True)
class TypedValue:
    """A value in its canonical, storable form.

    ``text`` always populated (so a text search over conditions needs no per-type
    branching); ``numeric`` populated for NUMBER/MONEY; ``elements`` for LIST/RANGE.
    """

    value_type: str
    text: str
    numeric: Decimal | None = None
    elements: tuple[Any, ...] = ()
    currency_code: str | None = None
    unit_code: str | None = None
    #: Set for REFERENCE values once the resolver has run.
    resolved_ref_id: str | None = None

    def with_ref(self, ref_id: str | None) -> TypedValue:
        return TypedValue(
            self.value_type, self.text, self.numeric, self.elements,
            self.currency_code, self.unit_code, ref_id,
        )


# --- Primitive parsers ------------------------------------------------------


def parse_decimal(raw: Any, *, field: str = "") -> Decimal:
    """Parse to Decimal without ever routing through float."""
    if isinstance(raw, Decimal):
        candidate = raw
    elif isinstance(raw, bool):
        # bool is an int subclass; a boolean silently becoming 1 hides a mapping bug.
        raise ValueError_(f"'{raw}' is a yes/no value, not a number.", field=field, raw=raw)
    elif isinstance(raw, int):
        candidate = Decimal(raw)
    elif isinstance(raw, float):
        # Reachable only from a JSON source, where the float already happened
        # upstream. str() first, so we inherit repr's shortest round-trip rather
        # than the full binary expansion.
        candidate = Decimal(str(raw))
    else:
        text = str(raw).strip().translate(_STRIP)
        if not text:
            raise ValueError_("A number is required.", field=field, raw=raw)
        if text.endswith("%"):
            text = text[:-1]
        text = _normalise_numeric(text, field=field, raw=raw)
        try:
            candidate = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError_(f"'{raw}' is not a number.", field=field, raw=raw) from exc

    if not candidate.is_finite():
        raise ValueError_(f"'{raw}' is not a finite number.", field=field, raw=raw)
    return candidate


def quantize(value: Decimal, *, field: str = "", raw: Any = None) -> Decimal:
    """Fit a Decimal to numeric(20,6), refusing to lose significance silently."""
    with localcontext() as ctx:
        ctx.prec = 40
        rescaled = value.quantize(SCALE)
        if rescaled != value:
            raise ValueError_(
                f"'{raw if raw is not None else value}' has more precision than six "
                "decimal places, which this system stores exactly. Round it at source "
                "so the stored value and the vendor's document agree.",
                field=field, raw=raw,
            )
    if abs(rescaled) >= Decimal(10) ** MAX_INTEGER_DIGITS:
        raise ValueError_(
            f"'{raw if raw is not None else value}' is too large to store "
            f"(limit {MAX_INTEGER_DIGITS} integer digits).",
            field=field, raw=raw,
        )
    return rescaled


def parse_bool(raw: Any, *, field: str = "") -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().upper()
    if token in TRUTHY:
        return True
    if token in FALSY:
        return False
    raise ValueError_(f"'{raw}' is not a yes/no value.", field=field, raw=raw)


def parse_date(raw: Any, *, field: str = "") -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        raise ValueError_("A date is required.", field=field, raw=raw)
    # Excel hands back a full datetime for a date cell.
    head = text.split(" ")[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    raise ValueError_(
        f"'{raw}' is not a recognised date (try YYYY-MM-DD).", field=field, raw=raw
    )


def parse_datetime(raw: Any, *, field: str = "", timezone: str | None = None) -> datetime:
    """Parse to an aware UTC datetime.

    A naive timestamp takes the **source system's** declared timezone, never the
    server's. A vendor in Europe/London exporting 00:30 on the clock-change night
    is not the same instant as 00:30 wherever this process happens to run, and
    assuming otherwise misplaces an hour of usage twice a year.
    """
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw).strip()
        if not text:
            raise ValueError_("A timestamp is required.", field=field, raw=raw)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.combine(parse_date(text, field=field), datetime.min.time())

    if parsed.tzinfo is None:
        zone = UTC
        if timezone:
            try:
                zone = ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError_(
                    f"'{timezone}' is not a known timezone.", field=field, raw=raw
                ) from exc
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


def format_decimal(value: Decimal) -> str:
    """Render a Decimal for the canonical text column.

    ``normalize()`` renders 60 as ``6E+1`` — correct, and useless in a column a
    human reads. Trailing zeros are trimmed without ever entering exponent form.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# --- The codec --------------------------------------------------------------


def encode(
    raw: Any,
    value_type: str,
    *,
    field: str = "",
    currency: str | None = None,
    unit: str | None = None,
    allowed: tuple[str, ...] = (),
    timezone: str | None = None,
    money_scale: Decimal | int | None = None,
) -> TypedValue:
    """Turn a raw token into its canonical stored form.

    ``money_scale`` divides the incoming amount: Oracle BRM ships amounts in
    hundred-thousandths, so the profile declares ``100000`` and the scaling happens
    once, here, rather than in each adapter with its own rounding.
    """
    if value_type in NUMERIC_VALUE_TYPES:
        number = parse_decimal(raw, field=field)
        if money_scale:
            number = number / parse_decimal(money_scale, field=field)
        number = quantize(number, field=field, raw=raw)
        resolved_unit, _ = resolve_unit(unit)
        if unit and resolved_unit is None:
            raise ValueError_(
                f"'{unit}' is not a unit this system knows. Guessing would risk a "
                "60x or 1024x error, so the row is rejected instead.",
                field=field, raw=unit,
            )
        code = _require_currency(value_type, currency, field=field, raw=raw)
        return TypedValue(
            value_type, format_decimal(number), number,
            currency_code=code, unit_code=resolved_unit,
        )

    if value_type == ValueType.BOOLEAN:
        flag = parse_bool(raw, field=field)
        return TypedValue(value_type, "true" if flag else "false")

    if value_type == ValueType.DATE:
        return TypedValue(value_type, parse_date(raw, field=field).isoformat())

    if value_type == ValueType.DATETIME:
        moment = parse_datetime(raw, field=field, timezone=timezone)
        return TypedValue(value_type, moment.isoformat())

    if value_type == ValueType.ENUM:
        token = str(raw).strip().upper()
        if not token:
            raise ValueError_("A value is required.", field=field, raw=raw)
        if allowed and token not in allowed:
            raise ValueError_(
                f"'{raw}' is not one of: {', '.join(allowed)}.", field=field, raw=raw
            )
        return TypedValue(value_type, token)

    if value_type == ValueType.REFERENCE:
        token = str(raw).strip().upper()
        if not token:
            raise ValueError_("A catalogue code is required.", field=field, raw=raw)
        return TypedValue(value_type, token)

    if value_type == ValueType.STRING:
        token = str(raw).strip()
        if not token:
            raise ValueError_("A value is required.", field=field, raw=raw)
        return TypedValue(value_type, token)

    raise ValueError_(f"'{value_type}' is not a storable value type.", field=field, raw=raw)


def encode_list(
    raws: list[Any],
    element_type: str,
    *,
    field: str = "",
    allowed: tuple[str, ...] = (),
    unit: str | None = None,
    currency: str | None = None,
    timezone: str | None = None,
) -> TypedValue:
    """Encode IN / NOT_IN values. Elements are all of the attribute's own type."""
    if not raws:
        raise ValueError_("A set membership test needs at least one value.", field=field)
    encoded = [
        encode(raw, element_type, field=f"{field}[{i}]", allowed=allowed,
               unit=unit, currency=currency, timezone=timezone)
        for i, raw in enumerate(raws)
    ]
    elements = tuple(_element(e) for e in encoded)
    duplicates = len(elements) - len(set(map(str, elements)))
    if duplicates:
        # Not an error: a vendor listing a zone twice is untidy, not wrong. But the
        # stored set is deduplicated so specificity scoring is not inflated by it.
        seen: dict[str, Any] = {}
        for element in elements:
            seen.setdefault(str(element), element)
        elements = tuple(seen.values())
    first = encoded[0]
    return TypedValue(
        ValueType.LIST,
        ", ".join(str(e) for e in elements),
        elements=elements,
        currency_code=first.currency_code,
        unit_code=first.unit_code,
    )


def encode_range(
    low: Any,
    high: Any,
    element_type: str,
    *,
    field: str = "",
    unit: str | None = None,
    currency: str | None = None,
    timezone: str | None = None,
) -> TypedValue:
    """Encode a BETWEEN pair, ordered."""
    lo = encode(low, element_type, field=f"{field}[0]", unit=unit, currency=currency,
                timezone=timezone)
    hi = encode(high, element_type, field=f"{field}[1]", unit=unit, currency=currency,
                timezone=timezone)
    if lo.numeric is not None and hi.numeric is not None and lo.numeric > hi.numeric:
        raise ValueError_(
            f"BETWEEN lower bound ({lo.text}) is greater than the upper bound "
            f"({hi.text}).",
            field=field,
        )
    if lo.numeric is None and lo.text > hi.text:
        raise ValueError_(
            f"BETWEEN bounds are reversed ({lo.text} > {hi.text}).", field=field
        )
    return TypedValue(
        ValueType.RANGE,
        f"{lo.text} .. {hi.text}",
        elements=(_element(lo), _element(hi)),
        currency_code=lo.currency_code,
        unit_code=lo.unit_code,
    )


def decode(stored: TypedValue) -> Any:
    """Return the Python value a stored TypedValue represents.

    Used by the round-trip test and by the projection. Money comes back as
    ``Decimal``, never ``float``.
    """
    match stored.value_type:
        case ValueType.NUMBER | ValueType.MONEY:
            return stored.numeric
        case ValueType.BOOLEAN:
            return stored.text == "true"
        case ValueType.DATE:
            return date.fromisoformat(stored.text)
        case ValueType.DATETIME:
            return datetime.fromisoformat(stored.text)
        case ValueType.LIST | ValueType.RANGE:
            return list(stored.elements)
        case _:
            return stored.text


# --- Internals --------------------------------------------------------------


def _require_currency(
    value_type: str, currency: str | None, *, field: str, raw: Any
) -> str | None:
    if value_type not in CURRENCY_BEARING_TYPES:
        return None
    code = (currency or "").strip().upper()
    if not code:
        raise ValueError_(
            "An amount of money needs a currency. Without one it cannot be summed, "
            "compared, or put on an invoice.",
            field=field, raw=raw,
        )
    if len(code) != 3 or not code.isalpha():
        raise ValueError_(
            f"'{currency}' is not a three-letter currency code.", field=field, raw=raw
        )
    return code


def _element(value: TypedValue) -> Any:
    """JSON-storable form of one list/range element.

    Decimals are stored as strings inside JSONB. ``json`` would otherwise render
    them via float and undo the whole point of the codec.
    """
    if value.numeric is not None:
        return format_decimal(value.numeric)
    return value.text
