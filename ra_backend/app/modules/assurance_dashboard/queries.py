"""SQL fragments shared by the dashboard's KPI and chart queries.

Everything the dashboard reads comes out of the reconciliation engine's own
output tables, whose shape is decided per rule rather than declared anywhere.
So the fragments here are BUILT from a rule's definition and from the output
table's real columns, never from a column list written by hand.

Three facts about that output drive the whole file:

1. **An output table holds several executions.** The engine keeps the last few
   runs in one table, so anything unscoped counts a rule's history as though it
   were today's volume — ``recon_ca910`` is 560 rows for a run that produced
   122. Every fragment here is scoped to ``last_execution_id``.

2. **The columns are text, and only partly castable.** The AIR/SDP feeds land
   amounts as text, blank on whichever side of a missing-record row is absent,
   so a bare ``::numeric`` fails on real rows. Casts are regex-guarded.

3. **A "metric" is not necessarily money.** A rule's metric pair is whatever
   two columns its author chose to compare — MSISDNs in one rule, IMSIs in
   another. Summing those would report a phone number as revenue, so only
   metrics that name an amount count toward exposure.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.reconciliation.plan import (
    STATUS_DUPLICATE,
    STATUS_GAP,
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_PRESENT,
    STATUS_TABLE1_MISSING,
    STATUS_TABLE2_MISSING,
)

#: Statuses that mean the record was fine. MATCH is a reconciliation's clean
#: state, PRESENT a sequence check's. Everything else is an exception.
CLEAN_STATUSES = (STATUS_MATCH, STATUS_PRESENT)

#: Columns every output table carries regardless of the rule. Whatever is left
#: after removing these is the rule's own comparison columns, in the order the
#: compiler wrote them: key pairs first, then metric pairs.
RESERVED_COLUMNS = ("status", "execution_time", "rule_id", "execution_id", "remarks")

#: Postgres regex for "this text is a number".
NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?$"

#: Postgres regex for "this text starts with an ISO date".
DATE_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"

#: Name fragments that mark a column as carrying money. A rule's metric pair is
#: only summed into revenue at risk when BOTH sides name an amount — the
#: alternative is reporting the sum of 207k MSISDNs as ₹4.9 lakh crore, which
#: is what an unfiltered version of this did.
_MONEY_HINTS = (
    "amt", "amount", "charge", "price", "revenue", "fee", "cost",
    "billed", "owed", "tariff", "balance", "debit", "credit",
)

#: Name fragments that make a column a plausible event timestamp. Used to pick
#: the column a daily series is bucketed on; the DATE_RE guard means a bad pick
#: yields NULL rather than a wrong bucket.
_DATE_HINTS = ("time", "date", "stamp")


def quote(identifier: str) -> str:
    """Quote an identifier. These come from our own definition rows rather than
    from a request, but they are still interpolated into SQL, so they are quoted
    rather than trusted."""
    return '"' + identifier.replace('"', '""') + '"'


def literal(value: str) -> str:
    """Quote a string literal for interpolation into a built statement."""
    return "'" + str(value).replace("'", "''") + "'"


def looks_monetary(column: str) -> bool:
    return any(hint in column.lower() for hint in _MONEY_HINTS)


def parse_json(value: Any) -> Any:
    """Definition columns arrive as JSON text from some drivers and as parsed
    structures from others."""
    if isinstance(value, str):
        return json.loads(value or "null")
    return value


def metric_pairs(definition: dict[str, Any]) -> list[tuple[str, str]]:
    metrics = parse_json(definition.get("metrics")) or []
    pairs = []
    for metric in metrics:
        left, right = metric.get("outputLeft"), metric.get("outputRight")
        if left and right:
            pairs.append((left, right))
    return pairs


def amount_columns(
    definition: dict[str, Any], available: list[str] | None = None
) -> tuple[str, str] | None:
    """The pair of columns this rule compared, when that pair is an amount.

    Returns None for a rule that compares no metric at all (a sequence or
    duplicate check) and for one whose metric is not money — both get no
    revenue figure rather than a number that would be read as rupees.
    """
    for left, right in metric_pairs(definition):
        if not (looks_monetary(left) and looks_monetary(right)):
            continue
        if available is not None and not (left in available and right in available):
            continue
        return left, right
    return None


def numeric(column: str) -> str:
    """A column read as a number, or NULL where its text is not one."""
    q = quote(column)
    return f"CASE WHEN {q}::text ~ '{NUMERIC_RE}' THEN {q}::text::numeric END"


def exposure_sql(pair: tuple[str, str] | None) -> str:
    """What an exception row puts at risk.

    A MISMATCH risks the DIFFERENCE, since both sides billed something; a
    missing record risks the WHOLE amount, on whichever side it exists; a
    matched record risks nothing.
    """
    if not pair:
        # No monetary metric: the rows still count toward record and exception
        # totals, they just contribute nothing to revenue at risk.
        return "NULL::numeric"
    left, right = (numeric(column) for column in pair)
    return f"""CASE
        WHEN status IN ({", ".join(literal(s) for s in CLEAN_STATUSES)}) THEN 0
        WHEN {left} IS NOT NULL AND {right} IS NOT NULL
            THEN abs(coalesce({left}, 0) - coalesce({right}, 0))
        ELSE coalesce({left}, {right}, 0)
    END"""


def event_date_sql(columns: list[str]) -> str:
    """The day an output row belongs to, from whichever of its columns is a date.

    A reconciliation's output carries its comparison columns and nothing else,
    so the event date is only available when the rule happened to join on one —
    which the AIR rules do. Rules that did not get NULL, and drop out of the
    daily series rather than landing in a made-up bucket.
    """
    candidates = [c for c in columns if any(hint in c.lower() for hint in _DATE_HINTS)]
    if not candidates:
        return "NULL::text"
    guarded = [
        f"CASE WHEN {quote(c)}::text ~ '{DATE_RE}' THEN substr({quote(c)}::text, 1, 10) END"
        for c in candidates
    ]
    return f"coalesce({', '.join(guarded)})"


def subject_sql(key_columns: list[str]) -> str:
    """A short identity for one output row — the first key pair, whichever side
    of it is populated. Used to name a finding in the executive table."""
    if not key_columns:
        return "NULL::text"
    sides = [f"nullif({quote(c)}::text, '')" for c in key_columns[:2]]
    return f"coalesce({', '.join(sides)})" if len(sides) > 1 else sides[0]


def key_columns(definition: dict[str, Any], columns: list[str]) -> list[str]:
    """The output columns that identify a record rather than measure it.

    The compiler writes key pairs first and metric pairs last, with no marker
    between them, so the boundary is found by counting back from the end.
    """
    comparison = [c for c in columns if c not in RESERVED_COLUMNS]
    metric_width = 2 * len(metric_pairs(definition))
    return comparison[:-metric_width] if metric_width else comparison


# --- Human-readable labels ---------------------------------------------------


def short_table(qualified: str | None) -> str:
    """`air_schema.air_processed` -> `air_processed`."""
    return (qualified or "").split(".")[-1] or "source"


def status_phrase(status: str, left_table: str, right_table: str) -> str:
    """What a status means in this rule's terms, for a chart label.

    TABLE1_MISSING means the key was not found in table 1 — so the leakage is
    named after the table the record is missing FROM, which is the system that
    would have to be corrected.
    """
    return {
        STATUS_MISMATCH: "Amount mismatch",
        STATUS_TABLE1_MISSING: f"Not found in {left_table}",
        STATUS_TABLE2_MISSING: f"Not found in {right_table}",
        STATUS_GAP: "Sequence gap",
        STATUS_DUPLICATE: "Duplicate record",
    }.get(status, status.replace("_", " ").capitalize())


def rule_label(definition: dict[str, Any]) -> str:
    """A rule's title as a chart label. Authored titles are snake_cased about as
    often as not (`AIR_Proc_File_Seq`), which reads badly in a legend."""
    title = (definition.get("report_title") or definition.get("rule_id") or "").strip()
    return title.replace("_", " ") if title else str(definition.get("rule_id"))
