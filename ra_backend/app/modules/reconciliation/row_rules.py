"""Row-level single-table rules: Duplicate and Threshold.

Both answer "which ROWS of this table are wrong", so both report every column of
the source row plus a status — unlike Sequence, which reports a folded series,
and unlike Reconciliation, which reports a pair of sides.

    Duplicate   repeated values of one attribute; the first occurrence is the
                ORIGINAL and is NOT reported, every later one is a DUPLICATE.
    Threshold   rows where one attribute fails a comparison, reported as
                THRESHOLD_BREACH.

Everything is one INSERT ... SELECT against the source table. No source row is
ever read into the API, which is what lets these run over tables of millions of
rows.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationFailedError
from app.modules.reconciliation.plan import (
    STATUS_DUPLICATE,
    STATUS_THRESHOLD_BREACH,
    SYSTEM_COLUMNS,
    Column,
    ReconPlan,
    RowRuleOptions,
    TableRef,
)

# Comparison operators the author may choose, mapped to SQL. A whitelist, so the
# operator can be interpolated: nothing outside this table ever reaches the SQL.
OPERATORS: dict[str, str] = {
    "<": "<",
    ">": ">",
    "=": "=",
    "<=": "<=",
    ">=": ">=",
    "!=": "<>",
}

#: Attributes whose name ends with this are treated as timestamps, per the
#: rule's specification — for those the earliest row of a repeated value is the
#: ORIGINAL. Every partition shares one value, so "earliest" is decided by the
#: tiebreak column below.
_TIMESTAMP_SUFFIX = "_timestamp"


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def qualified(schema: str, table: str) -> str:
    return f"{q(schema)}.{q(table)}"


def is_timestamp_attribute(column: Column) -> bool:
    """Timestamp by NAME as the rule specifies, or by type — a column actually
    typed as a timestamp is one whatever it is called."""
    return column.name.lower().endswith(_TIMESTAMP_SUFFIX) or column.data_type.lower().startswith(
        "timestamp"
    )


#: What a Duplicate or Threshold report carries, besides the status.
#:
#: Deliberately narrow. These reports identify FILES, and a file is identified
#: by its name, the batch it arrived in and when it was produced — the other
#: fifty-odd columns of a file log are noise on a screen whose job is "which
#: files are wrong".
REPORT_COLUMNS = ("filename", "batch_id", "file_timestamp")


def report_source_columns(options: RowRuleOptions) -> list[Column]:
    """The source columns this report projects, in REPORT_COLUMNS order.

    Only the ones the source table actually has: not every table carries all
    three, and a rule over one that carries none would otherwise produce a
    report of nothing but a status. In that case the checked attribute stands
    in, so the row is still identifiable.
    """
    by_name = {c.name: c for c in options.source_columns}
    chosen = [by_name[name] for name in REPORT_COLUMNS if name in by_name]
    if chosen:
        return chosen
    fallback = by_name.get(options.attribute)
    return [fallback] if fallback else list(options.source_columns[:1])


def output_columns(options: RowRuleOptions) -> list[str]:
    """The output table's business columns.

    A source column whose name collides with an appended system column is
    suffixed rather than dropped.
    """
    reserved = set(SYSTEM_COLUMNS)
    names: list[str] = []
    for column in report_source_columns(options):
        name = column.name
        if name in reserved or name in names:
            name = f"{name}_src"
        names.append(name)
    return names


def build_ddl(plan: ReconPlan) -> str:
    """Output table: every source column with its own type, then the system
    columns. Types are copied so a value cannot be truncated or re-rounded on
    the way in."""
    options = _options(plan)
    lines = [
        f"    {q(alias)} {column.data_type}"
        for alias, column in zip(output_columns(options), report_source_columns(options))
    ]
    lines += [
        '    "status" text NOT NULL',
        '    "execution_time" timestamptz NOT NULL',
        '    "rule_id" text NOT NULL',
        '    "execution_id" uuid NOT NULL',
        '    "remarks" text',
    ]
    return (
        f"CREATE TABLE IF NOT EXISTS {qualified(plan.output_schema, plan.output_table)} (\n"
        + ",\n".join(lines)
        + "\n);"
    )


def _options(plan: ReconPlan) -> RowRuleOptions:
    if plan.row_rule is None:  # pragma: no cover - the compiler guarantees this
        raise ValidationFailedError(f"{plan.kind} plan has no row-rule options.")
    return plan.row_rule


def _tiebreak(options: RowRuleOptions) -> str:
    """What decides which row of a repeated value came first.

    Every row in a duplicate partition holds the SAME value, so ordering by the
    attribute alone cannot pick one — some stable second key is required or
    "first occurrence" is whatever the planner happened to return. A real
    ordering column is used when the table has one; ctid is the fallback, which
    is stable within a single statement (which is all this needs).
    """
    return q(options.order_column) if options.order_column else "ctid"


def build_duplicate_insert(plan: ReconPlan) -> str:
    """Every row after the first for each repeated value.

    row_number() over the attribute (plus any partition) ranks the rows sharing
    a value; rank 1 is the ORIGINAL and is filtered out, so the report holds
    only DUPLICATE rows as specified.

    NULL is not a duplicate of NULL here — a row with no value is not a repeat
    of another row with no value — so those are excluded.
    """
    options = _options(plan)
    source = qualified(plan.left.schema, plan.left.table)
    aliases = output_columns(options)

    partition_by = ", ".join(
        [f"s.{q(options.attribute)}"]
        + ([f"s.{q(options.partition_column)}"] if options.partition_column else [])
    )

    projected = ", ".join(f"ranked.{q(c.name)}" for c in report_source_columns(options))
    target = ", ".join(q(a) for a in aliases)

    return f"""INSERT INTO {qualified(plan.output_schema, plan.output_table)} (
            {target}, "status", "execution_time", "rule_id", "execution_id", "remarks"
        )
        SELECT {projected},
               '{STATUS_DUPLICATE}' AS status,
               :execution_time AS execution_time,
               :rule_id AS rule_id,
               CAST(:execution_id AS uuid) AS execution_id,
               'occurrence ' || ranked.__rn || ' of ' || ranked.__total AS remarks
        FROM (
            SELECT s.*,
                   row_number() OVER (
                       PARTITION BY {partition_by} ORDER BY {_tiebreak(options)}
                   ) AS __rn,
                   count(*) OVER (PARTITION BY {partition_by}) AS __total
            FROM {source} s
            WHERE s.{q(options.attribute)} IS NOT NULL
        ) AS ranked
        WHERE ranked.__rn > 1"""


def threshold_status(plan: ReconPlan) -> str:
    """What a breaching row is labelled.

    The rule's own name, so a rule called Zero_KB marks its rows "Zero_KB"
    rather than a generic THRESHOLD_BREACH that says nothing about which check
    produced them. Falls back to the constant when a rule has no usable name.

    Quotes are doubled because this ends up inside a SQL string literal.
    """
    label = (plan.rule_name or "").strip()
    return (label or STATUS_THRESHOLD_BREACH).replace("'", "''")[:64]


def build_threshold_insert(plan: ReconPlan) -> str:
    """Rows failing the configured comparison.

    The operator comes from OPERATORS, so it is one of six known strings. The
    VALUE is bound, never interpolated, and cast to the attribute's own type so
    a numeric column compares numerically rather than as text.
    """
    options = _options(plan)
    operator = OPERATORS.get(options.operator or "")
    if operator is None:
        raise ValidationFailedError(
            f"'{options.operator}' is not a comparison operator.",
            details={"allowed": sorted(OPERATORS)},
        )

    source = qualified(plan.left.schema, plan.left.table)
    attribute = next(c for c in options.source_columns if c.name == options.attribute)
    projected = ", ".join(f"s.{q(c.name)}" for c in report_source_columns(options))
    target = ", ".join(q(a) for a in output_columns(options))

    return f"""INSERT INTO {qualified(plan.output_schema, plan.output_table)} (
            {target}, "status", "execution_time", "rule_id", "execution_id", "remarks"
        )
        SELECT {projected},
               '{threshold_status(plan)}' AS status,
               :execution_time AS execution_time,
               :rule_id AS rule_id,
               CAST(:execution_id AS uuid) AS execution_id,
               '{options.attribute} {options.operator} {_escape(options.value)}' AS remarks
        FROM {source} s
        WHERE s.{q(options.attribute)} {operator} CAST(:threshold_value AS {attribute.data_type})"""


def build_insert(plan: ReconPlan) -> str:
    from app.modules.reconciliation.plan import KIND_DUPLICATE

    return (
        build_duplicate_insert(plan)
        if plan.kind == KIND_DUPLICATE
        else build_threshold_insert(plan)
    )


def build_scanned_count(plan: ReconPlan) -> str:
    """Rows the rule looked at — the source table's size.

    Counted rather than estimated because "rows scanned" is reported as fact on
    the execution summary. count(*) on a large table is a sequential scan, which
    is why it is a separate statement the caller can skip.
    """
    return f"SELECT count(*) AS n FROM {qualified(plan.left.schema, plan.left.table)}"


def bind_params(plan: ReconPlan) -> dict[str, Any]:
    """Extra bind parameters this kind's INSERT needs."""
    from app.modules.reconciliation.plan import KIND_THRESHOLD

    if plan.kind == KIND_THRESHOLD:
        return {"threshold_value": _options(plan).value}
    return {}


def _escape(value: str | None) -> str:
    return str(value or "").replace("'", "''")
