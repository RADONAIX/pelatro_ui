"""Request and response shapes for the canonical rule API.

Separate from ``CanonicalDraft`` on purpose. The draft is an internal contract
between adapters and the writer; these are an HTTP contract with a UI. Keeping
them apart means the API can rename a field for clarity without touching
storage, and storage can add a column without breaking a client — the two
evolve for entirely different reasons and on entirely different schedules.

Everything monetary is ``Decimal`` at this boundary too. Pydantic will happily
coerce a JSON number to ``float`` if you let it, and a rate that becomes a float
in the request model is a rate that was already damaged before the codec ever
saw it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.rules.ingest import keys
from app.modules.rules.vocabulary.modes import (
    ChargingMode,
    ExecutionMode,
    FallbackPolicy,
)
from app.modules.rules.vocabulary.values import ValueType

CODE_PATTERN = r"^[A-Z0-9][A-Z0-9_.:-]{0,95}$"


def _optional_rule_key(value: str | None) -> str | None:
    """Blank means absent — a form library sends "" for an untouched field, and
    the kernel derives a key deterministically when none is given."""
    cleaned = keys.clean_optional(value)
    return keys.validate_format(cleaned) if cleaned else None


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Inbound: the wizard's payload ------------------------------------------


class ParameterIn(Base):
    name: str = Field(min_length=1, max_length=64)
    #: Sent as JSON — a string, number or boolean. Typed by the codec against
    #: `value_type`, so a client never has to guess our storage format.
    value: Any
    value_type: ValueType = ValueType.STRING
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=16)
    #: Ordered parameters under one name — a rate ladder is several entries.
    sequence: int = Field(default=0, ge=0)


class ActionIn(Base):
    action_type: str = Field(min_length=1, max_length=48)
    #: Overrides the action spec's default. Rarely set.
    target_attribute: str | None = Field(default=None, max_length=64)
    parameters: list[ParameterIn] = Field(default_factory=list)
    sequence: int = Field(default=0, ge=0)


class ConditionIn(Base):
    attribute: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=24)
    #: Always a list, even for a single-value operator, so there is one shape to
    #: consume and no per-operator branching anywhere downstream.
    values: list[Any] = Field(default_factory=list)
    negated: bool = False
    unit: str | None = Field(default=None, max_length=16)
    currency: str | None = Field(default=None, max_length=8)


class ConditionGroupIn(Base):
    """A bracket. Nests to the validator's depth cap of four."""

    logic: str = Field(default="AND", pattern=r"^(AND|OR)$")
    negated: bool = False
    label: str = Field(default="", max_length=128)
    conditions: list[ConditionIn] = Field(default_factory=list)
    children: list[ConditionGroupIn] = Field(default_factory=list)


class BehaviourIn(Base):
    """The wizard's Step 4.

    ``specificity`` is absent, and that is the design: it is computed from the
    conditions and shown read-only with its derivation. An author-editable
    specificity beside an author-editable priority is two knobs for one job, and
    a 10,000-rule estate where both have been hand-tuned is one where nobody can
    say which rule wins.
    """

    priority: int = Field(default=100, ge=0, le=100_000)
    stacking_policy: str = "EXCLUSIVE"
    conflict_group: str | None = None
    fallback_policy: FallbackPolicy = FallbackPolicy.FALLBACK_CHAIN
    stop_processing: bool = False
    execution_mode: ExecutionMode = ExecutionMode.BOTH
    condition_logic: str = Field(default="AND", pattern=r"^(AND|OR)$")


class TargetsIn(Base):
    """Catalogue *codes*, never ids — a rule set exported from staging must
    import into production unchanged, and ids do not survive that trip."""

    product: str | None = None
    offer: str | None = None
    tariff_plan: str | None = None


