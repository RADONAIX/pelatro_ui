"""The canonical vocabulary, published so the builder can be generated from it.

``/meta/attributes``, ``/meta/actions`` and ``/meta/enums`` next door still serve
the *legacy* registry, and deliberately keep doing so until the R4 cut-over: the
UI screens built against them must not change shape underneath a running system.

These endpoints serve the canonical registry — 39 rule types, 31 mode-scoped
stages, 35 actions, 34 attributes. Without them the vocabulary exists in Python
and in Postgres and is invisible to anything that would use it, which is the
state the whole R1 phase was in until now.

The one that carries the design is ``/meta/pipelines``. It answers "which stages
does a prepaid rule run through, in what order" from the same registry the
mode-coherence validator enforces against, so the wizard filtering its action
palette and the validator rejecting an out-of-pipeline action can never disagree.
A UI that hard-coded that list would drift the first time a stage was added.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser
from app.modules.rules.constants import (
    OPERATOR_ARITY,
    OPERATOR_LABELS,
    OPERATORS_BY_TYPE,
)
from app.modules.rules.vocabulary.actions import CANONICAL_ACTIONS, actions_for_mode
from app.modules.rules.vocabulary.aliases import (
    ACTION_ALIASES,
    ATTRIBUTE_ALIASES,
    CHARGING_MODE_ALIASES,
    RULE_TYPE_ALIASES,
)
from app.modules.rules.vocabulary.attributes import (
    ATTRIBUTE_MODE_SCOPE,
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
from app.modules.rules.vocabulary.policies import CANONICAL_STACKING_POLICIES
from app.modules.rules.vocabulary.stages import CANONICAL_STAGES, pipeline_for
from app.modules.rules.vocabulary.types import CANONICAL_RULE_TYPES, rule_types_for_mode
from app.modules.rules.vocabulary.values import (
    CURRENCY_BEARING_TYPES,
    NUMERIC_VALUE_TYPES,
    UNIT_BEARING_TYPES,
    UnitDimension,
    ValueType,
)

router = APIRouter(prefix="/meta", tags=["meta"])

#: Charging mode as a query parameter, described once. Passing it filters the
#: response to what an author working in that mode may actually use — which is
#: what stops the wizard offering ADD_INVOICE_COMPONENT on a prepaid rule.
_MODE = Query(
    None,
    description="PREPAID | POSTPAID | BOTH. Filters to what this mode can use.",
)


@router.get("/charging-modes", summary="Charging modes and the stages each reaches")
async def charging_modes(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "code": mode.value,
            "label": mode.value.title(),
            "stage_categories": list(CATEGORIES_FOR_MODE[mode]),
            "stage_count": len(pipeline_for(mode)),
            "rule_type_count": len(rule_types_for_mode(mode)),
        }
        for mode in ChargingMode
    ]


@router.get("/rule-categories", summary="The three rule families")
async def rule_categories(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "code": category.value,
            "label": category.value.title(),
            "stage_count": sum(
                1 for s in CANONICAL_STAGES if s.applies_to == category
            ),
            "rule_type_count": sum(
                1 for t in CANONICAL_RULE_TYPES if t.rule_category == category
            ),
        }
        for category in RuleCategory
    ]


@router.get("/rule-stages", summary="Execution stages, mode-scoped and ordered")
async def rule_stages(
    _: CurrentUser, charging_mode: str | None = _MODE
) -> list[dict[str, Any]]:
    stages = (
        pipeline_for(charging_mode)
        if charging_mode in set(ChargingMode)
        else sorted(CANONICAL_STAGES, key=lambda s: s.execution_order)
    )
    return [
        {
            "code": stage.code,
            "name": stage.name,
            "applies_to": stage.applies_to,
            "execution_order": stage.execution_order,
            "is_stateful": stage.is_stateful,
            "description": stage.description,
        }
        for stage in stages
    ]


@router.get("/pipelines", summary="The ordered stage sequence per charging mode")
async def pipelines(_: CurrentUser) -> dict[str, list[dict[str, Any]]]:
    """One registry, filtered by category — this *is* "one engine, not two".

    The wizard reads this to decide which stages a rule can occupy, and the
    validator enforces the same thing from the same source. Two implementations
    of this list is how prepaid and postpaid quietly become separate engines.
    """
    return {
        mode.value: [
            {
                "code": stage.code,
                "name": stage.name,
                "execution_order": stage.execution_order,
                "applies_to": stage.applies_to,
                "is_stateful": stage.is_stateful,
            }
            for stage in pipeline_for(mode)
        ]
        for mode in ChargingMode
    }


@router.get("/rule-types", summary="Rule types, their stage and required actions")
async def rule_types(
    _: CurrentUser, charging_mode: str | None = _MODE
) -> list[dict[str, Any]]:
    types = (
        rule_types_for_mode(charging_mode)
        if charging_mode in set(ChargingMode)
        else CANONICAL_RULE_TYPES
    )
    return [
        {
            "code": spec.code,
            "name": spec.name,
            "rule_category": spec.rule_category,
            "stage_code": spec.stage_code,
            "charging_mode": spec.charging_mode,
            "required_action_types": list(spec.required_action_types),
            # An operator-facing synonym compiling to identical behaviour —
            # USAGE_RATING is the postpaid word for BASE_TARIFF, and a catalogue
            # filter should find it under the word the operator uses.
            "alias_of": spec.alias_of,
            "description": spec.description,
        }
        for spec in types
    ]


@router.get("/canonical-actions", summary="Canonical actions and their typed parameters")
async def canonical_actions(
    _: CurrentUser, charging_mode: str | None = _MODE
) -> list[dict[str, Any]]:
    """Deliberately not ``/meta/actions``: that path serves the legacy registry
    and screens are built against its shape. Both live until the cut-over."""
    actions = (
        actions_for_mode(charging_mode)
        if charging_mode in set(ChargingMode)
        else CANONICAL_ACTIONS
    )
    return [
        {
            "code": spec.code,
            "label": spec.label,
            "stage_code": spec.stage_code,
            "category": spec.category,
            "target_attribute": spec.target_attribute,
            # Which parameter is promoted to rule_action.action_value, so the UI
            # knows which field is "the" value and can lead the form with it.
            "value_param": spec.value_param,
            "description": spec.description,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "value_type": p.value_type,
                    "required": p.required,
                    "reference": p.reference,
                    "values": list(p.values),
                    "unit_dimension": p.unit_dimension,
                    "repeatable": p.repeatable,
                    "description": p.description,
                }
                for p in spec.params
            ],
        }
        for spec in actions
    ]


@router.get("/canonical-attributes", summary="Canonical condition attributes")
async def canonical_attributes(
    _: CurrentUser, charging_mode: str | None = _MODE
) -> list[dict[str, Any]]:
    attributes = (
        attributes_for_mode(charging_mode)
        if charging_mode in set(ChargingMode)
        else CANONICAL_ATTRIBUTES
    )
    return [
        {
            "key": a.key,
            "label": a.label,
            "data_type": a.data_type,
            "group": a.group,
            "reference": a.reference,
            "values": list(a.values),
            "specificity": a.specificity,
            "description": a.description,
            "operators": list(OPERATORS_BY_TYPE.get(a.data_type, ())),
            # Absent = every mode. Present = the modes whose events carry it, so
            # the builder can grey out "Billing Cycle" on a prepaid rule rather
            # than letting an author write a condition that never matches.
            "charging_modes": list(ATTRIBUTE_MODE_SCOPE.get(a.key, ())),
        }
        for a in attributes
    ]


@router.get("/value-types", summary="Storage types for comparisons and parameters")
async def value_types(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "code": value_type.value,
            "numeric": value_type in NUMERIC_VALUE_TYPES,
            # MONEY without a currency is rejected by the codec. The UI needs to
            # know that before it renders the field, not after the save fails.
            "requires_currency": value_type in CURRENCY_BEARING_TYPES,
            "accepts_unit": value_type in UNIT_BEARING_TYPES,
        }
        for value_type in ValueType
    ]


@router.get("/units", summary="Unit dimensions a rate can be measured in")
async def units(_: CurrentUser) -> dict[str, Any]:
    """Dimensions only. The units themselves are catalogue data — see
    ``/catalog/charging-units`` — because an operator adds one without a deploy."""
    return {
        "dimensions": [d.value for d in UnitDimension],
        "catalogue": "charging-units",
    }


@router.get("/canonical-operators", summary="Operators and their value arity")
async def canonical_operators(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "min_values": OPERATOR_ARITY[key][0],
            "max_values": OPERATOR_ARITY[key][1],
        }
        for key, label in OPERATOR_LABELS.items()
    ]


@router.get("/canonical-enums", summary="Every canonical enumeration the UI needs")
async def canonical_enums(_: CurrentUser) -> dict[str, Any]:
    return {
        "charging_mode": [e.value for e in ChargingMode],
        "rule_category": [e.value for e in RuleCategory],
        "execution_mode": [e.value for e in ExecutionMode],
        "fallback_policy": [e.value for e in FallbackPolicy],
        "fallback_scope": [e.value for e in FallbackScope],
        "conflict_resolution": [e.value for e in ConflictResolution],
        "dependency_type": [e.value for e in DependencyType],
        "rule_set_type": [e.value for e in RuleSetType],
        "validation_state": [e.value for e in ValidationState],
        "value_type": [e.value for e in ValueType],
        "unit_dimension": [e.value for e in UnitDimension],
        "stacking_policy": [p.code for p in CANONICAL_STACKING_POLICIES],
        "stage_categories_for_mode": {
            mode.value: list(categories)
            for mode, categories in CATEGORIES_FOR_MODE.items()
        },
    }


@router.get("/aliases", summary="Vendor synonyms accepted on ingest")
async def aliases(_: CurrentUser) -> dict[str, dict[str, str]]:
    """Published so an operator can see what an import will do to their file
    *before* running it, and so "our file said SET_MINIMUM" has an answer."""
    return {
        "actions": dict(ACTION_ALIASES),
        "attributes": dict(ATTRIBUTE_ALIASES),
        "rule_types": dict(RULE_TYPE_ALIASES),
        "charging_modes": dict(CHARGING_MODE_ALIASES),
    }
