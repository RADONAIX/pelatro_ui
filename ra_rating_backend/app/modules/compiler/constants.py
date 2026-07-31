"""What the compiler turns a canonical rule into.

The central idea: split a rule's conditions into **match dimensions** and
**residual predicates**.

A match dimension is an attribute that a rating context also carries, compared
with equality or set membership. Those become columns on the executable rule,
with ``NULL`` meaning "any". Selection is then a plain join —

    (r.product IS NULL OR r.product = ctx.product) AND ...

— which is why 10,000 rules can be resolved for 100,000 contexts in one bulk
statement instead of 10^9 comparisons. Anything that cannot be reduced to a
dimension (``duration_seconds > 60``, ``called_number STARTS_WITH '44'``) stays
as a residual predicate, evaluated only against the handful of candidates a
context already matched.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.rules.constants import Operator

#: Rule attribute -> executable column. Order is the lookup key's order, and is
#: also the order the UI shows a rule's match signature in.
MATCH_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("service_type", "service_type"),
    ("product", "product_code"),
    ("offer", "offer_code"),
    ("tariff_plan", "tariff_plan_code"),
    ("destination_zone", "destination_zone"),
    ("origin_zone", "origin_zone"),
    ("time_band", "time_band"),
    ("account_type", "account_type"),
    ("roaming", "roaming"),
    ("network_type", "network_type"),
    ("rating_group", "rating_group"),
    ("on_net", "on_net"),
)

DIMENSION_BY_ATTRIBUTE: dict[str, str] = dict(MATCH_DIMENSIONS)
DIMENSION_COLUMNS: tuple[str, ...] = tuple(col for _, col in MATCH_DIMENSIONS)

#: Only these operators reduce to a dimension. NOT_EQUALS/NOT_IN deliberately do
#: not: a negative match is cheap to evaluate as a residual predicate but would
#: need an anti-join here, which does not compose with the wildcard semantics.
DIMENSION_OPERATORS: frozenset[str] = frozenset({Operator.EQUALS, Operator.IN})


class SnapshotStatus(StrEnum):
    #: Compiled and checksummed, not yet the one the engine uses.
    PUBLISHED = "PUBLISHED"
    #: The snapshot rating runs resolve against.
    ACTIVE = "ACTIVE"
    #: Replaced by a newer activation. Kept forever — historical results
    #: reference it, and re-explaining a charge means re-reading it.
    SUPERSEDED = "SUPERSEDED"
    #: Compilation failed; retained for the error report.
    FAILED = "FAILED"


class IssueKind(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    BUSINESS = "BUSINESS"
    CONFLICT = "CONFLICT"
    COVERAGE = "COVERAGE"


#: Rule types whose actions set the base charge. Two of these matching the same
#: context with equal precedence is genuinely ambiguous — the engine would have
#: no basis to choose, so it is an error rather than a warning.
EXCLUSIVE_STAGES: frozenset[str] = frozenset(
    {"BASE_CHARGE", "PULSE", "MINIMUM_CHARGE", "TARIFF_SELECTION", "ROUNDING"}
)
