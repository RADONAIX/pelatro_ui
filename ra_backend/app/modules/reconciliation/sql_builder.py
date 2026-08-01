"""Dynamic SQL Generator — a ReconPlan becomes DDL and one INSERT ... SELECT.

Pure functions: no I/O, no settings, no globals. Given a plan they return
strings, which makes every generated statement inspectable in a test and in the
metadata table (recon_definition.generated_sql is exactly what ran).

Identifier handling: names come from a compiled plan, so they exist in the
catalog — but they are still quoted, because a legal Postgres column name can
be a reserved word. Values are NEVER interpolated: the execution engine binds
:execution_id / :rule_id.
"""

from __future__ import annotations

from app.modules.reconciliation import row_rules, sequence
from app.modules.reconciliation.plan import (
    KIND_DUPLICATE,
    KIND_RECONCILIATION,
    ROW_KINDS,
    PRESENCE_COLUMN,
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_TABLE1_MISSING,
    STATUS_TABLE2_MISSING,
    ReconPlan,
)


def q(identifier: str) -> str:
    """Quote one identifier. Embedded quotes are doubled — defence in depth; the
    compiler has already rejected anything that isn't [a-z_][a-z0-9_]*."""
    return '"' + identifier.replace('"', '""') + '"'


def qualified(schema: str, table: str) -> str:
    return f"{q(schema)}.{q(table)}"


# --- DDL --------------------------------------------------------------------


def build_ddl(plan: ReconPlan) -> str:
    """Dispatch on the plan's kind.

    Row-level rules (Duplicate, Threshold) mirror the source table's whole
    column list; a sequence has a fixed six-column shape; a reconciliation's is
    derived from the columns it compares.
    """
    if plan.kind in ROW_KINDS:
        return row_rules.build_ddl(plan)
    if plan.kind != KIND_RECONCILIATION:
        return sequence.build_sequence_ddl(plan.output_schema, plan.output_table)
    return _build_recon_ddl(plan)


def _build_recon_ddl(plan: ReconPlan) -> str:
    """CREATE TABLE IF NOT EXISTS for the output table.

    Column order is the spec's: Part 1 the key columns from both tables, Part 2
    the metric columns from both tables, Part 3 the system columns. Each side
    keeps its own column and its own source type — a processed amount and a raw
    amount never collapse into one "amount", which is the whole point of the
    output.
    """
    lines: list[str] = []
    for pair in plan.keys:
        lines.append(f"    {q(pair.output_left)} {pair.left.data_type}")
        lines.append(f"    {q(pair.output_right)} {pair.right.data_type}")
    for pair in plan.metrics:
        lines.append(f"    {q(pair.output_left)} {pair.left.data_type}")
        lines.append(f"    {q(pair.output_right)} {pair.right.data_type}")

    lines.append("    \"status\" text NOT NULL")
    lines.append("    \"execution_time\" timestamptz NOT NULL")
    lines.append("    \"rule_id\" text NOT NULL")
    lines.append("    \"execution_id\" uuid NOT NULL")
    lines.append("    \"remarks\" text")

    table = qualified(plan.output_schema, plan.output_table)
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        + ",\n".join(lines)
        + "\n);"
    )


def build_indexes(plan: ReconPlan) -> list[str]:
    """Indexes the report view actually uses.

    Every read is "the latest execution of this rule, optionally one status,
    paginated" — so (execution_id, status) serves the filter and the ordering
    without a sort. Created separately from the table so a re-compile against an
    existing table still adds them.
    """
    table = qualified(plan.output_schema, plan.output_table)
    base = plan.output_table
    return [
        f"CREATE INDEX IF NOT EXISTS {q(base + '_exec_status_idx')} "
        f"ON {table} (execution_id, status);",
        f"CREATE INDEX IF NOT EXISTS {q(base + '_exec_time_idx')} "
        f"ON {table} (execution_time DESC);",
    ]


# --- Comparison predicates --------------------------------------------------


def _base_type(data_type: str) -> str:
    """Type name without precision — numeric(18,4) and numeric compare the same."""
    return data_type.split("(")[0].strip().lower()


