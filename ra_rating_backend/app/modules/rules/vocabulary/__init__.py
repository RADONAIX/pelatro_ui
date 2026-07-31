"""The canonical rule vocabulary.

One registry for what a rule can *be* (types), *where it runs* (stages), *what it
does* (actions), *what it tests* (attributes) and *how vendor words map onto ours*
(aliases). This package is the single source for three consumers that must never
disagree:

1. the ``ra_rule.rule_type`` / ``rule_stage`` / ``rule_stacking_policy`` tables,
   seeded and kept in step by :func:`app.modules.rules.vocabulary.sync.sync_vocabulary`;
2. the ``/api/rating/meta/*`` dictionaries the UI builder is generated from;
3. the validator's contracts.

Relationship to ``rules/constants.py``: the legacy module is untouched and still
drives the live rating path. This package is a strict superset, and
``tests/test_canonical_vocabulary.py`` fails if the overlap ever diverges. The
R4 cut-over retires the legacy module once the compiler and engine read from here.
"""

from __future__ import annotations

from app.modules.rules.vocabulary.actions import (
    ACTION_BY_CODE,
    CANONICAL_ACTIONS,
    ActionSpec,
    ParamSpec,
    actions_for_mode,
)
from app.modules.rules.vocabulary.aliases import (
    ACTION_ALIASES,
    ATTRIBUTE_ALIASES,
    CHARGING_MODE_ALIASES,
    RULE_TYPE_ALIASES,
    resolve_action,
    resolve_attribute,
    resolve_charging_mode,
    resolve_rule_type,
)
from app.modules.rules.vocabulary.attributes import (
    ATTRIBUTE_MODE_SCOPE,
    CANONICAL_ATTRIBUTE_BY_KEY,
    CANONICAL_ATTRIBUTES,
    attributes_for_mode,
)
from app.modules.rules.vocabulary.modes import (
    CATEGORIES_FOR_MODE,
    ChargingMode,
    ConflictResolution,
    DependencyType,
    ExecutionMode,
    FallbackPolicy,
    FallbackScope,
    RuleCategory,
    RuleSetType,
    ValidationState,
)
from app.modules.rules.vocabulary.stages import (
    CANONICAL_STAGES,
    LEGACY_STAGE_CODES,
    STAGE_BY_CODE,
    StageSpec,
    pipeline_for,
    stage_codes_for,
)
from app.modules.rules.vocabulary.types import (
    CANONICAL_RULE_TYPE_STAGE,
    CANONICAL_RULE_TYPES,
    RULE_TYPE_BY_CODE,
    RuleTypeSpec,
    rule_types_for_mode,
)

__all__ = [
    "ACTION_ALIASES",
    "ACTION_BY_CODE",
    "ATTRIBUTE_ALIASES",
    "ATTRIBUTE_MODE_SCOPE",
    "CANONICAL_ACTIONS",
    "CANONICAL_ATTRIBUTES",
    "CANONICAL_ATTRIBUTE_BY_KEY",
    "CANONICAL_RULE_TYPES",
    "CANONICAL_RULE_TYPE_STAGE",
    "CANONICAL_STAGES",
    "CATEGORIES_FOR_MODE",
    "CHARGING_MODE_ALIASES",
    "LEGACY_STAGE_CODES",
    "RULE_TYPE_ALIASES",
    "RULE_TYPE_BY_CODE",
    "STAGE_BY_CODE",
    "ActionSpec",
    "ChargingMode",
    "ConflictResolution",
    "DependencyType",
    "ExecutionMode",
    "FallbackPolicy",
    "FallbackScope",
    "ParamSpec",
    "RuleCategory",
    "RuleSetType",
    "RuleTypeSpec",
    "StageSpec",
    "ValidationState",
    "actions_for_mode",
    "attributes_for_mode",
    "pipeline_for",
    "resolve_action",
    "resolve_attribute",
    "resolve_charging_mode",
    "resolve_rule_type",
    "rule_types_for_mode",
    "stage_codes_for",
]
