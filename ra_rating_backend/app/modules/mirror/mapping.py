"""Pure mapping: platform rows in, `canonical_rating` row dicts out.

No I/O in this module. Everything here is a function of its arguments, which is
what lets ``tests/test_mirror_mapping.py`` cover every skip decision without a
database anywhere near it.

The return convention is uniform: a mapper returns either the rows, or raises
:class:`Unmappable` carrying the reason. The caller logs the reason and moves on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from app.modules.mirror import targetvocab as tv
from app.modules.mirror import valuemaps as vm
from app.modules.mirror.targetvocab import UnsupportedAction
from app.modules.rules.constants import Operator
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
from app.modules.rules.vocabulary.values import ValueType


class Unmappable(Exception):
    """The target schema cannot express this row faithfully.

    Raised rather than returning a best-effort approximation. A mirrored rule
    that silently drops a negation or files itself under the wrong stage is worse
    than a rule that is simply absent, because the absence is visible.
    """


@dataclass(slots=True)
class RuleRows:
    rule: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]


# --- Helpers -----------------------------------------------------------------


def _clip(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    return value[:length]


def _as_start(value: date | datetime | None) -> datetime:
    """Platform validity starts are DATEs; the target wants timestamptz."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return datetime.now(UTC)


def _as_end(value: date | datetime | None, *, start: datetime) -> datetime | None:
    """Platform ``effective_to`` is an INCLUSIVE date; the target's CHECK demands
    ``effective_to > effective_from``.

    Converting an inclusive end date to the exclusive timestamp at the start of
    the following day satisfies the constraint and is what the date actually
    meant. Treating it as midnight *of* that day would silently shorten every
    rule by a day, and a same-day window would fail the CHECK outright.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        end = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        end = datetime.combine(value + timedelta(days=1), time.min, tzinfo=UTC)
    if end <= start:
        raise Unmappable(f"effective_to {end.isoformat()} is not after {start.isoformat()}")
    return end


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --- Target-vocabulary translation helpers -----------------------------------


def _target_operator(platform_operator: str) -> str:
    """Platform operator → the target's short code (EQUALS → EQ)."""
    code = tv.OPERATOR_MAP.get(platform_operator)
    if code is None:
        raise Unmappable(f"operator '{platform_operator}' has no target code")
    return code


def _condition_value_text(value: Any, value_type: str, scale: Decimal | None) -> str:
    """One condition value in the target's own literal style.

    Two normalisations, both taken from the operator's own sample rows:
    booleans are stored uppercase (``FALSE``), and a scaled attribute divides
    its numeric value (bytes → volume_kb).
    """
    text = _text(value)
    if value_type == "BOOLEAN":
        return text.upper()
    if scale is not None:
        try:
            return format(Decimal(text) / scale, "f")
        except ArithmeticError as exc:
            raise Unmappable(f"'{text}' is not numeric, so it cannot be rescaled") from exc
    return text


# The UI/catalogue distinguishes local on-net and local off-net zones. The
# canonical-rating schema supplied by the operator deliberately does not: its
# sample rules use destination_zone = LOCAL for both. Keep that lossy
# normalisation at the mirror boundary; the source rule and the rating engine
# continue to retain and use LOCAL_ONNET / LOCAL_OFFNET unchanged.
_LOCAL_DESTINATION_ZONES = frozenset(
    {"LOCAL", "LOCAL_ONNET", "LOCAL_OFFNET", "ONNET", "OFFNET", "ON_NET", "OFF_NET"}
)


def _target_condition_value(attribute_name: str, value: str) -> str:
    if attribute_name == "destination_zone":
        normalised = value.strip().upper().replace("-", "_").replace(" ", "_")
        if normalised in _LOCAL_DESTINATION_ZONES:
            return "LOCAL"
    return value


def _synthetic_condition_id(rule_id: str, kind: str, group: int) -> str:
    """Stable target-only id that always fits varchar(50)."""
    candidate = f"{rule_id}:{kind}:{group}"
    if len(candidate) <= 50:
        return candidate
    digest = sha256(candidate.encode("utf-8")).hexdigest()[:40]
    return f"mirror:{digest}"


