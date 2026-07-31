"""Request and response shapes for bulk lifecycle operations.

One response for every operation. A UI that renders "what would happen" and
"what happened" with two different components will eventually show them
differently, and the moment those diverge the dry run stops being a preview and
becomes a second opinion.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BulkRequest(Base):
    """Exactly one selector. No arbitrary id list — see `lifecycle.selectors`."""

    batch_id: str | None = None
    rule_set_id: str | None = None
    #: The catalogue's own filters, by the names the list endpoint uses, so a
    #: bulk action operates on exactly what the operator was looking at.
    filters: dict[str, Any] | None = None
    #: Explicitly ticked rules, by key. Keys rather than ids so the request says
    #: what it did — an audit entry naming five UUIDs explains nothing.
    rule_keys: list[str] | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def _one_selector(self):
        chosen = [
            n for n, v in (
                ("batch_id", self.batch_id),
                ("rule_set_id", self.rule_set_id),
                ("filters", self.filters or None),
                ("rule_keys", self.rule_keys or None),
            ) if v
        ]
        if len(chosen) != 1:
            raise ValueError(
                "Name exactly one of batch_id, rule_set_id, filters or rule_keys"
                + (f"; got {', '.join(chosen)}" if chosen else "")
            )
        return self


class ApproveRequest(BulkRequest):
    comment: str = Field(default="", max_length=2000)
    #: All-or-nothing unless the caller opts out. A half-approved tariff prices
    #: traffic wrong in a way that reads as an engine fault.
    atomic: bool = True


class ActivateRequest(ApproveRequest):
    """Compile the selection into a snapshot and make it live.

    `force` records an override for a set that still has blocking validation
    issues. Deliberately not defaulted on: a snapshot compiled past its own
    errors is a decision somebody should have to make explicitly, and the
    snapshot carries the fact that they did.
    """

    force: bool = False


class BulkPreview(BulkRequest):
    operation: Literal["validate", "approve", "revert"] = "validate"
    atomic: bool = True


class JobRequest(ApproveRequest):
    """Queue a bulk operation instead of running it in the request.

    Same selectors, same flags — the only difference is where it runs. That is
    the point: a job must not be a slightly different operation, or the estate
    ends up with two definitions of "approve".
    """

    operation: Literal["validate", "approve", "revert", "activate", "delete"]
    force: bool = False


class JobOut(Base):
    bulk_run_id: str
    operation: str
    status: str
    selector: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    atomic: bool = True
    force: bool = False
    comment: str = ""
    total: int = 0
    processed: int = 0
    applied: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    snapshot: dict[str, Any] | None = None
    touched_live_pricing: bool = False
    error: str = ""
    error_details: dict[str, Any] | None = None
    actor_name: str | None = None
    created_at: Any = None
    started_at: Any = None
    finished_at: Any = None
    #: Derived, not stored. A RUNNING row whose process died reads as RUNNING
    #: forever otherwise, and the operator has no way to know to resubmit.
    stale: bool = False
    #: 0-100, or None when the total is not yet known.
    percent: int | None = None


class JobList(Base):
    items: list[JobOut]
    total: int


class RuleOutcomeOut(Base):
    rule_id: str
    rule_key: str
    rule_name: str
    #: APPLIED | ALREADY | BLOCKED_VALIDATION | SKIPPED_OWN_WORK |
    #: BLOCKED_TRANSITION | BLOCKED_NO_VERSION
    outcome: str
    from_status: str = ""
    to_status: str = ""
    reason: str = ""
    code: str = ""


class SnapshotRef(Base):
    """The snapshot an activation produced. Absent for every other operation."""

    snapshot_id: str
    version: int
    status: str
    rule_count: int
    checksum: str
    forced: bool


class BulkResponse(Base):
    operation: str
    selector: dict[str, Any]
    dry_run: bool
    total: int
    #: Rules the operation would move, plus those already where it would put them.
    eligible: int
    applied: int
    counts: dict[str, int] = Field(default_factory=dict)
    #: Grouped by cause, never one line per rule: thirty-two rules failing one
    #: check is one fix, and thirty-two lines reads as a broken batch.
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    touched_live_pricing: bool = False
    #: Whether this selection needs rule-approval rights rather than edit rights.
    requires_approver_role: bool = False
    caller_can_approve: bool = False
    #: Capped. The grouped `blocked` list is the summary; this is for drilling in.
    rules: list[RuleOutcomeOut] = Field(default_factory=list)
    #: Set by `activate` only — the snapshot that was compiled and made live.
    #: Without it the caller has no way to name what they just published, which
    #: is the one thing they need in order to roll it back.
    snapshot: SnapshotRef | None = None
