"""Pydantic schemas for the rule API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.catalog.constants import ServiceType
from app.modules.rules.constants import (
    ActionType,
    ConditionLogic,
    Operator,
    RuleStatus,
    RuleType,
    StackingPolicy,
    ValidationSeverity,
)
from app.modules.rules.ingest import keys

#: Kept for reference and for anything that documents the shape. Validation goes
#: through `keys.validate_format`, which explains a rejection instead of printing
#: the regex at whoever typed the key.
RULE_KEY_PATTERN = r"^[A-Z0-9][A-Z0-9_.-]{2,79}$"


def _optional_rule_key(value: str | None) -> str | None:
    """Blank means absent; anything else must be a well-formed key."""
    cleaned = keys.clean_optional(value)
    return keys.validate_format(cleaned) if cleaned else None


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Conditions & actions ---------------------------------------------------


class ConditionIn(Base):
    attribute: str
    operator: Operator
    #: Always a list, even for single-value operators. EXISTS/NOT_EXISTS pass [].
    values: list[Any] = Field(default_factory=list)
    group_index: int = Field(default=0, ge=0, le=9)
    negate: bool = False


class ConditionRead(ConditionIn):
    id: str
    sequence: int


class ActionIn(Base):
    action_type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)


class ActionRead(ActionIn):
    id: str
    sequence: int


# --- Rules ------------------------------------------------------------------


class RuleCreate(Base):
    #: Omit and the service derives one from the name — authors rarely want to
    #: invent a key, but importers always supply theirs.
    rule_key: str | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    rule_type: RuleType
    service_type: ServiceType
    category: str = ""

    rule_set_id: str | None = None
    product_id: str | None = None
    offer_id: str | None = None
    tariff_plan_id: str | None = None

    priority: int = Field(default=100, ge=0, le=100_000)
    stacking_policy: StackingPolicy = StackingPolicy.EXCLUSIVE
    conflict_group: str | None = Field(default=None, max_length=64)
    condition_logic: ConditionLogic = ConditionLogic.AND

    effective_from: date
    effective_to: date | None = None
    currency_code: str | None = None
    owner: str | None = None
    source_system: str = "MANUAL"
    change_comment: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)

    conditions: list[ConditionIn] = Field(default_factory=list)
    actions: list[ActionIn] = Field(default_factory=list)

    @field_validator("rule_key", mode="before")
    @classmethod
    def _clean_rule_key(cls, value):
        return _optional_rule_key(value)


class RuleUpdate(Base):
    """PATCH body. Conditions/actions are replace-whole-list when supplied —
    element-level patching of an ordered predicate list is ambiguous and the
    builder always submits the full set anyway."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    rule_type: RuleType | None = None
    service_type: ServiceType | None = None
    category: str | None = None

    rule_set_id: str | None = None
    product_id: str | None = None
    offer_id: str | None = None
    tariff_plan_id: str | None = None

    priority: int | None = Field(default=None, ge=0, le=100_000)
    stacking_policy: StackingPolicy | None = None
    conflict_group: str | None = None
    condition_logic: ConditionLogic | None = None

    effective_from: date | None = None
    effective_to: date | None = None
    currency_code: str | None = None
    owner: str | None = None
    change_comment: str | None = None
    attributes: dict[str, Any] | None = None

    conditions: list[ConditionIn] | None = None
    actions: list[ActionIn] | None = None

class RuleSummary(Base):
    """Row shape for the Rule Catalogue table — no conditions/actions payload."""

    id: str
    rule_key: str
    version: int
    name: str
    description: str
    rule_type: str
    execution_stage: str
    category: str
    service_type: str
    status: str
    priority: int
    specificity: int
    stacking_policy: str
    conflict_group: str | None
    product_id: str | None
    offer_id: str | None
    tariff_plan_id: str | None
    rule_set_id: str | None
    effective_from: date
    effective_to: date | None
    currency_code: str | None
    source_system: str
    owner: str | None
    created_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    condition_count: int = 0
    action_count: int = 0
    has_errors: bool = False
    created_at: datetime
    updated_at: datetime


class RuleDetail(RuleSummary):
    supersedes_id: str | None = None
    condition_logic: str
    change_comment: str
    attributes: dict[str, Any]
    last_validation: dict[str, Any] | None = None
    conditions: list[ConditionRead] = Field(default_factory=list)
    actions: list[ActionRead] = Field(default_factory=list)


class RuleListResponse(BaseModel):
    items: list[RuleSummary]
    total: int
    limit: int
    offset: int


# --- Lifecycle --------------------------------------------------------------


class StatusChange(Base):
    status: RuleStatus
    comment: str = ""


class NewVersionRequest(Base):
    change_comment: str = ""
    #: Leave empty to copy the current version verbatim, then edit the draft.
    name: str | None = None


class CloneRequest(Base):
    rule_key: str | None = None

    @field_validator("rule_key", mode="before")
    @classmethod
    def _clean_rule_key(cls, value):
        return _optional_rule_key(value)
    name: str = Field(min_length=1, max_length=255)


# --- Validation -------------------------------------------------------------


class ValidationIssue(BaseModel):
    severity: ValidationSeverity
    code: str
    message: str
    #: Dotted path into the rule, e.g. "conditions[2].values" — lets the builder
    #: highlight the offending row instead of showing a wall of text.
    path: str = ""
    hint: str = ""


class ValidationReport(BaseModel):
    valid: bool
    checked_at: datetime
    error_count: int
    warning_count: int
    issues: list[ValidationIssue]


# --- Rule sets --------------------------------------------------------------


class RuleSetCreate(Base):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    owner: str | None = None
    source_system: str = "MANUAL"


class RuleSetRead(Base):
    id: str
    code: str
    name: str
    description: str
    status: str
    owner: str | None
    source_system: str
    created_by: str | None
    rule_count: int = 0
    created_at: datetime
    updated_at: datetime


# --- Audit & templates ------------------------------------------------------


class AuditEntryRead(Base):
    id: str
    rule_key: str
    rule_id: str | None
    version: int | None
    action: str
    from_status: str | None
    to_status: str | None
    actor_id: str | None
    actor_name: str | None
    comment: str
    diff: dict[str, Any]
    created_at: datetime


class TemplateRead(Base):
    id: str
    code: str
    name: str
    description: str
    service_type: str
    rule_type: str
    payload: dict[str, Any]
    is_system: bool


# --- Dashboard --------------------------------------------------------------


class RuleStats(BaseModel):
    total_rules: int
    logical_rules: int
    by_status: dict[str, int]
    by_service_type: dict[str, int]
    by_rule_type: dict[str, int]
    draft_count: int
    active_count: int
    expiring_within_30_days: int
    rules_with_errors: int