def compare_operands(pair) -> tuple[str, str]:
    """The two sides of a comparison, cast so they can actually be compared.

    Source tables are rarely typed consistently across a raw/processed pair —
    here `air_proc_origin_time_stamp` is a timestamp while its raw counterpart
    `air_rfl_rec_origin_timestamp` is text. Postgres has no equality operator
    between those, so comparing them directly fails the whole statement with
    "operator does not exist" rather than reporting a mismatch.

    When the two types differ, both sides are compared as text: it is defined for
    every type, and it is what an analyst pairing those two columns means. Equal
    types are left untouched so numeric and timestamp comparison keep their
    natural semantics (and any index on them stays usable).
    """
    left = f"l.{q(pair.output_left)}"
    right = f"r.{q(pair.output_right)}"
    if _base_type(pair.left.data_type) != _base_type(pair.right.data_type):
        return f"{left}::text", f"{right}::text"
    return left, right


def _metric_match_predicate(plan: ReconPlan) -> str:
    """True when every selected metric agrees.

    IS NOT DISTINCT FROM is the NULL-safe equality: two NULLs agree, NULL and a
    value do not. It works for text, numeric, timestamp and everything else,
    which is why there is no per-type branching here.

    With a tolerance, numeric pairs additionally agree when the difference is
    within tolerance percent of the left value. NULLIF guards the zero-baseline
    division; a left value of 0 falls back to exact equality rather than
    dividing by zero.
    """
    predicates: list[str] = []
    for pair in plan.metrics:
        left, right = compare_operands(pair)
        exact = f"{left} IS NOT DISTINCT FROM {right}"
        if plan.tolerance_pct > 0 and pair.comparable_numerically:
            within = (
                f"({left} IS NOT NULL AND {right} IS NOT NULL AND "
                f"abs({left}::numeric - {right}::numeric) <= "
                f"abs(nullif({left}::numeric, 0)) * {plan.tolerance_pct} / 100.0)"
            )
            predicates.append(f"({exact} OR {within})")
        else:
            predicates.append(f"({exact})")
    return "\n            AND ".join(predicates)


def _status_expression(plan: ReconPlan) -> str:
    """The four-way verdict.

    Presence is tested with the join-carried marker, not with a key column: a
    row whose key is genuinely NULL would otherwise be misreported as missing.
    """
    return f"""CASE
            WHEN r.{q(PRESENCE_COLUMN)} IS NULL THEN '{STATUS_TABLE2_MISSING}'
            WHEN l.{q(PRESENCE_COLUMN)} IS NULL THEN '{STATUS_TABLE1_MISSING}'
            WHEN {_metric_match_predicate(plan)}
                THEN '{STATUS_MATCH}'
            ELSE '{STATUS_MISMATCH}'
        END"""


def _remarks_expression(plan: ReconPlan) -> str:
    """Which metrics disagreed, for the mismatch rows only.

    Computed in the same pass rather than by re-reading the output later:
    naming the offending metric is most of the triage, and a second pass over
    millions of rows to derive it would not be affordable.
    """
    parts: list[str] = []
    for pair in plan.metrics:
        left, right = compare_operands(pair)
        label = pair.left.name.replace("'", "''")
        parts.append(
            f"CASE WHEN {left} IS DISTINCT FROM {right} THEN '{label}' END"
        )
    joined = ",\n                ".join(parts)
    return f"""CASE
            WHEN r.{q(PRESENCE_COLUMN)} IS NULL OR l.{q(PRESENCE_COLUMN)} IS NULL THEN NULL
            ELSE nullif(concat_ws(', ',
                {joined}
            ), '')
        END"""


# --- DML --------------------------------------------------------------------


def _side_subquery(plan: ReconPlan, side: str) -> str:
    """One side of the join, projected to just the columns the plan uses.

    Projecting early matters: these tables are wide and only a handful of
    columns take part, so the join never carries the rest. The presence marker
    rides along here.
    """
    if side == "l":
        ref, pairs_key, pairs_metric = plan.left, plan.keys, plan.metrics
        selector = lambda pair: (pair.left.name, pair.output_left)  # noqa: E731
    else:
        ref, pairs_key, pairs_metric = plan.right, plan.keys, plan.metrics
        selector = lambda pair: (pair.right.name, pair.output_right)  # noqa: E731

    columns: list[str] = []
    for pair in list(pairs_key) + list(pairs_metric):
        source, alias = selector(pair)
        columns.append(f"{q(source)} AS {q(alias)}")
    columns.append(f"1 AS {q(PRESENCE_COLUMN)}")
    body = ",\n                   ".join(columns)
    return f"""(
            SELECT {body}
            FROM {qualified(ref.schema, ref.table)}
        )"""


