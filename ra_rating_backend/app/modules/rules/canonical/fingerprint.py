"""The behaviour hash — what makes "3 changed, 3,997 unchanged" possible.

A vendor export is a full dump every night. Importing 4,000 rules and creating
4,000 new versions each time makes the audit trail useless within a week, so each
incoming rule is compared against what is stored and only a genuine behavioural
difference creates a version.

**What is excluded is the interesting part.** Name, description, category and owner
do not appear: a vendor renaming a plan is not a tariff change, and versioning the
estate for it produces churn that hides the three changes that mattered. What *is*
included is everything that can alter a charge — conditions, actions, parameters,
targets, priority, validity, stacking, and the mode/stage the rule runs at.

Stored indexed on ``rule_version.behaviour_hash``, unlike the legacy fingerprint
that hid inside a JSONB blob and made change detection a sequential scan.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftConditionGroup,
)


def _scalar(value: Any) -> str:
    """One value in the form the *column* holds it, not the form it arrived in.

    A draft built by hand carries ``1``; the same draft read back from the store
    carries ``"1.000000"``, because the codec quantizes every number to six
    decimal places on the way in. Hashing the raw form would make a rule differ
    from itself the moment it made a round trip — and reconciliation, whose whole
    job is to say "3 changed, 3,997 unchanged", would report the entire estate as
    changed on the first re-import.

    Non-numeric values are compared as trimmed, upper-cased text, which is what
    the codec stores for enums and references.
    """
    text = str(value).strip()
    try:
        return format(Decimal(text).quantize(Decimal("0.000001")), "f")
    except (InvalidOperation, ValueError, ArithmeticError):
        return text


def _target_attribute(action: DraftAction) -> str:
    """The target the action will actually be stored with.

    The writer falls back to the action spec's default when a draft leaves it
    unset, so the fingerprint has to make the same substitution or a draft and
    its own stored form disagree.
    """
    if action.target_attribute:
        return action.target_attribute
    from app.modules.rules.vocabulary.actions import ACTION_BY_CODE

    spec = ACTION_BY_CODE.get(action.action_type)
    return (spec.target_attribute if spec else "") or ""


def _group_payload(group: DraftConditionGroup) -> dict[str, Any]:
    """Canonical form of a condition tree.

    Conditions are sorted within a group and groups within their parent, because
    the *order* two ANDed predicates were typed in has no effect on any charge —
    treating a reordering as a change would version the estate for nothing. The
    nesting itself is preserved, since that does change meaning.
    """
    return {
        "logic": group.logic,
        "negated": group.negated,
        "conditions": sorted(
            [
                [
                    c.attribute,
                    c.operator,
                    sorted(_scalar(v).upper() for v in c.values),
                    c.negated,
                    (c.unit or "").upper(),
                    (c.currency or "").upper(),
                ]
                for c in group.conditions
            ],
            key=json.dumps,
        ),
        "children": sorted(
            [_group_payload(child) for child in group.children], key=json.dumps
        ),
    }


def behaviour_payload(draft: CanonicalDraft) -> dict[str, Any]:
    """The exact structure that gets hashed. Exposed so a diff view can show
    *which* part of the behaviour moved, not just that the hash changed."""
    return {
        "charging_mode": draft.charging_mode,
        "rule_type": draft.rule_type_code,
        "service_type": draft.service_type,
        "conditions": _group_payload(draft.root_group),
        "actions": sorted(
            [
                [
                    a.action_type,
                    _target_attribute(a),
                    sorted(
                        [
                            [
                                p.name,
                                _scalar(p.raw),
                                p.value_type,
                                (p.currency or "").upper(),
                                (p.unit or "").upper(),
                                p.sequence,
                            ]
                            for p in a.parameters
                        ],
                        key=json.dumps,
                    ),
                ]
                for a in draft.actions
            ],
            key=json.dumps,
        ),
        "parameters": sorted(
            [
                [p.name, _scalar(p.raw), p.value_type, p.sequence]
                for p in draft.parameters
            ],
            key=json.dumps,
        ),
        "targets": [
            (draft.targets.product or "").upper(),
            (draft.targets.offer or "").upper(),
            (draft.targets.tariff_plan or "").upper(),
        ],
        "behaviour": {
            "priority": draft.behaviour.priority,
            "stacking_policy": draft.behaviour.stacking_policy,
            "conflict_group": (draft.behaviour.conflict_group or "").upper(),
            "fallback_policy": draft.behaviour.fallback_policy,
            "stop_processing": draft.behaviour.stop_processing,
            "execution_mode": draft.behaviour.execution_mode,
            "condition_logic": draft.behaviour.condition_logic,
        },
        "validity": [
            draft.validity.effective_from.isoformat(),
            draft.validity.effective_to.isoformat() if draft.validity.effective_to else "",
            (draft.validity.currency_code or "").upper(),
        ],
        "dependencies": sorted(
            [[d.depends_on_rule_key, d.dependency_type] for d in draft.dependencies],
            key=json.dumps,
        ),
        "fallbacks": sorted(
            [[f.fallback_rule_key, f.level, f.scope] for f in draft.fallbacks],
            key=json.dumps,
        ),
    }


def behaviour_hash(draft: CanonicalDraft) -> str:
    payload = behaviour_payload(draft)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def content_hash(data: bytes) -> str:
    """Hash of a whole uploaded payload, so the same file posted twice is one batch."""
    return hashlib.sha256(data).hexdigest()


def record_hash(raw: Any) -> str:
    """Hash of one source record exactly as it arrived."""
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def natural_key(draft: CanonicalDraft) -> str:
    """Identity for a rule whose source gave it none.

    Derived from what the rule *does*, never from its name — a rename must be an
    update to the same logical rule, and two rules called "Peak" with different
    predicates must not collide. Deliberately narrower than the behaviour hash:
    priority and validity are excluded, so re-pricing a rule keeps its identity.
    """
    payload = {
        "charging_mode": draft.charging_mode,
        "rule_type": draft.rule_type_code,
        "service_type": draft.service_type,
        "conditions": _group_payload(draft.root_group),
        "action_types": sorted(draft.action_types),
        "targets": [
            (draft.targets.product or "").upper(),
            (draft.targets.offer or "").upper(),
            (draft.targets.tariff_plan or "").upper(),
        ],
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return digest[:16]
