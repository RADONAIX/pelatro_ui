"""Schemas for validation, compilation and snapshot management."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.rules.schemas import ValidationIssue


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RuleSetValidationRequest(Base):
    #: Omit to validate the whole approved estate.
    rule_set_id: str | None = None
    include_coverage: bool = True


class RuleSetValidationReport(BaseModel):
    checked_at: datetime
    rule_count: int
    error_count: int
    warning_count: int
    #: True when a compile would be allowed.
    can_compile: bool
    structural: list[ValidationIssue] = Field(default_factory=list)
    conflicts: list[ValidationIssue] = Field(default_factory=list)
    coverage: list[ValidationIssue] = Field(default_factory=list)


class CompileRequest(Base):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    rule_set_id: str | None = None
    #: Compile despite conflict errors. Recorded on the snapshot — an override
    #: has to be visible afterwards, not just at the moment it was used.
    force: bool = False


class SnapshotSummary(Base):
    id: str
    version: int
    name: str
    description: str
    status: str
    rule_set_id: str | None
    effective_from: date
    effective_to: date | None
    rule_count: int
    product_count: int
    checksum: str
    compiled_by_name: str | None
    activated_at: datetime | None
    superseded_at: datetime | None
    published_to_clickhouse: bool
    created_at: datetime


class SnapshotDetail(SnapshotSummary):
    stats: dict[str, Any]
    issues: list[Any]


class ExecutableRuleRead(Base):
    id: str
    rule_id: str
    rule_key: str
    rule_version: int
    rule_name: str
    rule_type: str
    execution_stage: str
    stage_order: int
    priority: int
    specificity: int
    stacking_policy: str
    conflict_group: str | None
    effective_from: date
    effective_to: date | None
    currency_code: str | None
    signature: str
    dimension_sets: dict[str, Any]
    predicates: list[Any]
    actions: list[Any]


class SnapshotDiffEntry(BaseModel):
    rule_key: str
    change: str  # ADDED | REMOVED | CHANGED | UNCHANGED
    from_version: int | None = None
    to_version: int | None = None
    details: list[str] = Field(default_factory=list)


class SnapshotDiff(BaseModel):
    from_snapshot: int
    to_snapshot: int
    added: int
    removed: int
    changed: int
    unchanged: int
    identical: bool
    entries: list[SnapshotDiffEntry]


class ActivateRequest(Base):
    comment: str = ""