def build_insert(plan: ReconPlan) -> str:
    """Dispatch on kind — each generator lives in its own module."""
    if plan.kind in ROW_KINDS:
        return row_rules.build_insert(plan)
    if plan.kind != KIND_RECONCILIATION:
        if plan.sequence is None:  # pragma: no cover - compiler guarantees this
            raise ValueError(f"{plan.kind} plan has no sequence options")
        return sequence.build_sequence_insert(
            plan.output_schema,
            plan.output_table,
            plan.left,
            plan.sequence,
            duplicates_only=plan.kind == KIND_DUPLICATE,
        )
    return _build_recon_insert(plan)


def _build_recon_insert(plan: ReconPlan) -> str:
    """INSERT INTO <output> SELECT ... FROM t1 FULL OUTER JOIN t2 ON <keys>.

    One statement: the whole reconciliation happens inside the database, so no
    source row ever crosses the network. That is what makes millions of rows
    affordable.

    :execution_id and :execution_time are bound by the caller — never formatted
    into the string.
    """
    # Keys join with plain "=", not the NULL-safe form: two rows whose key is
    # NULL are not the same record, and matching them would invent pairings.
    # The cast rule is the same as for metrics — a text key cannot be compared
    # with a timestamp key without one.
    on_clause = "\n            AND ".join(
        "{} = {}".format(*compare_operands(pair)) for pair in plan.keys
    )

    projected: list[str] = []
    for pair in plan.keys:
        projected.append(f"l.{q(pair.output_left)}")
        projected.append(f"r.{q(pair.output_right)}")
    for pair in plan.metrics:
        projected.append(f"l.{q(pair.output_left)}")
        projected.append(f"r.{q(pair.output_right)}")

    target_columns = [
        q(name)
        for pair in plan.keys
        for name in (pair.output_left, pair.output_right)
    ] + [
        q(name)
        for pair in plan.metrics
        for name in (pair.output_left, pair.output_right)
    ] + [q("status"), q("execution_time"), q("rule_id"), q("execution_id"), q("remarks")]

    select_list = ",\n            ".join(projected)

    return f"""INSERT INTO {qualified(plan.output_schema, plan.output_table)} (
            {", ".join(target_columns)}
        )
        SELECT
            {select_list},
            {_status_expression(plan)} AS status,
            :execution_time AS execution_time,
            :rule_id AS rule_id,
            CAST(:execution_id AS uuid) AS execution_id,
            {_remarks_expression(plan)} AS remarks
        FROM {_side_subquery(plan, 'l')} AS l
        FULL OUTER JOIN {_side_subquery(plan, 'r')} AS r
            ON {on_clause}"""


def build_summary(plan: ReconPlan) -> str:
    """Per-status counts for one execution, straight off the status index."""
    return f"""SELECT status, count(*) AS n
        FROM {qualified(plan.output_schema, plan.output_table)}
        WHERE execution_id = CAST(:execution_id AS uuid)
        GROUP BY status"""


def build_results_query(
    plan: ReconPlan, *, status: str | None, order_by: str | None = None
) -> str:
    """The report view: only the selected keys, metrics and status.

    Nothing else is projected — not the system columns, not any other source
    column — because the report is specified to show exactly what the author
    chose to compare.
    """
    columns = ", ".join(q(name) for name in plan.business_columns)
    where = "WHERE execution_id = CAST(:execution_id AS uuid)"
    if status:
        where += " AND status = :status"
    # Whitelisted against the plan's own columns — an unknown or absent value
    # orders by nothing rather than reaching the identifier quoter.
    ordering = (
        f"ORDER BY {q(order_by)}"
        if order_by is not None and order_by in plan.business_columns
        else ""
    )
    return f"""SELECT {columns}, status
        FROM {qualified(plan.output_schema, plan.output_table)}
        {where}
        {ordering}
        LIMIT :limit OFFSET :offset"""


def build_results_count(plan: ReconPlan, *, status: str | None) -> str:
    where = "WHERE execution_id = CAST(:execution_id AS uuid)"
    if status:
        where += " AND status = :status"
    return f"""SELECT count(*) AS n
        FROM {qualified(plan.output_schema, plan.output_table)}
        {where}"""


def build_prune(plan: ReconPlan) -> str:
    """Drop rows from superseded executions.

    Without this the output table grows by a full reconciliation every run. The
    retained execution ids are bound as an array, so the statement is the same
    whatever the retention setting.
    """
    return f"""DELETE FROM {qualified(plan.output_schema, plan.output_table)}
        WHERE execution_id <> ALL (CAST(:keep AS uuid[]))"""
