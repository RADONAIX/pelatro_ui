"""Rule Compiler — an authored rule becomes a validated, executable plan.

Everything a request can influence is resolved here, against the live catalog:

  * both tables must exist, and in the SAME database (the reconciliation is one
    server-side INSERT ... SELECT, which cannot span databases);
  * every key and metric column must exist on its side, with its real type;
  * the pair of tables must be inside the assurance's metadata scope, so a rule
    cannot reach a table the author could not have selected.

Downstream stages therefore never see an unchecked identifier. This is what
lets the SQL generator interpolate names into DDL/DML at all — a compiled plan
is the trust boundary.
"""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.errors import ValidationFailedError
from app.core.logging import get_logger
from app.integrations import ra_postgres
from app.modules.meta import metadata_catalog
from app.modules.reconciliation import sequence
from app.modules.reconciliation.plan import (
    Column,
    ColumnPair,
    ReconPlan,
    SequenceOptions,
    TableRef,
)

log = get_logger("recon.compiler")

# Postgres types that support arithmetic, so a percentage tolerance is meaningful.
_NUMERIC_TYPES = {
    "smallint", "integer", "bigint", "decimal", "numeric", "real",
    "double precision", "money", "smallserial", "serial", "bigserial",
}

# A safe unquoted SQL identifier. Generated names are built to satisfy this and
# re-checked before they reach the SQL generator.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Postgres truncates identifiers past this, which would silently collide.
_MAX_IDENT = 63


def _is_numeric(data_type: str) -> bool:
    return data_type.split("(")[0].strip().lower() in _NUMERIC_TYPES


def parse_table_id(table_id: str) -> tuple[str | None, str, str]:
    """``"db:schema.table"`` or the legacy ``"schema.table"`` -> parts."""
    database: str | None = None
    rest = table_id
    if ":" in table_id:
        database, rest = table_id.split(":", 1)
    if rest.count(".") != 1:
        raise ValidationFailedError(
            f"'{table_id}' is not a table reference. Expected database:schema.table."
        )
    schema, table = rest.split(".", 1)
    if not schema or not table:
        raise ValidationFailedError(f"'{table_id}' is missing a schema or table name.")
    return database, schema, table


async def _resolve_table(assurance: str, table_id: str) -> TableRef:
    database, schema, table = parse_table_id(table_id)
    # Raises when the table is outside this assurance's configured scope.
    database = metadata_catalog.source_for_schema(assurance, schema, database, table)
    return TableRef(database=database, schema=schema, table=table)


