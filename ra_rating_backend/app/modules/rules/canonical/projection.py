"""``canonical_json`` — the denormalized read model, built from the normalized rows.

The normalized tables are the truth: they carry the constraints, the foreign keys,
and the indexes that make "which rules mention zone LOCAL_ONNET" an index scan.
But the compiler and the engine need one whole rule at a time, and assembling that
from five tables per rule is five joins the hot path does not need.

So the writer builds this projection in the *same transaction* as the rows it
derives from, and the compiler reads one JSONB column. The risk of any
denormalization is silent drift; the mitigation is that this function is pure, and
a nightly job re-derives it from the rows and alerts on mismatch. Never hand-edit
``canonical_json`` — regenerate it.

Money is rendered as a **string**, not a JSON number. ``json.dumps`` on a Decimal
either raises or routes through float, and a rate that survived the codec only to
lose precision in its projection would be the same bug one layer down.
"""

from __future__ import annotations

from typing import Any

from app.modules.rules.canonical.draft import CanonicalDraft, DraftConditionGroup
from app.modules.rules.canonical.valuetypes import TypedValue, format_decimal
from app.modules.rules.vocabulary.values import ValueType

#: Bumped when the projection's shape changes, so a consumer can tell a stale
#: projection from a current one instead of guessing from its contents.
PROJECTION_VERSION = 1


def _typed(value: TypedValue) -> dict[str, Any]:
    out: dict[str, Any] = {"type": value.value_type, "text": value.text}
    if value.numeric is not None:
        # A string: JSON has no exact decimal, and float would undo the codec.
        out["numeric"] = format_decimal(value.numeric)
    if value.elements:
        out["elements"] = list(value.elements)
    if value.currency_code:
        out["currency"] = value.currency_code
    if value.unit_code:
        out["unit"] = value.unit_code
    if value.resolved_ref_id:
        out["ref_id"] = value.resolved_ref_id
    return out


def _group(
    group: DraftConditionGroup, encoded: dict[int, TypedValue], path: str = "0"
) -> dict[str, Any]:
    """Serialize one condition group, keyed by the encoded values the writer typed.

    ``encoded`` is keyed by ``id()`` of the draft condition — the writer already
    typed every value, and re-encoding here could produce a projection that
    disagrees with the rows, which is precisely the drift this design fears.
    """
    return {
        "logic": group.logic,
        "negated": group.negated,
        "label": group.label,
        "path": path,
        "conditions": [
            {
                "attribute": cond.attribute,
                "operator": cond.operator,
                "negated": cond.negated,
                "sequence": index,
                "value": _typed(encoded[id(cond)]),
            }
            for index, cond in enumerate(group.conditions)
            if id(cond) in encoded
        ],
        "groups": [
            _group(child, encoded, f"{path}.{i}")
            for i, child in enumerate(group.children)
        ],
    }


def build(
    draft: CanonicalDraft,
    *,
    encoded_conditions: dict[int, TypedValue],
    encoded_parameters: dict[int, TypedValue],
    rule_key: str,
    version_number: int,
    stage_code: str,
    specificity: int,
    behaviour_hash: str,
    resolved: dict[str, str | None],
) -> dict[str, Any]:
    """Assemble the projection. Pure: same inputs, same output, always."""
    from app.modules.rules.vocabulary.actions import ACTION_BY_CODE

    actions: list[dict[str, Any]] = []
    for sequence, action in enumerate(
        sorted(draft.actions, key=lambda a: a.sequence)
    ):
        spec = ACTION_BY_CODE.get(action.action_type)
        params: dict[str, Any] = {}
        ordered: dict[str, list[dict[str, Any]]] = {}
        for param in sorted(action.parameters, key=lambda p: (p.name, p.sequence)):
            typed = encoded_parameters.get(id(param))
            if typed is None:
                continue
            payload = _typed(typed)
            declared = next(
                (p for p in (spec.params if spec else ()) if p.key == param.name), None
            )
            if declared is not None and declared.repeatable:
                ordered.setdefault(param.name, []).append(payload)
            else:
                params[param.name] = payload
        for name, entries in ordered.items():
            params[name] = entries

        principal = None
        if spec and spec.value_param:
            principal = params.get(spec.value_param)
            if isinstance(principal, list):
                principal = None

        actions.append(
            {
                "action_type": action.action_type,
                "target_attribute": action.target_attribute
                or (spec.target_attribute if spec else None),
                "stage": spec.stage_code if spec else stage_code,
                "sequence": sequence,
                "value": principal,
                "params": params,
            }
        )

    rule_params = {
        p.name: _typed(encoded_parameters[id(p)])
        for p in draft.parameters
        if id(p) in encoded_parameters
    }

    return {
        "projection_version": PROJECTION_VERSION,
        "rule_key": rule_key,
        "rule_name": draft.rule_name,
        "version_number": version_number,
        "charging_mode": draft.charging_mode,
        "rule_type": draft.rule_type_code,
        "stage": stage_code,
        "service_type": draft.service_type,
        "conditions": _group(draft.root_group, encoded_conditions),
        "condition_logic": draft.behaviour.condition_logic,
        "actions": actions,
        "parameters": rule_params,
        "targets": {
            "product": draft.targets.product,
            "offer": draft.targets.offer,
            "tariff_plan": draft.targets.tariff_plan,
            "product_id": resolved.get("product_id"),
            "offer_id": resolved.get("offer_id"),
            "tariff_plan_id": resolved.get("tariff_plan_id"),
        },
        "behaviour": {
            "priority": draft.behaviour.priority,
            "specificity_score": specificity,
            "stacking_policy": draft.behaviour.stacking_policy,
            "conflict_group": draft.behaviour.conflict_group,
            "fallback_policy": draft.behaviour.fallback_policy,
            "stop_processing": draft.behaviour.stop_processing,
            "execution_mode": draft.behaviour.execution_mode,
        },
        "validity": {
            "effective_from": draft.validity.effective_from.isoformat(),
            "effective_to": (
                draft.validity.effective_to.isoformat()
                if draft.validity.effective_to
                else None
            ),
            "currency_code": draft.validity.currency_code,
        },
        "dependencies": [
            {"rule_key": d.depends_on_rule_key, "type": d.dependency_type}
            for d in draft.dependencies
        ],
        "fallbacks": [
            {"rule_key": f.fallback_rule_key, "level": f.level, "scope": f.scope}
            for f in draft.fallbacks
        ],
        "behaviour_hash": behaviour_hash,
    }


def condition_count(projection: dict[str, Any]) -> int:
    """How many predicates a projection holds, at any nesting depth."""

    def walk(group: dict[str, Any]) -> int:
        return len(group.get("conditions", ())) + sum(
            walk(child) for child in group.get("groups", ())
        )

    root = projection.get("conditions")
    return walk(root) if isinstance(root, dict) else 0


def flat_conditions(projection: dict[str, Any]) -> list[dict[str, Any]]:
    """Every predicate, flattened, each carrying the group path it came from.

    What the compiler consumes: it needs the predicates and their bracket
    structure, not a tree to walk per CDR.
    """
    out: list[dict[str, Any]] = []

    def walk(group: dict[str, Any]) -> None:
        for cond in group.get("conditions", ()):
            out.append({**cond, "group_path": group.get("path", "0"),
                        "group_logic": group.get("logic", "AND"),
                        "group_negated": group.get("negated", False)})
        for child in group.get("groups", ()):
            walk(child)

    root = projection.get("conditions")
    if isinstance(root, dict):
        walk(root)
    return out


def is_reference(value: dict[str, Any] | None) -> bool:
    return bool(value) and value.get("type") == ValueType.REFERENCE
