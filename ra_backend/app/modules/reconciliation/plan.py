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

# Named after the SIDE that has no row, not after what that side happens to hold.
#
# These were RAW_MISSING / PROCESSED_MISSING, which assumed Table 1 is always
# "processed" and Table 2 always "raw". That is true of an AIR processed-vs-raw
# rule and false of most others — an MSC-vs-IN rule has no raw side at all — so
# the names now say exactly which of the author's two tables is missing the
# record. TABLE1_MISSING means the key was not found in Table 1.
STATUS_TABLE1_MISSING = "TABLE1_MISSING"
STATUS_TABLE2_MISSING = "TABLE2_MISSING"

ALL_STATUSES = (
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_TABLE1_MISSING,
    STATUS_TABLE2_MISSING,
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


# Single-table rule statuses. Distinct from the reconciliation four because a
# sequence answers a different question: not "do both sides agree" but "is
# anything missing from the run".
STATUS_PRESENT = "PRESENT"
STATUS_GAP = "GAP"
STATUS_DUPLICATE = "DUPLICATE"

SEQUENCE_STATUSES = (STATUS_PRESENT, STATUS_GAP, STATUS_DUPLICATE)

KIND_RECONCILIATION = "reconciliation"
KIND_SEQUENCE = "sequence"
KIND_DUPLICATE = "duplicate"
KIND_THRESHOLD = "threshold"

#: Kinds that report whole source ROWS (every column plus a status), as opposed
#: to a folded series (sequence) or a paired comparison (reconciliation).
ROW_KINDS = (KIND_DUPLICATE, KIND_THRESHOLD)

STATUS_ORIGINAL = "ORIGINAL"
STATUS_THRESHOLD_BREACH = "THRESHOLD_BREACH"

#: A duplicate report holds only DUPLICATE rows — the first occurrence is the
#: ORIGINAL and is deliberately not reported — so that is the only status the
#: filter chips need to offer.
DUPLICATE_STATUSES = (STATUS_DUPLICATE,)
THRESHOLD_STATUSES = (STATUS_THRESHOLD_BREACH,)


@dataclass(frozen=True)
class SequenceOptions:
    """How to read a counter out of the selected attribute.

    ``group_index`` is the 0-based index of the digit run that carries the
    counter, inferred from the data at compile time — a filename like
    AIROUTPUTCDR_4011_ATAIR800_0000_20260728-150704.ber holds five digit runs
    and only the third is the sequence. ``None`` means the value IS the number
    (a file_sequence_number column), so nothing is extracted.

    ``partition_column`` is what the counter increments within. When absent the
    partition is derived by masking the counter out of the value itself, which
    is right for AIR (one series per production file) and wrong for SDP (whose
    filenames embed a per-file timestamp) — hence the explicit option.
    """

    column: str
    group_index: int | None
    partition_column: str | None = None

    def as_dict(self) -> dict:
        return {
            "column": self.column,
            "groupIndex": self.group_index,
            "partitionColumn": self.partition_column,
        }

    @staticmethod
    def from_dict(raw: dict) -> "SequenceOptions":
        return SequenceOptions(
            column=raw["column"],
            group_index=raw.get("groupIndex"),
            partition_column=raw.get("partitionColumn"),
        )


@dataclass(frozen=True)
class RowRuleOptions:
    """Parameters for a row-level rule (Duplicate, Threshold).

    ``source_columns`` is the full column list of the source table, resolved at
    compile time: the report must carry every column, and the output table's
    DDL is built from their real types.
    """

    attribute: str
    #: Duplicate only — an extra grouping, so a value repeated under two
    #: different partitions is not one duplicate set.
    partition_column: str | None = None
    #: Threshold only — one of row_rules.OPERATORS.
    operator: str | None = None
    #: Threshold only — compared against the attribute, cast to its type.
    value: str | None = None
    #: Decides which row of a repeated value counts as the first occurrence.
    order_column: str | None = None
    source_columns: list["Column"] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "attribute": self.attribute,
            "partitionColumn": self.partition_column,
            "operator": self.operator,
            "value": self.value,
            "orderColumn": self.order_column,
            "sourceColumns": [
                {"name": c.name, "dataType": c.data_type} for c in self.source_columns
            ],
        }

    @staticmethod
    def from_dict(raw: dict) -> "RowRuleOptions":
        return RowRuleOptions(
            attribute=raw["attribute"],
            partition_column=raw.get("partitionColumn"),
            operator=raw.get("operator"),
            value=raw.get("value"),
            order_column=raw.get("orderColumn"),
            source_columns=[
                Column(name=c["name"], data_type=c["dataType"], numeric=False)
                for c in raw.get("sourceColumns", [])
            ],
        )


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

    #: Which generator builds this plan's SQL. Single-table kinds leave keys and
    #: metrics empty and carry their parameters on `sequence` instead.
    kind: str = KIND_RECONCILIATION
    sequence: SequenceOptions | None = None
    row_rule: RowRuleOptions | None = None

    #: Percent tolerance for numeric metric pairs. 0 = exact.
    tolerance_pct: float = 0.0

    output_schema: str = "assurance"
    output_table: str = ""

    report_key: str = ""
    report_title: str = ""

    frequency: str = "Daily"
    severity: str = "medium"

    #: Local time of day this rule runs, "HH:mm". The frequency says how often,
    #: this says when.
    execution_time: str = "00:00"
    #: Breached rows a run must produce before a case is raised.
    breach_threshold: int = 1
    #: The rule's case routing, copied at compile time so a finished run can
    #: decide about a case without joining back to assurance_rule.
    case_routing: dict | None = None

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