def _prepend_service_type(
    *,
    rule_id: str,
    service_type: str | None,
    rows: list[dict[str, Any]],
    created_at: datetime | None,
) -> list[dict[str, Any]]:
    """Materialise the rule-header service scope as target conditions.

    The source models keep service_type on the rule header, while the target
    keeps all matching dimensions in rating_rule_condition. For an OR-of-ANDs,
    the header predicate must be repeated in every group; adding it to only one
    group would allow the other groups to match a different service.
    """
    service = (service_type or "").strip().upper()
    if not service or service == "ANY":
        return rows

    groups = sorted({int(row["condition_group"]) for row in rows}) or [1]
    for row in rows:
        row["sequence_no"] = int(row["sequence_no"]) + 1

    service_rows = [
        {
            "condition_id": _synthetic_condition_id(rule_id, "service", group),
            "rule_id": rule_id,
            "condition_group": group,
            "sequence_no": 1,
            "attribute_name": "service_type",
            "operator_code": "EQ",
            "value_type": "STRING",
            "comparison_value": service,
            "comparison_value_to": None,
            "case_sensitive": False,
            "created_at": created_at,
        }
        for group in groups
    ]
    return sorted(
        [*service_rows, *rows],
        key=lambda row: (
            int(row["condition_group"]),
            int(row["sequence_no"]),
            str(row["condition_id"]),
        ),
    )


# --- Condition-group flattening ----------------------------------------------

#: The target holds `condition_group` as a plain integer, which can express
#: exactly one shape: an OR of ANDs. The platform holds an arbitrary-depth tree
#: with per-group negation. Everything the integer can carry is mapped; anything
#: else raises.


def flatten_condition_groups(
    *, condition_logic: str, groups: list[Any], conditions: list[Any]
) -> dict[str, int]:
    """condition_id → target `condition_group`. Raises when the tree cannot be
    represented by a flat integer without changing what the rule means."""
    if not conditions:
        return {}
    if any(getattr(g, "negated_flag", False) for g in groups):
        raise Unmappable("a negated condition group has no target representation")

    roots = [g for g in groups if g.parent_group_id is None]
    children = [g for g in groups if g.parent_group_id is not None]

    if any(c.parent_group_id in {ch.condition_group_id for ch in children} for c in children):
        raise Unmappable("condition nesting deeper than two levels")

    conds_by_group: dict[str, list[Any]] = {}
    for cond in conditions:
        conds_by_group.setdefault(cond.condition_group_id, []).append(cond)

    # --- Two levels: one root OR'ing a set of AND groups ---------------------
    if children:
        if len(roots) != 1:
            raise Unmappable("more than one root group alongside nested groups")
        root = roots[0]
        if conds_by_group.get(root.condition_group_id):
            raise Unmappable("root group mixes its own conditions with subgroups")
        if root.group_logic != "OR":
            raise Unmappable(f"root group logic '{root.group_logic}' with subgroups")
        if any(c.group_logic != "AND" for c in children):
            raise Unmappable("an OR subgroup inside an OR root cannot be flattened")
        ordered = sorted(children, key=lambda g: (g.sequence_number, g.condition_group_id))
        index_of = {g.condition_group_id: i + 1 for i, g in enumerate(ordered)}
        return {
            c.rule_condition_id: index_of[c.condition_group_id]
            for c in conditions
            if c.condition_group_id in index_of
        }

    # --- One flat group -----------------------------------------------------
    if len(roots) == 1:
        root = roots[0]
        if root.group_logic == "AND":
            return {c.rule_condition_id: 1 for c in conditions}
        # A single OR group: each predicate becomes its own group, which is
        # precisely what an OR of one-condition groups means.
        ordered = sorted(conditions, key=lambda c: (c.sequence_number, c.rule_condition_id))
        return {c.rule_condition_id: i + 1 for i, c in enumerate(ordered)}

    # --- Several sibling groups, no nesting ---------------------------------
    if any(g.group_logic != "AND" for g in roots if conds_by_group.get(g.condition_group_id)):
        raise Unmappable("an OR sibling group cannot be flattened")
    if condition_logic == "AND":
        # AND of ANDs is one flat AND.
        return {c.rule_condition_id: 1 for c in conditions}
    if condition_logic == "OR":
        ordered = sorted(roots, key=lambda g: (g.sequence_number, g.condition_group_id))
        index_of = {g.condition_group_id: i + 1 for i, g in enumerate(ordered)}
        return {
            c.rule_condition_id: index_of[c.condition_group_id]
            for c in conditions
            if c.condition_group_id in index_of
        }
    raise Unmappable(f"unrecognised top-level condition logic '{condition_logic}'")


