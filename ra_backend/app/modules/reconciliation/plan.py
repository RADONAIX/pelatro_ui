"""The compiled reconciliation plan — the contract between every stage.

Rule Compiler -> [ReconPlan] -> SQL Generator -> Execution Engine -> Reports

The plan is the ONLY thing the SQL generator reads. Every identifier on it has
already been resolved against information_schema by the compiler, so the
generator can quote and interpolate them without re-validating: nothing on a
plan came straight from a request body.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Status vocabulary. These four strings are the reconciliation's whole output
# contract — the report filters on them and the UI colours them, so they are
# defined once here and never spelled out again anywhere else.
STATUS_MATCH = "MATCH"
STATUS_MISMATCH = "MISMATCH"
STATUS_RAW_MISSING = "RAW_MISSING"
STATUS_PROCESSED_MISSING = "PROCESSED_MISSING"

ALL_STATUSES = (
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_RAW_MISSING,
    STATUS_PROCESSED_MISSING,
)

# System columns appended to every generated table, in this order.
SYSTEM_COLUMNS = ("status", "execution_time", "rule_id", "execution_id", "remarks")

# Marker selected from each side of the FULL OUTER JOIN.
#
# Which side is missing CANNOT be decided by testing a join key for NULL: a row
# that genuinely holds NULL in that key is indistinguishable from a row that did
# not match. A constant carried through the join is never NULL for a row that
# exists, so it answers the question exactly.
PRESENCE_COLUMN = "__present"


@dataclass(frozen=True)
class Column:
    """One resolved source column."""

    name: str
    #: Postgres type as format_type() reports it, e.g. "numeric(18,4)".
    data_type: str
    #: True when the type is arithmetic, so a numeric tolerance may apply.
    numeric: bool

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.name} {self.data_type}"


@dataclass(frozen=True)
class ColumnPair:
    """A left/right column pairing — one join key or one compared metric.

    ``output_left``/``output_right`` are the names these take in the generated
    table. They are usually just the source names (the spec's example relies on
    that), and are disambiguated only when the two sides collide.
    """

    left: Column
    right: Column
    output_left: str
    output_right: str

    @property
    def comparable_numerically(self) -> bool:
        return self.left.numeric and self.right.numeric


@dataclass(frozen=True)
class TableRef:
    database: str
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True)
class ReconPlan:
    """Everything needed to build, populate and report on one reconciliation."""

    rule_id: str
    assurance_id: str
    rule_name: str

    left: TableRef
    right: TableRef

    keys: list[ColumnPair]
    metrics: list[ColumnPair]

    #: Percent tolerance for numeric metric pairs. 0 = exact.
    tolerance_pct: float = 0.0

    output_schema: str = "assurance"
    output_table: str = ""

    report_key: str = ""
    report_title: str = ""

    frequency: str = "Daily"
    severity: str = "medium"

    #: Populated by the SQL generator; carried so the engine and the metadata
    #: repository store exactly what ran.
    ddl: str = ""
    insert_sql: str = ""

    #: Output-table column order: keys (both sides), metrics (both sides),
    #: then SYSTEM_COLUMNS. Report views project this same order.
    business_columns: list[str] = field(default_factory=list)

    @property
    def output_qualified(self) -> str:
        return f"{self.output_schema}.{self.output_table}"

    @property
    def source_database(self) -> str:
        return self.left.database
