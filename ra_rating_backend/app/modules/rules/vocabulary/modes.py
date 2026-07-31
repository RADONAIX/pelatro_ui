"""Charging modes, rule categories and the per-version behaviour policies.

``charging_mode`` is the field that lets prepaid and postpaid share one rule
model instead of two engines. It does that by scoping the *stage pipeline*
(see :mod:`app.modules.rules.vocabulary.stages`) rather than by branching:
a rule declares which world it belongs to, and the stages available to it
follow from that declaration.
"""

from __future__ import annotations

from enum import StrEnum


class ChargingMode(StrEnum):
    """Which charging world a rule belongs to.

    ``BOTH`` means the rule text is correct either way — a destination
    classification or a tax rule does not care how the subscriber pays. It is
    validated to contain only ``COMMON``-stage actions, because a rule that
    deducts a prepaid balance is emphatically not mode-agnostic.
    """

    PREPAID = "PREPAID"
    POSTPAID = "POSTPAID"
    BOTH = "BOTH"


class RuleCategory(StrEnum):
    """Which of the three rule families a stage or type belongs to.

    Deliberately the same value space as :class:`ChargingMode` minus ``BOTH``,
    with ``COMMON`` in its place: a *stage* is common or mode-specific, whereas a
    *rule* is prepaid, postpaid, or valid for both.
    """

    COMMON = "COMMON"
    PREPAID = "PREPAID"
    POSTPAID = "POSTPAID"


#: Stage categories reachable from a given rule charging mode. This mapping IS
#: the "one engine, not two" design: everything else — conditions, actions,
#: selection, the compiler — is single-implementation.
CATEGORIES_FOR_MODE: dict[str, tuple[str, ...]] = {
    ChargingMode.PREPAID: (RuleCategory.COMMON, RuleCategory.PREPAID),
    ChargingMode.POSTPAID: (RuleCategory.COMMON, RuleCategory.POSTPAID),
    ChargingMode.BOTH: (RuleCategory.COMMON,),
}


class ExecutionMode(StrEnum):
    """Which runtime a rule is meant for.

    Prepaid reservation and session control only mean anything inside a live
    session with a sub-100 ms budget; monthly rental and invoice assembly only
    mean anything in a bill run. Storing this lets the compiler emit an online
    snapshot and an offline snapshot from one rule set, instead of an online
    engine loading thousands of invoice rules it can never fire.
    """

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BOTH = "BOTH"


class FallbackPolicy(StrEnum):
    """What happens at this stage when the rule does not match.

    ``FALLBACK_CHAIN`` walks ``rule_fallback`` in level order — the explicit
    form of the exact → product → service → global-default chain that is
    currently only implicit in the specificity score.
    """

    FALLBACK_CHAIN = "FALLBACK_CHAIN"
    NEXT_MATCH = "NEXT_MATCH"
    GLOBAL_DEFAULT = "GLOBAL_DEFAULT"
    ERROR = "ERROR"
    NONE = "NONE"


class ConflictResolution(StrEnum):
    """How a conflict group picks its single winner."""

    HIGHEST_SPECIFICITY = "HIGHEST_SPECIFICITY"
    HIGHEST_PRIORITY = "HIGHEST_PRIORITY"
    FIRST_MATCH = "FIRST_MATCH"
    ERROR = "ERROR"


class DependencyType(StrEnum):
    REQUIRES = "REQUIRES"
    PRECEDES = "PRECEDES"
    EXCLUDES = "EXCLUDES"
    AMENDS = "AMENDS"


class FallbackScope(StrEnum):
    PRODUCT = "PRODUCT"
    SERVICE = "SERVICE"
    GLOBAL_DEFAULT = "GLOBAL_DEFAULT"


class RuleSetType(StrEnum):
    #: An editorial grouping — "all roaming rules".
    LOGICAL = "LOGICAL"
    #: A pinned set of versions published together; what the compiler snapshots.
    RELEASE = "RELEASE"
    #: Everything one vendor export produced, so an import is revertible as a unit.
    VENDOR_IMPORT = "VENDOR_IMPORT"


class ValidationState(StrEnum):
    """Cached verdict for the catalogue's Validation column.

    Distinct from the detail rows in ``rule_validation_issue``: this answers
    "is it green?" for 10,000 rows in one indexed read, the issue table answers
    "why is it red?" for the one row the author clicked.
    """

    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"