# --- Rule --------------------------------------------------------------------


def map_rule(
    rule: Any,
    version: Any,
    *,
    stage_code: str,
    rule_type_code: str,
    allows_multiple: bool = False,
    action_codes: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    """One `rating_rule` row, per **rule version**.

    Keying the target row on ``rule_version_id`` rather than ``rule_id`` is the
    central mapping decision. The target has a ``version_no`` column, an
    effective window and a status — it is shaped to hold one row per version and
    let a reader pick the one live at an event's timestamp. Collapsing onto the
    logical rule would keep only the newest statement and make every historical
    rating result un-re-explainable, which is the platform's whole purpose.

    The logical identity (``rule.rule_key``) has no target column and is not
    mirrored; see the mapping document.
    """
    stage = vm.target_stage(stage_code)
    if stage is None:
        raise Unmappable(f"stage '{stage_code}' has no equivalent in the target's seven")

    status = vm.target_status(version.status)
    if status is None:
        raise Unmappable(f"status '{version.status}' has no target equivalent")

    start = _as_start(version.effective_from)
    end = _as_end(version.effective_to, start=start)

    currency = version.currency_code
    if currency is not None and len(currency) != 3:
        currency = None

    # The platform expresses "how many rules may fire" with two orthogonal
    # fields; the target knows exactly two strategies. A stackable rule is
    # ALL_MATCHES; everything else — stop_processing, exclusive, best-match
    # ranking the target cannot express — reduces to FIRST_MATCH.
    strategy = "ALL_MATCHES" if (allows_multiple and not version.stop_processing) else "FIRST_MATCH"

    return {
        "rule_id": version.rule_version_id,
        "rule_name": _clip(rule.rule_name, 250),
        "rule_description": _clip(rule.description or None, 1000),
        "rule_stage": stage,
        "rule_type": _clip(tv.rule_type_target(rule_type_code, set(action_codes)), 50),
        "priority": version.priority,
        "match_strategy": strategy,
        "currency_code": currency,
        "account_scope": vm.target_account_scope(rule.charging_mode),
        "effective_from": start,
        "effective_to": end,
        "version_no": version.version_number,
        "status": status,
        # The target FKs this to `canonical_rating.source_system`, a table
        # outside the ten this feature may populate. Writing a code with no
        # parent row would fail the constraint, so it stays NULL.
        "source_system_code": None,
        "created_by": _clip(version.created_by or rule.created_by or "SYSTEM", 100),
        "created_at": _as_start(version.created_at),
        "approved_by": _clip(version.approved_by, 100),
        "approved_at": version.approved_at,
    }


# --- Conditions --------------------------------------------------------------


def map_conditions(
    *,
    rule_id: str,
    conditions: list[Any],
    group_index: dict[str, int],
    service_type: str | None = None,
    service_created_at: datetime | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for cond in sorted(conditions, key=lambda c: (c.sequence_number, c.rule_condition_id)):
        group = group_index.get(cond.rule_condition_id)
        if group is None:
            raise Unmappable(f"condition {cond.rule_condition_id} has no flattened group")

        operator = cond.operator_code
        if cond.negated_flag:
            inverted = vm.invert_operator(operator)
            if inverted is None:
                raise Unmappable(
                    f"operator '{operator}' is negated and has no inverse; the target "
                    "has no per-condition negation flag"
                )
            operator = inverted

        # Attribute + value type in the TARGET's vocabulary. A mapped attribute
        # carries the type the operator's own table declares for it; a
        # pass-through attribute derives one from the platform registry.
        target_name, target_type, scale = tv.attribute_target(cond.attribute_name)
        if not target_type:
            attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute_name)
            target_type = vm.target_condition_value_type(
                cond.comparison_value_type, attr.data_type if attr else None
            )
            if target_type == "ARRAY":
                target_type = "STRING"  # element type; the ARRAY case is below
        if target_type is None:
            raise Unmappable(
                f"value type '{cond.comparison_value_type}' on '{cond.attribute_name}' "
                "has no target equivalent"
            )
        value_type = target_type

        value: str | None = (
            _target_condition_value(
                target_name,
                _condition_value_text(cond.comparison_value, value_type, scale),
            )
            if cond.comparison_value
            else None
        )
        value_to: str | None = None
        elements = list(cond.comparison_values or [])
        if operator in (Operator.BETWEEN,) or cond.comparison_value_type == ValueType.RANGE:
            if len(elements) != 2:
                raise Unmappable("BETWEEN without exactly two values")
            value = _target_condition_value(
                target_name, _condition_value_text(elements[0], value_type, scale)
            )
            value_to = _target_condition_value(
                target_name, _condition_value_text(elements[1], value_type, scale)
            )
        elif cond.comparison_value_type == ValueType.LIST or operator in (
            Operator.IN,
            Operator.NOT_IN,
        ):
            # ARRAY has no element columns; JSON keeps the set intact and ordered.
            value = json.dumps(
                [
                    _target_condition_value(
                        target_name, _condition_value_text(e, target_type, scale)
                    )
                    for e in elements
                ]
            )
            value_type = "ARRAY"
        elif operator in (Operator.EXISTS, Operator.NOT_EXISTS):
            value = None

        # `uq_rule_condition_sequence` is (rule_id, condition_group, sequence_no).
        # Platform sequence numbers restart per group, so a per-group counter is
        # what keeps them unique here too.
        seq = 1
        while (group, seq) in seen:
            seq += 1
        seen.add((group, seq))

        rows.append(
            {
                "condition_id": cond.rule_condition_id,
                "rule_id": rule_id,
                "condition_group": group,
                "sequence_no": seq,
                "attribute_name": _clip(target_name, 100),
                "operator_code": _clip(_target_operator(operator), 30),
                "value_type": value_type,
                "comparison_value": value,
                "comparison_value_to": value_to,
                # The platform has no case-sensitivity flag on a predicate; its
                # comparisons are case-sensitive by construction. False is the
                # target's own default and matches that behaviour.
                "case_sensitive": False,
                "created_at": cond.created_at,
            }
        )
    return _prepend_service_type(
        rule_id=rule_id,
        service_type=service_type,
        rows=rows,
        created_at=service_created_at,
    )


# --- Actions -----------------------------------------------------------------


def _action_translation_context(
    actions_and_params: list[tuple[Any, dict[str, Any]]],
    *,
    tax_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facts shared by target action rows belonging to one rule."""
    context: dict[str, Any] = {"tax_rates": tax_rates or {}}
    for action, params in actions_and_params:
        if (
            action.action_type in {"SET_RATE", "SET_TIERED_RATE", "SET_RATING_UNIT"}
            and params.get("unit") not in (None, "")
        ):
            context.setdefault("unit", params["unit"])
        if action.action_type == "SET_PULSE":
            pulse = params.get("initial_seconds")
            if pulse not in (None, ""):
                context.setdefault("pulse_seconds", pulse)
    return context


def map_actions(
    *,
    rule_id: str,
    actions: list[Any],
    parameters: list[Any],
    tax_rates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """`rating_rule_action` rows.

    The target's grain is one row per **action parameter**, not per action:
    ``parameter_name`` and ``parameter_value`` are both NOT NULL. Translation is
    the target-vocabulary function in :mod:`targetvocab` — one platform action
    can fan out over several target action types (SET_RATE carries the rate,
    SET_RATING_UNIT the unit), ordered by a single ``sequence_no`` counter
    because ``uq_rule_action_sequence`` is (rule_id, sequence_no).
    """
    params_by_action: dict[str, list[Any]] = {}
    for param in parameters:
        if param.rule_action_id:
            params_by_action.setdefault(param.rule_action_id, []).append(param)

    prepared: list[tuple[Any, dict[str, Any]]] = []
    for action in sorted(actions, key=lambda a: (a.execution_sequence, a.rule_action_id)):
        # Assemble the parameter dict the translator consumes: parameter rows
        # first, then the promoted principal value and typed side-columns as
        # fallbacks for actions whose parameters were never row-ified.
        params: dict[str, Any] = {}
        for param in sorted(
            params_by_action.get(action.rule_action_id, []),
            key=lambda p: (p.parameter_name, p.sequence_number),
        ):
            params.setdefault(param.parameter_name, param.parameter_value)
        spec = ACTION_BY_CODE.get(action.action_type)
        if spec is not None and spec.value_param and action.action_value is not None:
            params.setdefault(spec.value_param, action.action_value)
        if action.currency_code:
            params.setdefault("currency", action.currency_code)
        if action.unit_code:
            params.setdefault("unit", action.unit_code)
        prepared.append((action, params))

    context = _action_translation_context(prepared, tax_rates=tax_rates)
    rows: list[dict[str, Any]] = []
    seq = 0
    for action, params in prepared:
        try:
            translated = tv.translate_action(action.action_type, params, context=context)
        except UnsupportedAction as exc:
            raise Unmappable(str(exc)) from exc

        for n, (target_type, name, value, value_type) in enumerate(translated, start=1):
            seq += 1
            rows.append(
                {
                    "action_id": _clip(f"{action.rule_action_id}:{n}", 50),
                    "rule_id": rule_id,
                    "sequence_no": seq,
                    "action_type": target_type,
                    "parameter_name": _clip(name, 100),
                    "parameter_value": value,
                    "value_type": value_type,
                    "created_at": action.created_at,
                }
            )

    if not rows:
        raise Unmappable("no mirrorable actions")
    return rows


# --- Legacy rule model -------------------------------------------------------

#: The legacy model is what the rule-management UI actually writes:
#: `rating.rules` + `rule_conditions` + `rule_actions`, one row per version keyed
#: on (rule_key, version). `RULE_COMPILE_SOURCE` still defaults to LEGACY, so
#: this — not the canonical model — is the live estate in a default deployment.
#:
#: It maps onto the target more directly than the canonical model does, because
#: the target's flat shape and the legacy shape make the same simplifications:
#: an integer condition group instead of a tree, no per-parameter table, one row
#: per version.

def map_legacy_rule(rule: Any) -> dict[str, Any]:
    """One `rating_rule` row from a legacy `rating.rules` row."""
    stage = vm.target_stage(rule.execution_stage)
    if stage is None:
        raise Unmappable(
            f"stage '{rule.execution_stage}' has no equivalent in the target's seven"
        )
    status = vm.target_status(rule.status)
    if status is None:
        raise Unmappable(f"status '{rule.status}' has no target equivalent")

    start = _as_start(rule.effective_from)
    end = _as_end(rule.effective_to, start=start)

    currency = rule.currency_code
    if currency is not None and len(currency) != 3:
        currency = None

    return {
        "rule_id": rule.id,
        "rule_name": _clip(rule.name, 250),
        "rule_description": _clip(rule.description or None, 1000),
        "rule_stage": stage,
        "rule_type": _clip(
            tv.rule_type_target(rule.rule_type, {a.action_type for a in rule.actions}), 50
        ),
        "priority": rule.priority,
        # The target knows exactly two strategies; the platform's EXCLUSIVE /
        # OVERRIDE ranking semantics both reduce to FIRST_MATCH.
        "match_strategy": tv.LEGACY_STRATEGY_MAP.get(rule.stacking_policy, "FIRST_MATCH"),
        "currency_code": currency,
        # The legacy model has no charging-mode column — it predates the
        # prepaid/postpaid split — so every legacy rule is scoped to BOTH.
        "account_scope": "BOTH",
        "effective_from": start,
        "effective_to": end,
        "version_no": rule.version,
        "status": status,
        "source_system_code": None,
        "created_by": _clip(rule.created_by or "SYSTEM", 100),
        "created_at": _as_start(rule.created_at),
        "approved_by": _clip(rule.approved_by, 100),
        "approved_at": rule.approved_at,
    }


def map_legacy_conditions(rule: Any) -> list[dict[str, Any]]:
    """`rating_rule_condition` rows from legacy `rule_conditions`.

    Legacy `group_index` is already the integer the target wants, so no tree
    flattening is needed — the two models agree on the shape. The one adjustment
    is that a rule whose top-level logic is AND has its groups collapsed to 1,
    because distinct group numbers in the target are OR'd.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    collapse = (rule.condition_logic or "AND") == "AND"

    for cond in sorted(rule.conditions, key=lambda c: (c.sequence, c.id)):
        operator = cond.operator
        if cond.negate:
            inverted = vm.invert_operator(operator)
            if inverted is None:
                raise Unmappable(
                    f"operator '{operator}' is negated and has no inverse; the target "
                    "has no per-condition negation flag"
                )
            operator = inverted

        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is None:
            raise Unmappable(f"'{cond.attribute}' is not a known rating attribute")
        # Attribute + value type in the TARGET's vocabulary. A mapped attribute
        # carries the type the operator's own table declares; a pass-through
        # falls back to the platform registry — legacy conditions carry no
        # declared type of their own, which is the gap the canonical model
        # was built to close.
        target_name, base_type, scale = tv.attribute_target(cond.attribute)
        if not base_type:
            base_type = vm.DATA_TYPE_MAP.get(attr.data_type)
        if base_type is None:
            raise Unmappable(f"data type '{attr.data_type}' has no target equivalent")

        values = list(cond.values or [])
        value: str | None
        value_to: str | None = None
        if operator in (Operator.EXISTS, Operator.NOT_EXISTS):
            value, value_type = None, base_type
        elif operator in (Operator.IN, Operator.NOT_IN):
            value = json.dumps(
                [
                    _target_condition_value(
                        target_name, _condition_value_text(v, base_type, scale)
                    )
                    for v in values
                ]
            )
            value_type = "ARRAY"
        elif operator == Operator.BETWEEN:
            if len(values) != 2:
                raise Unmappable("BETWEEN without exactly two values")
            value = _target_condition_value(
                target_name, _condition_value_text(values[0], base_type, scale)
            )
            value_to = _target_condition_value(
                target_name, _condition_value_text(values[1], base_type, scale)
            )
            value_type = base_type
        else:
            value = (
                _target_condition_value(
                    target_name, _condition_value_text(values[0], base_type, scale)
                )
                if values
                else None
            )
            value_type = base_type

        group = 1 if collapse else int(cond.group_index) + 1
        seq = 1
        while (group, seq) in seen:
            seq += 1
        seen.add((group, seq))

        rows.append(
            {
                "condition_id": cond.id,
                "rule_id": rule.id,
                "condition_group": group,
                "sequence_no": seq,
                "attribute_name": _clip(target_name, 100),
                "operator_code": _clip(_target_operator(operator), 30),
                "value_type": value_type,
                "comparison_value": value,
                "comparison_value_to": value_to,
                "case_sensitive": False,
                "created_at": cond.created_at,
            }
        )
    return _prepend_service_type(
        rule_id=rule.id,
        service_type=getattr(rule, "service_type", None),
        rows=rows,
        created_at=getattr(rule, "created_at", None),
    )


def map_legacy_actions(
    rule: Any, *, tax_rates: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """`rating_rule_action` rows from legacy `rule_actions`.

    Legacy holds parameters in a JSONB ``params`` dict; the target wants one row
    per parameter, in the TARGET's own action vocabulary. Translation is
    :func:`targetvocab.translate_action` — one platform action fans out over
    the target's action types (SET_RATE carries the rate, SET_RATING_UNIT the
    unit). An action the 14-type registry cannot express refuses the whole
    rule: a rule mirrored minus one of its actions matches and then charges
    differently, which is worse than an absence.
    """
    prepared = [
        (action, dict(action.params or {}))
        for action in sorted(rule.actions, key=lambda a: (a.sequence, a.id))
    ]
    context = _action_translation_context(prepared, tax_rates=tax_rates)

    rows: list[dict[str, Any]] = []
    seq = 0
    for action, params in prepared:
        try:
            translated = tv.translate_action(action.action_type, params, context=context)
        except UnsupportedAction as exc:
            raise Unmappable(str(exc)) from exc

        for n, (target_type, name, value, value_type) in enumerate(translated, start=1):
            seq += 1
            rows.append(
                {
                    # The legacy action id is one row; the target needs one per
                    # parameter, so a counter disambiguates.
                    "action_id": _clip(f"{action.id}:{n}", 50),
                    "rule_id": rule.id,
                    "sequence_no": seq,
                    "action_type": target_type,
                    "parameter_name": _clip(name, 100),
                    "parameter_value": value,
                    "value_type": value_type,
                    "created_at": action.created_at,
                }
            )

    if not rows:
        raise Unmappable("no mirrorable actions")
    return rows


# --- Metadata catalogue ------------------------------------------------------


def map_destination_prefix(prefix: Any, zone: Any) -> dict[str, Any]:
    """`destination_prefix`, denormalising the zone the target has no table for."""
    if zone is None:
        raise Unmappable("prefix has no resolvable destination zone")
    start = _as_start(prefix.created_at)
    return {
        "prefix": _clip(prefix.prefix, 40),
        "zone_code": _clip(zone.code, 50),
        # The target's `destination_type` is the zone's kind (NATIONAL,
        # INTERNATIONAL, ON_NET …), which is exactly `zone_type`.
        "destination_type": _clip(zone.zone_type, 50),
        "country_code": _clip(zone.country_code, 10),
        # No platform column carries a terminating operator for a prefix.
        "operator_code": None,
        "priority": 100,
        "effective_from": start,
        "effective_to": None,
        "status": vm.target_catalog_status(zone.status),
    }


def map_time_band(band: Any) -> list[dict[str, Any]]:
    """`time_band` rows — possibly several for one platform band.

    Two expansions happen here:

    * ``days`` is a list and ``day_type`` is a single value, so a band on an
      arbitrary set of days becomes one row per day.
    * ``chk_time_band_seconds`` requires ``start_second < end_second``, but a
      platform band may wrap midnight (22:00 → 06:00). A wrapping band is split
      into ``[start, 86400)`` and ``[0, end)``, which is the same coverage
      expressed the only way the target permits.
    """
    start_s = band.start_time.hour * 3600 + band.start_time.minute * 60 + band.start_time.second
    end_s = band.end_time.hour * 3600 + band.end_time.minute * 60 + band.end_time.second
    if end_s == 0:
        end_s = 86_400
    spans = [(start_s, end_s)] if start_s < end_s else [(start_s, 86_400), (0, end_s)]
    spans = [(a, b) for a, b in spans if a < b]
    if not spans:
        raise Unmappable("time band covers no seconds")

    effective_from = _as_start(band.created_at)
    status = vm.target_catalog_status(band.status)
    rows = []
    for day_type in vm.target_day_types(band.days):
        for span_start, span_end in spans:
            rows.append(
                {
                    "time_band_code": _clip(band.code, 50),
                    "time_band_name": _clip(band.name, 150),
                    "day_type": day_type,
                    "start_second": span_start,
                    "end_second": span_end,
                    "timezone_name": _clip(band.timezone or "UTC", 100),
                    "priority": band.priority,
                    "effective_from": effective_from,
                    "effective_to": None,
                    "status": status,
                }
            )
    return rows


# --- Subscriber plane --------------------------------------------------------


def map_subscriber_offer(assignment: Any) -> dict[str, Any]:
    """`subscriber_offer` from a `subscriber_products` row."""
    if not assignment.offer_code:
        raise Unmappable("subscriber product carries no offer code")
    start = _as_start(assignment.effective_from)
    end = _as_end(assignment.effective_to, start=start)
    status = "ACTIVE"
    if end is not None and end <= datetime.now(UTC):
        status = "EXPIRED"
    return {
        "subscriber_id": _clip(assignment.subscriber_id, 50),
        "offer_id": _clip(assignment.offer_code, 50),
        "status": status,
        "effective_from": start,
        "effective_to": end,
        "priority": 100,
    }


def map_subscriber_bundle_balance(bucket: Any) -> dict[str, Any]:
    """`subscriber_bundle_balance` from a `balance_buckets` row."""
    owner = bucket.subscriber_id or bucket.owner_key
    if not owner:
        raise Unmappable("balance bucket has no subscriber")
    allocated = Decimal(str(bucket.allocated or 0))
    consumed = Decimal(str(bucket.consumed or 0))
    # The target's CHECK forbids a negative remainder; overflow is tracked
    # separately on the platform side and never drives the balance below zero.
    remaining = max(Decimal("0"), allocated - consumed)
    start = _as_start(bucket.period_start)
    end = _as_end(bucket.period_end, start=start)
    if remaining == 0:
        status = "EXHAUSTED"
    elif end is not None and end <= datetime.now(UTC):
        status = "EXPIRED"
    else:
        status = "ACTIVE"
    return {
        "subscriber_id": _clip(owner, 50),
        "bundle_code": _clip(bucket.bundle_code, 50),
        # No separate balance-type dimension on a platform bucket; the service it
        # is denominated for is the closest true statement.
        "balance_type": _clip(bucket.service_type, 50),
        "initial_balance": allocated,
        "remaining_balance": remaining,
        "unit_of_measure": _clip(bucket.quota_unit, 30),
        "priority": 100,
        "effective_from": start,
        "effective_to": end,
        "status": status,
        "last_updated_at": bucket.last_consumed_at or bucket.updated_at,
    }
