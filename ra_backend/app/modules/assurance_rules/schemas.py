"""Wire shapes for authored assurance rules.

Field names are camelCase to match the UI's CustomRule type verbatim, so the
Rule Explorer stores what it renders with no mapping layer in between.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "high", "medium"]
Frequency = Literal["Real-time", "Hourly", "Daily", "Cycle"]
State = Literal["Draft", "Active"]


class CaseRouting(BaseModel):
    raiseCase: bool = False
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    owner: str = ""
    #: Breached rows a run must produce before a case is raised. Only consulted
    #: when raiseCase is true; 1 means "raise on any breach", which is what
    #: rules did before this existed.
    breachThreshold: int = Field(default=1, ge=1)


class AttrPair(BaseModel):
    left: str = ""
    right: str = ""


class RuleComparison(BaseModel):
    mode: Literal["Single", "Multiple"] = "Multiple"
    table1: str = ""
    table2: str = ""
    metrics: list[AttrPair] = Field(default_factory=list)
    keys: list[AttrPair] = Field(default_factory=list)


class RuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    category: str = Field(min_length=1, max_length=64)
    entity: str = Field(min_length=1, max_length=128)
    severity: Severity = "medium"
    frequency: Frequency = "Daily"
    #: Local time of day the rule runs, "HH:mm". Validated by pattern rather
    #: than a time type so the wire shape stays the string the UI edits.
    executionTime: str = Field(default="00:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    state: State = "Draft"
    params: dict[str, Any] = Field(default_factory=dict)
    caseRouting: CaseRouting | None = None
    comparison: RuleComparison | None = None
    #: Extra source columns the report should carry, beyond the ones the rule
    #: uses. "1:col"/"2:col" for a two-table reconciliation; bare names for a
    #: single-table rule.
    reportColumns: list[str] = Field(default_factory=list)


class RuleCreate(RuleBase):
    """The assurance is a path/query concern, not a body field — it comes from
    the screen the author is on, so a body claiming a different one would be a
    second source of truth for the same fact."""


class RuleUpdate(RuleBase):
    """Full replacement of the editable fields. The id and the assurance are
    fixed at creation: moving a rule between assurances would change its
    identity, and re-keying it is a delete plus a create."""


class RuleRow(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    appId: str
    createdBy: str = ""
    createdAt: datetime
    updatedAt: datetime
    #: Present on create/update of a Reconciliation rule: what the compiler and
    #: the first execution did. Carries an `error` instead when compilation
    #: failed — the rule saved either way, so the author is told here rather
    #: than by a failed request.
    reconciliation: dict[str, Any] | None = None
