"""How narrowly a rule is targeted, computed rather than declared.

Higher score = more specific = wins a tie against a broader rule. This is what
makes the documented exact → product → service → global-default fallback fall out
of the data instead of being hand-coded: a rule pinning product AND destination AND
time band naturally outranks a service-wide default, without anyone tuning
priorities.

It is computed and read-only on purpose. Two hand-tuned knobs for one job — an
author-set specificity beside an author-set priority — is how a 10,000-rule estate
becomes unpredictable: one person raises specificity to win a fight, someone else
raises priority, and afterwards nobody can say which rule wins or why.

Scoring is identical to the legacy ``rules/service.compute_specificity`` for the
operators it supported, so the R4 backfill does not renumber the existing estate.
``test_canonical_writer.py`` asserts that agreement.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.rules.canonical.draft import DraftCondition, DraftConditionGroup
from app.modules.rules.constants import Operator
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY


@dataclass(frozen=True, slots=True)
class Contribution:
    """One attribute's contribution, so the wizard can show the derivation.

    "product 50 + destination zone 45 + time band 35 = 130" is an explanation an
    author can act on; a bare 130 is a number they have to trust.
    """

    attribute: str
    label: str
    operator: str
    points: int
    note: str = ""


def contributions(conditions: list[DraftCondition] | tuple[DraftCondition, ...]) -> list[
    Contribution
]:
    out: list[Contribution] = []
    for cond in conditions:
        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        if attr is None:
            continue
        weight = attr.specificity
        match cond.operator:
            case Operator.EQUALS | Operator.STARTS_WITH:
                out.append(Contribution(cond.attribute, attr.label, cond.operator, weight))
            case Operator.IN:
                # Diminishing: an IN over twenty zones is barely narrower than
                # "any zone", so it should not score like an exact match.
                n = max(1, len(cond.values))
                points = max(1, weight // min(n, 5))
                out.append(
                    Contribution(
                        cond.attribute, attr.label, cond.operator, points,
                        note=f"{n} value(s), so {weight} ÷ {min(n, 5)}",
                    )
                )
            case (
                Operator.BETWEEN
                | Operator.GREATER_THAN
                | Operator.LESS_THAN
                | Operator.GREATER_OR_EQUAL
                | Operator.LESS_OR_EQUAL
                | Operator.CONTAINS
            ):
                out.append(
                    Contribution(
                        cond.attribute, attr.label, cond.operator, weight // 2,
                        note="a range binds the dimension loosely",
                    )
                )
            case Operator.NOT_EQUALS | Operator.NOT_IN:
                out.append(
                    Contribution(
                        cond.attribute, attr.label, cond.operator, 1,
                        note="an exclusion barely narrows the match",
                    )
                )
            case _:
                # EXISTS / NOT_EXISTS bind no value, so they add nothing.
                continue
    return out


def compute(conditions: list[DraftCondition] | tuple[DraftCondition, ...]) -> int:
    return sum(c.points for c in contributions(conditions))


def compute_for_group(root: DraftConditionGroup) -> int:
    """Score a whole condition tree.

    A negated group's conditions still count: "not in these three zones" is a real
    constraint on which CDRs reach the rule, even though it is a loose one — and
    the per-operator weights already reflect that looseness.
    """
    return compute(tuple(root.all_conditions()))


def explain(root: DraftConditionGroup) -> dict:
    """Derivation for the wizard's read-only Specificity field."""
    parts = contributions(tuple(root.all_conditions()))
    return {
        "total": sum(p.points for p in parts),
        "contributions": [
            {
                "attribute": p.attribute,
                "label": p.label,
                "operator": p.operator,
                "points": p.points,
                "note": p.note,
            }
            for p in parts
        ],
    }
