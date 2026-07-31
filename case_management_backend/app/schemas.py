"""Pydantic contracts for the case API.

Everything is exposed to the browser in camelCase (aliased from the snake_case
ORM attributes) while still accepting either spelling on input, so the frontend
types map straight onto these payloads with no translation layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel


def to_iso(dt: datetime | None) -> str | None:
    """Serialise as UTC ISO-8601 with a Z suffix.

    Timestamps are stored naive-UTC (SQLite drops tzinfo), so a naive value is
    interpreted as UTC rather than as the server's local zone — otherwise the
    browser would shift every timestamp by the host's offset.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[datetime, PlainSerializer(to_iso, return_type=str | None)]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# --------------------------------------------------------------------------
# Mismatch evidence
# --------------------------------------------------------------------------

class MismatchIn(ApiModel):
    """One record-level mismatch as emitted by a control run."""

    record_ref: str = ""
    entity: str = ""
    subscriber: str = ""
    field: str = ""
    expected_value: str | None = None
    actual_value: str | None = None
    delta: str | None = None
    status: str = "MISMATCH"
    occurred_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MismatchOut(MismatchIn):
    id: str
    position: int = 0
    occurred_at: UtcDateTime | None = None


# --------------------------------------------------------------------------
# Comments / activity
# --------------------------------------------------------------------------

class CommentIn(ApiModel):
    author: str = ""
    body: str = Field(min_length=1)


class CommentOut(ApiModel):
    id: str
    author: str
    body: str
    created_at: UtcDateTime


class ActivityOut(ApiModel):
    id: str
    actor: str
    action: str
    field: str | None = None
    from_value: str | None = None
    to_value: str | None = None
    note: str | None = None
    created_at: UtcDateTime


class InsightIn(ApiModel):
    body: str = Field(min_length=1)


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------

class AttachmentOut(ApiModel):
    id: str
    case_id: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    uploaded_by: str
    created_at: UtcDateTime
    # Ready-to-use relative download path, so the client never builds one.
    download_url: str = ""


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

