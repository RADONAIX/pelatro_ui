"""Response shapes for the import API.

Built around one question an operator asks in two halves: *did you understand my
file*, and *what would this do to my estate*. The preview answers both together
because that is how the question is asked — a mapping the operator has not
checked makes the decision counts meaningless, and a mapping with no counts
beside it does not tell them whether to press the button.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.rules.ingest.reconcile import ImportMode


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ParseRejection(Base):
    """A row that never became a draft — bad date, missing name, no actions.

    Distinct from a REJECTED *record*, which did become a draft and then failed
    validation. The two have different fixes: this one is a broken cell, that one
    is a rule the estate will not accept.
    """

    offset: int
    message: str
    column: str = ""


class PreviewRow(Base):
    offset: int
    rule_key: str
    rule_name: str = ""
    decision: str
    issues: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""


class MissingMetadata(Base):
    """One catalogue, and what this batch wanted from it that is not there.

    Grouped rather than per-row because the two situations look identical in a
    per-row report and are completely different in practice: forty rows failing
    on one missing offer is a five-second fix, forty rows failing on forty
    missing zones is a data problem.
    """

    catalogue: str
    label: str
    codes: list[str] = Field(default_factory=list)
    affected_rules: int = 0
    #: Whether an import may create placeholders here. False for anything where a
    #: placeholder would change what gets priced rather than merely name it.
    creatable: bool = False
    reason: str = ""
    create_endpoint: str = ""


class PreviewResponse(Base):
    filename: str
    headings: list[str]
    #: Only the headings that mapped. The UI shows this as "we read these 12
    #: columns", which is the half of the answer an operator checks first.
    mapping: dict[str, str] = Field(default_factory=dict)
    #: Headings we did not recognise. Never silently ignored — an unmapped
    #: "Peak Rate" column is the difference between a correct import and a
    #: catastrophic one, and only the operator can tell which.
    unmapped_headings: list[str] = Field(default_factory=list)
    row_count: int
    content_hash: str
    counts: dict[str, int] = Field(default_factory=dict)
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    #: False when the batch would change or withdraw something already live.
    safe_to_auto_commit: bool = True
    notes: list[str] = Field(default_factory=list)
    parse_rejections: list[ParseRejection] = Field(default_factory=list)
    #: What the catalogue is missing, grouped. The thing an operator acts on.
    missing_metadata: list[MissingMetadata] = Field(default_factory=list)
    sample: list[PreviewRow] = Field(default_factory=list)


class BatchSummary(Base):
    batch_id: str
    channel: str
    source_system_id: str | None = None
    filename: str | None = None
    import_mode: str = ImportMode.DELTA
    status: str
    dry_run: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    duration_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    triggered_by_name: str | None = None
    #: The approvals inbox sorts on this, not on row counts: a hundred new drafts
    #: change nothing until someone publishes them, and one changed rule changes
    #: what subscribers are charged tonight.
    touched_live_pricing: bool = False


class BatchDetail(BatchSummary):
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    #: Where a partial run got to, so a resume starts from the last good chunk
    #: rather than from zero.
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    record_decisions: dict[str, int] = Field(default_factory=dict)
    parse_rejections: list[ParseRejection] = Field(default_factory=list)
    missing_metadata: list[MissingMetadata] = Field(default_factory=list)
    #: Catalogue entries this run created because `create_missing_references`
    #: was set. Always reported: an operator must be able to tell what they
    #: defined from what an import invented on their behalf.
    created_references: dict[str, list[str]] = Field(default_factory=dict)


class BatchList(Base):
    items: list[BatchSummary]
    total: int
    limit: int
    offset: int


class RecordOut(Base):
    record_id: str
    source_offset: int
    external_ref: str | None = None
    decision: str
    rule_id: str | None = None
    rule_version_id: str | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = ""
    #: The record exactly as it arrived. This is what makes "what did they
    #: actually send us?" answerable without re-running the parser.
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class RecordList(Base):
    items: list[RecordOut]
    total: int
    limit: int
    offset: int


class ConnectorSync(Base):
    """Options for pulling a source system through the kernel."""

    #: Defaults to a preview. A connector that committed by default is how the
    #: legacy import changed live pricing before anyone had looked at it.
    dry_run: bool = True
    import_mode: str = Field(
        default=ImportMode.DELTA,
        description="FULL means the export is the whole truth, so an omission "
                    "becomes a withdrawal proposal.",
    )
    default_charging_mode: str | None = None
    default_currency: str | None = None
    effective_from: Any = None
