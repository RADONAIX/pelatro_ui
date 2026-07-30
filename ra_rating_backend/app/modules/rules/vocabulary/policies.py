"""Stacking policies as data.

Three rows, and they have to be rows rather than an enum for one reason: the
compiler and the engine both need to know *behaviourally* what a policy means —
may several rules apply, and does a winner erase what a lower-priority rule
already set. Encoding that as two booleans beside the code means adding a fourth
policy is a row, and neither the compiler nor the engine has to learn its name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StackingPolicySpec:
    code: str
    name: str
    allows_multiple: bool
    overrides_lower: bool
    description: str = ""


CANONICAL_STACKING_POLICIES: tuple[StackingPolicySpec, ...] = (
    StackingPolicySpec(
        "EXCLUSIVE", "Exclusive", allows_multiple=False, overrides_lower=False,
        description="Only the single highest-ranked winner in the conflict group applies.",
    ),
    StackingPolicySpec(
        "STACKABLE", "Stackable", allows_multiple=True, overrides_lower=False,
        description="Every match applies, in priority order.",
    ),
    StackingPolicySpec(
        "OVERRIDE", "Override", allows_multiple=False, overrides_lower=True,
        description="Replaces whatever a lower-priority rule already set at this stage.",
    ),
)

STACKING_POLICY_BY_CODE: dict[str, StackingPolicySpec] = {
    p.code: p for p in CANONICAL_STACKING_POLICIES
}