class CaseBase(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""

    # Accepts a code ("UA") or a full name ("Usage Assurance"); the service
    # resolves it against the catalog and fills in name/group. Optional when
    # `ruleId` is given — the rule already knows which assurance it belongs to.
    assurance: str = ""
    module: str = ""
    sub_module: str = ""

    rule_id: str | None = None
    rule_name: str | None = None
    rule_category: str = ""
    rule_run_id: str | None = None

    severity: str = "medium"
    status: str = "Open"
    action: str = "NA"
    owner: str = ""

    stream: str = ""
    node_id: str = ""
    source_feed: str = ""
    target_feed: str = ""
    linked_batch: str = ""
    linked_txn_id: str = ""

    expected_value: str | None = None
    actual_value: str | None = None
    variance: str | None = None
    variance_pct: float | None = None
    threshold: str | None = None
    estimated_impact: float = 0.0
    affected_count: int = 0

    evidence: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    detected_at: datetime | None = None
    mismatches: list[MismatchIn] = Field(default_factory=list)


class CaseCreate(CaseBase):
    """Analyst-raised case (the Add Case dialog)."""

    origin: Literal["analyst_raised", "auto_detected"] = "analyst_raised"
    reference: str | None = None      # server-generated when omitted
    created_by: str = ""


class RuleCaseIngest(CaseBase):
    """Case raised by a control run — the rule engine's entry point.

    `dedupeKey` makes the call idempotent: posting the same key twice returns
    the existing case (optionally topped up with new mismatch rows) instead of
    creating a second one for the same finding.
    """

    origin: Literal["auto_detected", "analyst_raised"] = "auto_detected"
    dedupe_key: str | None = None
    # When the key already exists, append the posted mismatch rows to the
    # existing case rather than discarding them.
    append_mismatches_on_duplicate: bool = False


class CaseUpdate(ApiModel):
    """Partial update — only the fields present in the body are written."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    severity: str | None = None
    action: str | None = None
    owner: str | None = None
    module: str | None = None
    sub_module: str | None = None
    tags: list[str] | None = None
    evidence: dict[str, Any] | None = None
    # Recorded against every resulting activity row.
    actor: str = "system"


class AssignIn(ApiModel):
    owner: str = ""
    actor: str = "system"


class StatusIn(ApiModel):
    status: str
    action: str | None = None
    note: str | None = None
    actor: str = "system"


class CaseRow(ApiModel):
    """List-row projection — everything the table and cards render."""

    id: str
    reference: str
    title: str
    description: str

    assurance_code: str
    assurance_name: str
    assurance_group: str
    module: str
    sub_module: str

    rule_id: str | None
    rule_name: str | None
    rule_category: str
    rule_run_id: str | None

    origin: str
    severity: str
    status: str
    action: str
    owner: str

    stream: str
    node_id: str
    source_feed: str
    target_feed: str
    linked_batch: str
    linked_txn_id: str

    expected_value: str | None
    actual_value: str | None
    variance: str | None
    variance_pct: float | None
    threshold: str | None
    estimated_impact: float
    affected_count: int

    evidence: dict[str, Any] | None
    tags: list[str]
    saved_insights: list[dict[str, Any]]
    details: dict[str, Any]

    detected_at: UtcDateTime
    created_at: UtcDateTime
    updated_at: UtcDateTime
    closed_at: UtcDateTime | None

    mismatch_count: int = 0


class CaseDetail(CaseRow):
    mismatches: list[MismatchOut] = Field(default_factory=list)
    comments: list[CommentOut] = Field(default_factory=list)
    activities: list[ActivityOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)


class CaseListResponse(ApiModel):
    items: list[CaseRow]
    total: int
    page: int
    page_size: int
    page_count: int


class SummaryResponse(ApiModel):
    """Counts for the tiles above the list — computed over the active filters."""

    total: int
    unassigned: int
    assigned: int
    by_status: dict[str, int]
    by_severity: dict[str, int]
    by_assurance: dict[str, int]
    by_category: dict[str, int]
    open_estimated_impact: float
    affected_records: int


class FacetValue(ApiModel):
    value: str
    label: str
    count: int


class FacetsResponse(ApiModel):
    """Filter options with live counts, derived from the cases that exist."""

    assurances: list[FacetValue]
    groups: list[FacetValue]
    modules: list[FacetValue]
    categories: list[FacetValue]
    statuses: list[FacetValue]
    severities: list[FacetValue]
    origins: list[FacetValue]
    actions: list[FacetValue]
    owners: list[FacetValue]
    rules: list[FacetValue]


class BulkIngestResponse(ApiModel):
    created: list[CaseRow]
    duplicates: list[CaseRow]
    created_count: int
    duplicate_count: int


class OkResponse(ApiModel):
    ok: bool = True
    message: str = ""


# --------------------------------------------------------------------------
# Control rules (the rules module)
# --------------------------------------------------------------------------

class RuleBase(ApiModel):
    """The fields the "Create control rule" form collects."""

    name: str = Field(min_length=1, max_length=200)
    # What leakage or integrity risk this control detects.
    intent: str = ""
    # Code ("UA") or full name ("Usage Assurance").
    assurance: str
    entity_scope: str = ""
    primitive_category: str = ""
    severity: str = "medium"
    frequency: str = "Daily"
    source_feed: str = ""
    target_feed: str = ""
    tolerance_pct: float | None = None
    # Primitive-specific parameters beyond the common feed/tolerance ones.
    params: dict[str, Any] = Field(default_factory=dict)
    lifecycle_state: str = "Draft"


class RuleCreate(RuleBase):
    # Server-generated from the assurance code (UA001…) when omitted.
    id: str | None = None
    created_by: str = "analyst"


class RuleUpdate(ApiModel):
    name: str | None = None
    intent: str | None = None
    entity_scope: str | None = None
    primitive_category: str | None = None
    severity: str | None = None
    frequency: str | None = None
    source_feed: str | None = None
    target_feed: str | None = None
    tolerance_pct: float | None = None
    params: dict[str, Any] | None = None
    lifecycle_state: str | None = None
    last_status: str | None = None


class RuleOut(ApiModel):
    id: str
    name: str
    intent: str
    assurance_code: str
    assurance_name: str
    assurance_group: str
    entity_scope: str
    primitive_category: str
    severity: str
    frequency: str
    source_feed: str
    target_feed: str
    tolerance_pct: float | None
    params: dict[str, Any]
    lifecycle_state: str
    last_status: str
    last_run_at: UtcDateTime | None
    cases_raised: int
    created_by: str
    created_at: UtcDateTime
    updated_at: UtcDateTime
    # Live count of cases currently open against this rule.
    open_cases: int = 0


class RuleListResponse(ApiModel):
    items: list[RuleOut]
    total: int
    page: int
    page_size: int
    page_count: int


class RuleRunResult(ApiModel):
    """What a control run reports back after executing."""

    status: str = "FAIL"                      # PASS | FAIL | WARNING
    run_id: str | None = None
    # Everything below is only needed when the run failed and a case must open.
    title: str | None = None
    description: str = ""
    expected_value: str | None = None
    actual_value: str | None = None
    variance: str | None = None
    variance_pct: float | None = None
    estimated_impact: float = 0.0
    affected_count: int = 0
    node_id: str = ""
    stream: str = ""
    linked_batch: str = ""
    linked_txn_id: str = ""
    sub_module: str = ""
    severity: str | None = None               # overrides the rule's severity
    owner: str = ""
    detected_at: datetime | None = None
    dedupe_key: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    mismatches: list[MismatchIn] = Field(default_factory=list)


class RuleRunResponse(ApiModel):
    rule_id: str
    status: str
    case_created: bool
    case: CaseDetail | None = None