async def _columns_of(ref: TableRef) -> dict[str, Column]:
    """Real columns and their real types, keyed by name.

    format_type() rather than information_schema.data_type: the output table
    must be able to hold the source values exactly, and "numeric" without its
    precision would silently round money.
    """
    rows = await ra_postgres.query_database(
        ref.database,
        """
        SELECT a.attname AS name,
               format_type(a.atttypid, a.atttypmod) AS data_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema
          AND c.relname = :table
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        {"schema": ref.schema, "table": ref.table},
    )
    if not rows:
        raise ValidationFailedError(
            f"Table {ref.qualified} does not exist in {ref.database}, or has no columns."
        )
    return {
        r["name"]: Column(
            name=r["name"],
            data_type=r["data_type"],
            numeric=_is_numeric(r["data_type"]),
        )
        for r in rows
    }


def _pick(columns: dict[str, Column], name: str, side: str, ref: TableRef) -> Column:
    column = columns.get(name)
    if column is None:
        raise ValidationFailedError(
            f"Column '{name}' does not exist on the {side} table {ref.qualified}.",
            details={"table": ref.qualified, "column": name},
        )
    return column


def _output_names(pairs: list[tuple[Column, Column]], taken: set[str]) -> list[ColumnPair]:
    """Name each side's column in the output table.

    The source name is kept wherever possible — the spec's example table is
    literally the source column names, and an analyst reading the output should
    recognise them. Only a genuine collision (the same name on both sides, or a
    name already used by an earlier pair) gets a _t1/_t2 suffix, so the common
    case stays clean.
    """
    out: list[ColumnPair] = []
    for left, right in pairs:
        left_name = left.name if left.name not in taken else f"{left.name}_t1"
        taken.add(left_name)
        right_name = right.name if right.name not in taken else f"{right.name}_t2"
        taken.add(right_name)
        for name in (left_name, right_name):
            if not _IDENT_RE.match(name) or len(name) > _MAX_IDENT:
                raise ValidationFailedError(
                    f"Column '{name}' cannot be represented in the generated table. "
                    "Use lower-case names of 63 characters or fewer."
                )
        out.append(
            ColumnPair(left=left, right=right, output_left=left_name, output_right=right_name)
        )
    return out


def output_table_name(rule_id: str) -> str:
    """Deterministic and unique: the rule id IS the identity, so re-compiling a
    rule always targets the same table instead of orphaning the last one."""
    slug = re.sub(r"[^a-z0-9]+", "_", rule_id.lower()).strip("_")
    name = f"recon_{slug}"
    if not _IDENT_RE.match(name) or len(name) > _MAX_IDENT:
        raise ValidationFailedError(f"Rule id '{rule_id}' cannot name a table.")
    return name


def report_key_for(rule_id: str) -> str:
    return f"recon_{re.sub(r'[^a-z0-9]+', '_', rule_id.lower()).strip('_')}"


async def compile_rule(
    *,
    rule_id: str,
    assurance_id: str,
    rule_name: str,
    comparison: dict,
    params: dict | None = None,
    frequency: str = "Daily",
    severity: str = "medium",
    execution_time: str = "00:00",
    case_routing: dict | None = None,
) -> ReconPlan:
    """Authored rule -> validated ReconPlan. Raises on anything unexecutable."""
    table1 = (comparison or {}).get("table1") or ""
    table2 = (comparison or {}).get("table2") or ""
    if not table1 or not table2:
        raise ValidationFailedError(
            "A reconciliation rule needs both tables. Pick Table 1 and Table 2."
        )

    left = await _resolve_table(assurance_id, table1)
    right = await _resolve_table(assurance_id, table2)
    if left.database != right.database:
        # Cross-database joins are not possible server-side, and streaming both
        # sides into the API to join in Python would not survive the row counts
        # this is built for. Refusing beats a plan that dies at execution.
        raise ValidationFailedError(
            "Both tables must be in the same database to reconcile.",
            details={"table1": left.database, "table2": right.database},
        )

    left_columns = await _columns_of(left)
    right_columns = await _columns_of(right)

    raw_keys = [
        k for k in (comparison.get("keys") or []) if k.get("left") and k.get("right")
    ]
    if not raw_keys:
        raise ValidationFailedError(
            "A reconciliation rule needs at least one comparison key — there is "
            "nothing to join on without it."
        )
    raw_metrics = [
        m for m in (comparison.get("metrics") or []) if m.get("left") and m.get("right")
    ]
    if not raw_metrics:
        raise ValidationFailedError(
            "A reconciliation rule needs at least one comparison metric — with no "
            "metric every joined row is trivially a MATCH."
        )

    key_columns = [
        (
            _pick(left_columns, k["left"], "Table 1", left),
            _pick(right_columns, k["right"], "Table 2", right),
        )
        for k in raw_keys
    ]
    metric_columns = [
        (
            _pick(left_columns, m["left"], "Table 1", left),
            _pick(right_columns, m["right"], "Table 2", right),
        )
        for m in raw_metrics
    ]

    # System column names are reserved so a source column called "status" cannot
    # shadow the reconciliation's own verdict.
    taken: set[str] = set(("status", "execution_time", "rule_id", "execution_id", "remarks"))
    keys = _output_names(key_columns, taken)
    metrics = _output_names(metric_columns, taken)

    tolerance = _tolerance_from(params)

    business_columns = [c for pair in keys for c in (pair.output_left, pair.output_right)]
    business_columns += [c for pair in metrics for c in (pair.output_left, pair.output_right)]

    plan = ReconPlan(
        rule_id=rule_id,
        assurance_id=assurance_id,
        rule_name=rule_name,
        left=left,
        right=right,
        keys=keys,
        metrics=metrics,
        tolerance_pct=tolerance,
        output_schema=settings.recon_output_schema,
        output_table=output_table_name(rule_id),
        report_key=report_key_for(rule_id),
        report_title=rule_name.strip() or rule_id,
        frequency=frequency,
        severity=severity,
        execution_time=execution_time,
        breach_threshold=max(1, int((case_routing or {}).get("breachThreshold") or 1)),
        case_routing=case_routing,
        business_columns=business_columns,
    )
    log.info(
        "recon_compiled",
        rule_id=rule_id,
        database=plan.source_database,
        left=left.qualified,
        right=right.qualified,
        keys=len(keys),
        metrics=len(metrics),
        output=plan.output_qualified,
    )
    return plan


async def compile_sequence_rule(
    *,
    rule_id: str,
    assurance_id: str,
    rule_name: str,
    kind: str,
    params: dict | None,
    frequency: str = "Daily",
    severity: str = "medium",
    execution_time: str = "00:00",
    case_routing: dict | None = None,
) -> ReconPlan:
    """A Sequence or Duplicate rule -> a validated plan.

    Single-table: the author picks one file log, the attribute carrying the
    counter, and optionally what it runs within. The file log is resolved
    against the fixed allow-list rather than the assurance's scope — the same
    four logs answer this question for every assurance.
    """
    params = params or {}
    table_id = (params.get("table") or "").strip()
    column = (params.get("sequenceField") or "").strip()
    partition = (params.get("partitionBy") or "").strip() or None

    if not table_id:
        raise ValidationFailedError("Pick the file log this rule runs over.")
    if not column:
        raise ValidationFailedError(
            "Pick the attribute whose values carry the sequence."
        )

    _, schema, table = parse_table_id(table_id)
    if not metadata_catalog.is_file_log(schema, table):
        raise ValidationFailedError(
            f"{schema}.{table} is not a file log. Sequence and Duplicate rules run "
            "over the AIR/SDP raw and processed file logs."
        )
    ref = TableRef(
        database=metadata_catalog.file_log_database(), schema=schema, table=table
    )

    columns = await _columns_of(ref)
    _pick(columns, column, "sequence", ref)
    if partition:
        _pick(columns, partition, "partition", ref)

    # Inferred from the real values, then frozen into the stored SQL.
    group_index = await sequence.infer_group_index(ref, column)
    options = SequenceOptions(
        column=column, group_index=group_index, partition_column=partition
    )

    plan = ReconPlan(
        rule_id=rule_id,
        assurance_id=assurance_id,
        rule_name=rule_name,
        left=ref,
        # One side only; repeated so the stored definition's NOT NULLs hold.
        right=ref,
        keys=[],
        metrics=[],
        kind=kind,
        sequence=options,
        output_schema=settings.recon_output_schema,
        output_table=output_table_name(rule_id),
        report_key=report_key_for(rule_id),
        report_title=rule_name.strip() or rule_id,
        frequency=frequency,
        severity=severity,
        execution_time=execution_time,
        breach_threshold=max(1, int((case_routing or {}).get("breachThreshold") or 1)),
        case_routing=case_routing,
        business_columns=list(sequence.SEQUENCE_COLUMNS),
    )
    log.info(
        "sequence_compiled",
        rule_id=rule_id,
        kind=kind,
        table=ref.qualified,
        column=column,
        group_index=group_index,
        partition=partition,
        output=plan.output_qualified,
    )
    return plan


def _tolerance_from(params: dict | None) -> float:
    """The builder collects Tolerance % as free text; a blank or unparseable
    value means exact comparison rather than an error — the field is optional
    and a rule should not fail to compile over it."""
    raw = (params or {}).get("tolerance")
    if raw in (None, ""):
        return 0.0
    try:
        value = float(str(raw).strip().rstrip("%"))
    except ValueError:
        log.warning("recon_tolerance_unparsed", value=raw)
        return 0.0
    return value if value > 0 else 0.0
