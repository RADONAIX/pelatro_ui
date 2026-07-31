"""Single-table rules: Sequence and Duplicate.

A Sequence rule asks "did every file arrive". Given a file log, an attribute
carrying a counter, and what that counter runs within, it reports every observed
value and flags where the series jumps:

    AIROUTPUTCDR_..._0000_...   PRESENT
    AIROUTPUTCDR_..._0001_...   GAP        (0002 missing, 1 file)
    AIROUTPUTCDR_..._0003_...   PRESENT

The counter is rarely a column of its own — it is buried in a filename among
other digit runs (a stream id, a node number, a date, a time). Which run is the
counter is inferred HERE, at compile time, by sampling the real values: mask
each digit run in turn and the counter is the one whose masking collapses the
most values onto the same template. That inference is then frozen into the
stored SQL, so a scheduled run cannot silently re-infer differently as the data
grows.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.integrations import ra_postgres
from app.modules.reconciliation.plan import SequenceOptions, TableRef

log = get_logger("recon.sequence")

#: How many digit runs to consider. Filenames here have five; ten is slack.
_MAX_GROUPS = 10

#: Rows sampled to infer the counter's position.
_SAMPLE_ROWS = 2000

_PURE_INT = re.compile(r"^\s*\d+\s*$")


def group_regex(index: int) -> str:
    """Postgres regex capturing the (index+1)-th digit run."""
    return rf"^(?:\D*\d+){{{index}}}\D*(\d+)"


def template_regex(index: int) -> tuple[str, str]:
    """Pattern + replacement that mask the (index+1)-th digit run with '#'."""
    return rf"^((?:\D*\d+){{{index}}}\D*)(\d+)", r"\1#"


async def infer_group_index(ref: TableRef, column: str) -> int | None:
    """Which digit run inside `column` is the counter.

    Returns None when the values are already plain integers (a
    file_sequence_number column), in which case nothing needs extracting.

    The signal is strong and cheap: for each candidate run, count how many
    distinct templates remain once it is masked. Masking the counter collapses a
    whole series onto one template; masking anything else changes nothing. The
    fewest templates wins, ties broken by the run with the most distinct values.
    """
    sample = await ra_postgres.query_database(
        ref.database,
        f'SELECT {_q(column)}::text AS v FROM {_q(ref.schema)}.{_q(ref.table)} '
        f"WHERE {_q(column)} IS NOT NULL LIMIT {_SAMPLE_ROWS}",
    )
    values = [r["v"] for r in sample if r["v"] is not None]
    if not values:
        raise ValidationFailedError(
            f"{ref.qualified}.{column} has no values to derive a sequence from."
        )

    if all(_PURE_INT.match(v) for v in values):
        return None

    stats = await ra_postgres.query_database(
        ref.database,
        f"""
        WITH s AS (
            SELECT {_q(column)}::text AS v
            FROM {_q(ref.schema)}.{_q(ref.table)}
            WHERE {_q(column)} IS NOT NULL
            LIMIT {_SAMPLE_ROWS}
        )
        SELECT k,
               count(DISTINCT regexp_replace(v, '^((?:\\D*\\d+){{' || k || '}}\\D*)(\\d+)', '\\1#')) AS templates,
               count(DISTINCT substring(v from '^(?:\\D*\\d+){{' || k || '}}\\D*(\\d+)')) AS groups
        FROM s, generate_series(0, {_MAX_GROUPS - 1}) k
        WHERE substring(v from '^(?:\\D*\\d+){{' || k || '}}\\D*(\\d+)') IS NOT NULL
        GROUP BY k
        ORDER BY templates ASC, groups DESC, k ASC
        """,
    )
    if not stats:
        raise ValidationFailedError(
            f"No number found inside {ref.qualified}.{column}, so it cannot carry "
            "a sequence. Pick an attribute whose values include a counter."
        )
    chosen = int(stats[0]["k"])
    log.info(
        "sequence_group_inferred",
        table=ref.qualified,
        column=column,
        group_index=chosen,
        templates=int(stats[0]["templates"]),
        distinct_values=int(stats[0]["groups"]),
    )
    return chosen


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sequence_expression(options: SequenceOptions) -> str:
    """SQL yielding the counter as a bigint."""
    column = f"{_q(options.column)}::text"
    if options.group_index is None:
        return f"nullif(btrim({column}), '')::bigint"
    return f"substring({column} from '{group_regex(options.group_index)}')::bigint"


def partition_expression(options: SequenceOptions) -> str:
    """SQL yielding what the counter runs within.

    An explicit partition column wins. Otherwise the value itself with the
    counter masked out — two files from the same series then share a partition
    while differing in the counter, which is exactly the grouping a sequence
    check needs.
    """
    if options.partition_column:
        return f"{_q(options.partition_column)}::text"
    if options.group_index is None:
        # A bare counter column with nothing to scope it: one global series.
        return "'(all)'"
    pattern, replacement = template_regex(options.group_index)
    return f"regexp_replace({_q(options.column)}::text, '{pattern}', '{replacement}')"


#: Output columns for a sequence rule, in report order.
SEQUENCE_COLUMNS = (
    "partition_key",
    "sequence_value",
    "next_sequence_value",
    "missing_count",
    "occurrences",
    "sample_value",
)


def build_sequence_select(ref: TableRef, options: SequenceOptions, *, duplicates_only: bool) -> str:
    """The body of a sequence/duplicate load.

    One pass to fold the raw rows into (partition, counter) with a count, a
    second (a window over the folded set) to look at the next counter in the
    same partition. Both run inside the database; nothing streams out.

    A row is DUPLICATE when the same counter appears more than once in a
    partition, GAP when the next counter is more than one away, PRESENT
    otherwise. The final counter in a partition cannot be a gap — there is
    nothing after it to be missing.
    """
    seq = sequence_expression(options)
    part = partition_expression(options)

    folded = f"""
        SELECT {part} AS partition_key,
               {seq} AS sequence_value,
               count(*) AS occurrences,
               min({_q(options.column)}::text) AS sample_value
        FROM {_q(ref.schema)}.{_q(ref.table)}
        WHERE {seq} IS NOT NULL
        GROUP BY 1, 2
    """

    windowed = f"""
        SELECT partition_key, sequence_value, occurrences, sample_value,
               lead(sequence_value) OVER (
                   PARTITION BY partition_key ORDER BY sequence_value
               ) AS next_sequence_value
        FROM ({folded}) AS folded
    """

    status = """CASE
                WHEN occurrences > 1 THEN 'DUPLICATE'
                WHEN next_sequence_value IS NULL THEN 'PRESENT'
                WHEN next_sequence_value = sequence_value + 1 THEN 'PRESENT'
                ELSE 'GAP'
            END"""

    remarks = """CASE
                WHEN occurrences > 1
                    THEN occurrences || ' files carry this number'
                WHEN next_sequence_value IS NOT NULL
                     AND next_sequence_value > sequence_value + 1
                    THEN 'missing ' || (sequence_value + 1)
                         || CASE WHEN next_sequence_value - sequence_value > 2
                                 THEN '-' || (next_sequence_value - 1) ELSE '' END
                ELSE NULL
            END"""

    # A Duplicate rule reports only the offending rows; a Sequence rule reports
    # the whole series, so a clean run is visibly clean rather than empty.
    having = "WHERE occurrences > 1" if duplicates_only else ""

    return f"""
        SELECT partition_key,
               sequence_value,
               next_sequence_value,
               CASE
                   WHEN next_sequence_value IS NULL THEN 0
                   ELSE greatest(next_sequence_value - sequence_value - 1, 0)
               END AS missing_count,
               occurrences,
               sample_value,
               {status} AS status,
               :execution_time AS execution_time,
               :rule_id AS rule_id,
               CAST(:execution_id AS uuid) AS execution_id,
               {remarks} AS remarks
        FROM ({windowed}) AS windowed
        {having}
    """


def build_sequence_ddl(schema: str, table: str) -> str:
    """The output table. Fixed shape — unlike a reconciliation, a sequence
    always reports the same six business columns whatever it was pointed at."""
    return f"""CREATE TABLE IF NOT EXISTS {_q(schema)}.{_q(table)} (
    "partition_key" text,
    "sequence_value" bigint,
    "next_sequence_value" bigint,
    "missing_count" bigint,
    "occurrences" bigint,
    "sample_value" text,
    "status" text NOT NULL,
    "execution_time" timestamptz NOT NULL,
    "rule_id" text NOT NULL,
    "execution_id" uuid NOT NULL,
    "remarks" text
);"""


def build_sequence_insert(
    schema: str, table: str, ref: TableRef, options: SequenceOptions, *, duplicates_only: bool
) -> str:
    columns = ", ".join(
        _q(c) for c in (*SEQUENCE_COLUMNS, "status", "execution_time", "rule_id", "execution_id", "remarks")
    )
    return (
        f"INSERT INTO {_q(schema)}.{_q(table)} ({columns})\n"
        + build_sequence_select(ref, options, duplicates_only=duplicates_only)
    )


def summarise(options: SequenceOptions, ref: TableRef, kind: str) -> dict[str, Any]:
    """Human-readable description for the report entry."""
    where = (
        f"partitioned by {options.partition_column}"
        if options.partition_column
        else "partitioned by the surrounding value"
    )
    how = (
        "the value itself"
        if options.group_index is None
        else f"number group {options.group_index + 1} inside it"
    )
    verb = "Duplicate check" if kind == "duplicate" else "Sequence check"
    return {
        "description": (
            f"{verb} over {ref.qualified}.{options.column} using {how}, {where}."
        )
    }
