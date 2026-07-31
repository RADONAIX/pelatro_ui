"""Canonical value types — the declared type of every stored comparison,
action value and parameter.

Why declare it at all, when the legacy model stored a bare JSONB array: the
engine currently re-infers "is this a number?" from the value itself on every
CDR. Declaring the type once at authoring time makes the inference a lookup,
lets Postgres hold a typed shadow column the reports can aggregate, and — the
reason that actually matters — separates ``MONEY`` from ``NUMBER`` so an amount
without a currency cannot be stored.
"""

from __future__ import annotations

from enum import StrEnum


class ValueType(StrEnum):
    STRING = "STRING"
    NUMBER = "NUMBER"
    #: A NUMBER that is an amount of money. Requires a currency; stored in a
    #: numeric(20,6) shadow column. Never a float, anywhere, ever.
    MONEY = "MONEY"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"
    #: Names a catalogue entity. The *code* is stored (so an export moves between
    #: environments unchanged) and the resolved id alongside it (so joins work).
    REFERENCE = "REFERENCE"
    DATE = "DATE"
    DATETIME = "DATETIME"
    #: Ordered elements of the attribute's own type — IN / NOT_IN.
    LIST = "LIST"
    #: Exactly two ordered elements — BETWEEN.
    RANGE = "RANGE"


#: Types whose canonical form also populates a ``*_value_numeric`` column, so
#: aggregate reporting never has to cast text.
NUMERIC_VALUE_TYPES: frozenset[str] = frozenset({ValueType.NUMBER, ValueType.MONEY})

#: Types that carry a currency. Enforced: MONEY without a currency is rejected.
CURRENCY_BEARING_TYPES: frozenset[str] = frozenset({ValueType.MONEY})

#: Types that may carry a unit of measure.
UNIT_BEARING_TYPES: frozenset[str] = frozenset({ValueType.NUMBER, ValueType.MONEY})


class UnitDimension(StrEnum):
    """What a unit measures. A rate's unit must match its dimension — charging
    per MEGABYTE for a voice call is a modelling error, not a preference."""

    TIME = "TIME"
    VOLUME = "VOLUME"
    EVENT = "EVENT"
    MESSAGE = "MESSAGE"
    CURRENCY = "CURRENCY"