class ValidityIn(Base):
    effective_from: date
    effective_to: date | None = None
    currency_code: str | None = Field(default=None, min_length=3, max_length=8)

    @model_validator(mode="after")
    def _ordered(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to is before effective_from.")
        return self


class RuleWrite(Base):
    """One rule, as the wizard or an API client states it."""

    rule_name: str = Field(min_length=1, max_length=255)
    charging_mode: ChargingMode
    rule_type_code: str = Field(min_length=1, max_length=48)
    service_type: str = Field(min_length=1, max_length=16)
    validity: ValidityIn
    #: Omit and the kernel derives one from what the rule *does*. Never from the
    #: name: a rename must be an update, not a new logical rule.
    rule_key: str | None = None
    description: str = ""
    conditions: ConditionGroupIn = Field(default_factory=ConditionGroupIn)
    actions: list[ActionIn] = Field(default_factory=list)
    behaviour: BehaviourIn = Field(default_factory=BehaviourIn)
    targets: TargetsIn = Field(default_factory=TargetsIn)
    set_codes: list[str] = Field(default_factory=list)
    owner: str | None = None
    change_reason: str = ""

    @field_validator("rule_key", mode="before")
    @classmethod
    def _clean_rule_key(cls, value):
        return _optional_rule_key(value)


# --- Outbound ---------------------------------------------------------------


class IssueOut(Base):
    severity: str
    code: str
    message: str
    #: Dotted path into the rule — `conditions[2].values[0]`, `actions[0].params.rate`.
    #: The builder highlights the row rather than printing a list of sentences.
    path: str = ""
    hint: str = ""


class SpecificityFactor(Base):
    """One condition's contribution, so the read-only score can show its working."""

    attribute: str
    label: str
    operator: str
    points: int


class SpecificityOut(Base):
    score: int
    factors: list[SpecificityFactor] = Field(default_factory=list)
    #: "product 50 + destination zone 45 + time band 35 = 130"
    derivation: str = ""


class ConditionOut(Base):
    attribute: str
    operator: str
    values: list[Any]
    value_type: str
    negated: bool
    unit_code: str | None = None
    currency_code: str | None = None
    #: The catalogue id a REFERENCE value resolved to, alongside its code.
    resolved_ref_id: str | None = None
    sequence: int


class ConditionGroupOut(Base):
    logic: str
    negated: bool
    label: str
    sequence: int
    conditions: list[ConditionOut] = Field(default_factory=list)
    children: list[ConditionGroupOut] = Field(default_factory=list)


class ParameterOut(Base):
    name: str
    value: Any
    value_type: str
    numeric: Decimal | None = None
    currency_code: str | None = None
    unit_code: str | None = None
    resolved_ref_id: str | None = None
    sequence: int


class ActionOut(Base):
    action_type: str
    label: str = ""
    stage_code: str = ""
    target_attribute: str | None = None
    value: Any = None
    value_numeric: Decimal | None = None
    currency_code: str | None = None
    unit_code: str | None = None
    parameters: list[ParameterOut] = Field(default_factory=list)
    sequence: int


class VersionSummary(Base):
    rule_version_id: str
    version_number: int
    status: str
    validation_state: str
    effective_from: date
    effective_to: date | None
    priority: int
    specificity_score: int
    execution_mode: str
    behaviour_hash: str
    published_snapshot_id: str | None = None
    change_reason: str = ""
    created_at: datetime


class RuleSummary(Base):
    """One row of the catalogue. Every column §E.2 lists, and nothing else —
    the list endpoint is the one that has to stay under 200 ms at 40,000 rules."""

    rule_id: str
    rule_key: str
    rule_name: str
    charging_mode: str
    rule_type_code: str
    rule_type_name: str = ""
    stage_code: str = ""
    service_type: str
    status: str
    owner: str | None = None
    source: str = "MANUAL"
    version_number: int | None = None
    priority: int | None = None
    specificity_score: int | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    currency_code: str | None = None
    product_code: str | None = None
    offer_code: str | None = None
    tariff_plan_code: str | None = None
    validation_state: str = "UNKNOWN"
    published_snapshot_id: str | None = None
    condition_count: int = 0
    action_count: int = 0
    updated_at: datetime | None = None


class RuleDetail(RuleSummary):
    description: str = ""
    execution_mode: str = "BOTH"
    stacking_policy: str = "EXCLUSIVE"
    conflict_group: str | None = None
    fallback_policy: str = "FALLBACK_CHAIN"
    stop_processing: bool = False
    condition_logic: str = "AND"
    behaviour_hash: str = ""
    change_reason: str = ""
    rule_version_id: str | None = None
    conditions: ConditionGroupOut | None = None
    actions: list[ActionOut] = Field(default_factory=list)
    specificity: SpecificityOut | None = None
    issues: list[IssueOut] = Field(default_factory=list)
    versions: list[VersionSummary] = Field(default_factory=list)
    #: Vendor fields no canonical column models yet. Surfaced as "3 unmapped
    #: fields on this rule" rather than silently dropped.
    extras: dict[str, Any] = Field(default_factory=dict)


class WriteResponse(Base):
    """What a create or update returns.

    Carries the issues alongside the rule because a save that succeeded with
    three warnings and a save that succeeded cleanly are different events, and a
    client that has to make a second call to find out which is a client that
    will not bother.
    """

    rule: RuleDetail
    decision: str
    issues: list[IssueOut] = Field(default_factory=list)
    validation_state: str


class ListEnvelope(Base):
    items: list[RuleSummary]
    total: int
    limit: int
    offset: int


class RuleEstateStats(Base):
    logical_rules: int
    total_versions: int
    pending_approval: int
    rules_with_errors: int


class StatusChange(Base):
    status: str = Field(min_length=1, max_length=16)
    comment: str = ""


class CloneRequest(Base):
    rule_name: str = Field(min_length=1, max_length=255)
    rule_key: str | None = None

    @field_validator("rule_key", mode="before")
    @classmethod
    def _clean_rule_key(cls, value):
        return _optional_rule_key(value)


class KeySuggestion(Base):
    suggested: str
    available: bool
    #: What the key would be if the author accepts it — already de-duplicated.
    resolved: str


# --- Validation (group 3) ---------------------------------------------------


class ValidationReport(Base):
    valid: bool
    validation_state: str
    error_count: int
    warning_count: int
    issues: list[IssueOut] = Field(default_factory=list)
    checked_at: datetime


class BatchValidationItem(Base):
    index: int
    rule_key: str
    rule_name: str
    valid: bool
    validation_state: str
    issues: list[IssueOut] = Field(default_factory=list)


class BatchValidationReport(Base):
    total: int
    valid_count: int
    error_count: int
    warning_count: int
    items: list[BatchValidationItem] = Field(default_factory=list)
    #: Grouped by cause. Four hundred rules failing one check is one fix, and an
    #: operator who sees four hundred lines concludes the batch is unusable.
    reasons: list[dict[str, Any]] = Field(default_factory=list)


class ReferenceCheck(Base):
    catalogue: str
    code: str
    exists: bool
    resolved_id: str | None = None
    message: str = ""


class ReferenceReport(Base):
    checked: int
    missing: int
    references: list[ReferenceCheck] = Field(default_factory=list)


class ConflictOut(Base):
    """Two rules that can both win the same event."""

    kind: str
    severity: str
    message: str
    rule_keys: list[str] = Field(default_factory=list)
    conflict_group: str | None = None
    stage_code: str = ""
    hint: str = ""


class ConflictReport(Base):
    examined: int
    conflicts: list[ConflictOut] = Field(default_factory=list)


ConditionGroupIn.model_rebuild()
ConditionGroupOut.model_rebuild()
