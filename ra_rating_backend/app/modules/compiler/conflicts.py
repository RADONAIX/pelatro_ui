"""Set-level validation: conflicts and coverage gaps (requirement §6).

These are the checks that need the *whole* rule set at once, which is why they
live with the compiler rather than with per-rule structural validation.

The core primitive is a rule's **match footprint**: the set of dimension values
it can match, with ``None`` meaning "any". Two rules can collide only if their
footprints intersect on every dimension and their validity windows overlap.
That reduces an O(n²) comparison over 10,000 rules to a bucketed comparison
within (stage, service) — the only place a collision can actually occur.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.compiler.constants import (
    DIMENSION_BY_ATTRIBUTE,
    DIMENSION_OPERATORS,
    EXCLUSIVE_STAGES,
    IssueKind,
)
from app.modules.rules.constants import ActionType, RuleStatus, ValidationSeverity
from app.modules.rules.models import Rule
from app.modules.rules.schemas import ValidationIssue

#: Comparing every pair inside a bucket is quadratic. Buckets are (stage,
#: service) so they are normally small, but a pathological estate could put
#: thousands of rules in one. Past this we report that overlap detection was
#: bounded rather than silently checking a subset.
MAX_PAIRS_PER_BUCKET = 250_000


@dataclass
class Footprint:
    """A rule reduced to what it can match."""

    rule: Rule
    #: dimension column -> frozenset of values, or None for "any".
    dimensions: dict[str, frozenset[str] | None] = field(default_factory=dict)
    #: True when the rule has conditions the footprint cannot represent. Such a
    #: rule may be narrower than its footprint suggests, so an overlap with it
    #: is reported as a warning rather than an error.
    has_residual: bool = False

    def value(self, column: str) -> frozenset[str] | None:
        return self.dimensions.get(column)


def _normalise(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).upper()


def build_footprint(rule: Rule) -> Footprint:
    fp = Footprint(rule=rule)
    # The rule header pins the service type even when no condition mentions it.
    fp.dimensions["service_type"] = frozenset({rule.service_type.upper()})

    for cond in rule.conditions:
        column = DIMENSION_BY_ATTRIBUTE.get(cond.attribute)
        if column is None or cond.operator not in DIMENSION_OPERATORS or cond.negate:
            fp.has_residual = True
            continue
        values = frozenset(_normalise(v) for v in (cond.values or []))
        if not values:
            fp.has_residual = True
            continue
        existing = fp.dimensions.get(column)
        # The same dimension constrained twice is an intersection.
        fp.dimensions[column] = values if existing is None else (existing & values)
    return fp


def windows_overlap(a: Rule, b: Rule) -> bool:
    a_end = a.effective_to or date.max
    b_end = b.effective_to or date.max
    return a.effective_from <= b_end and b.effective_from <= a_end


def footprints_intersect(a: Footprint, b: Footprint) -> bool:
    """True when some context could match both rules.

    A dimension absent from a footprint is a wildcard, so it intersects
    anything; two present dimensions intersect only if their value sets do.
    """
    for column in set(a.dimensions) | set(b.dimensions):
        left, right = a.value(column), b.value(column)
        if left is None or right is None:
            continue
        if not (left & right):
            return False
    return True


def _describe(fp: Footprint) -> str:
    parts = [
        f"{col}={'|'.join(sorted(vals))}"
        for col, vals in sorted(fp.dimensions.items())
        if vals is not None and col != "service_type"
    ]
    return ", ".join(parts) or "any"


def _issue(
    severity: str, kind: str, code: str, message: str, *, rules: list[Rule], hint: str = ""
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        # Rule keys rather than a dotted field path: a set-level issue belongs to
        # a group of rules, and the UI links to each of them.
        path=",".join(f"{r.rule_key}:v{r.version}" for r in rules),
        hint=hint or kind,
    )


# --- Conflicts --------------------------------------------------------------


def detect_conflicts(rules: list[Rule]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    footprints = [build_footprint(r) for r in rules]

    buckets: dict[tuple[str, str], list[Footprint]] = defaultdict(list)
    for fp in footprints:
        buckets[(fp.rule.execution_stage, fp.rule.service_type)].append(fp)

    for (stage, service), members in buckets.items():
        pair_count = len(members) * (len(members) - 1) // 2
        if pair_count > MAX_PAIRS_PER_BUCKET:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="overlap_check_bounded",
                    message=(
                        f"{len(members)} rules share the {stage} stage for {service}; "
                        "overlap detection was skipped for this group."
                    ),
                    path="",
                    hint="Split them across conflict groups so each set stays comparable.",
                )
            )
            continue

        for a, b in combinations(members, 2):
            if not windows_overlap(a.rule, b.rule):
                continue
            if not footprints_intersect(a, b):
                continue
            issues.extend(_classify_pair(a, b, stage))

    issues.extend(_detect_exclusive_promotions(footprints))
    issues.extend(_detect_circular_tariff_selection(rules))
    return issues


def _classify_pair(a: Footprint, b: Footprint, stage: str) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    ra, rb = a.rule, b.rule
    pair = [ra, rb]

    identical_match = a.dimensions == b.dimensions and not (a.has_residual or b.has_residual)
    same_precedence = ra.specificity == rb.specificity and ra.priority == rb.priority

    if identical_match and _same_actions(ra, rb):
        out.append(
            _issue(
                ValidationSeverity.WARNING,
                IssueKind.CONFLICT,
                "duplicate_rule",
                f"'{ra.name}' and '{rb.name}' match identically and do the same thing.",
                rules=pair,
                hint="Retire one of them — the second can never be the winner.",
            )
        )
        return out

    if same_precedence and stage in EXCLUSIVE_STAGES:
        severity = (
            ValidationSeverity.WARNING
            if (a.has_residual or b.has_residual)
            else ValidationSeverity.ERROR
        )
        out.append(
            _issue(
                severity,
                IssueKind.CONFLICT,
                "ambiguous_precedence",
                (
                    f"'{ra.name}' and '{rb.name}' can both match ({_describe(a)}) at the "
                    f"{stage} stage with the same specificity ({ra.specificity}) and "
                    f"priority ({ra.priority})."
                ),
                rules=pair,
                hint=(
                    "The engine has no basis to choose. Narrow one rule's conditions, "
                    "or give them different priorities."
                    + (
                        " One of them has conditions outside the match dimensions, so they "
                        "may not actually collide."
                        if severity == ValidationSeverity.WARNING
                        else ""
                    )
                ),
            )
        )
        return out

    if (
        ra.conflict_group
        and ra.conflict_group == rb.conflict_group
        and ra.stacking_policy == "STACKABLE"
        and rb.stacking_policy == "STACKABLE"
    ):
        out.append(
            _issue(
                ValidationSeverity.WARNING,
                IssueKind.CONFLICT,
                "stackable_conflict_group",
                (
                    f"'{ra.name}' and '{rb.name}' share conflict group "
                    f"'{ra.conflict_group}' but are both STACKABLE."
                ),
                rules=pair,
                hint=(
                    "A conflict group means mutually exclusive — one of these "
                    "should be EXCLUSIVE."
                ),
            )
        )

    if _contradictory_actions(ra, rb) and stage in EXCLUSIVE_STAGES:
        out.append(
            _issue(
                ValidationSeverity.WARNING,
                IssueKind.CONFLICT,
                "contradictory_actions",
                (
                    f"'{ra.name}' and '{rb.name}' overlap and both set the base charge in "
                    "incompatible ways."
                ),
                rules=pair,
                hint="Only the higher-precedence rule will apply. Confirm that is intended.",
            )
        )
    return out


def _action_signature(rule: Rule) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    return sorted(
        (a.action_type, tuple(sorted((k, str(v)) for k, v in (a.params or {}).items())))
        for a in rule.actions
    )


def _same_actions(a: Rule, b: Rule) -> bool:
    return _action_signature(a) == _action_signature(b)


def _contradictory_actions(a: Rule, b: Rule) -> bool:
    """Both set a base charge, but to different values."""
    charge_actions = {ActionType.SET_RATE.value, ActionType.SET_ZERO_CHARGE.value}
    a_charge = {x.action_type for x in a.actions} & charge_actions
    b_charge = {x.action_type for x in b.actions} & charge_actions
    if not (a_charge and b_charge):
        return False
    return _action_signature(a) != _action_signature(b)


def _detect_exclusive_promotions(footprints: list[Footprint]) -> list[ValidationIssue]:
    """Two exclusive promotions that can both match are a billing dispute waiting
    to happen — the subscriber sees one applied and expects the other."""
    promos = [
        fp
        for fp in footprints
        if any(a.action_type == ActionType.APPLY_PROMOTION.value for a in fp.rule.actions)
        and fp.rule.stacking_policy == "EXCLUSIVE"
    ]
    out: list[ValidationIssue] = []
    for a, b in combinations(promos, 2):
        if a.rule.conflict_group and a.rule.conflict_group == b.rule.conflict_group:
            continue  # already declared mutually exclusive — resolution is defined
        if windows_overlap(a.rule, b.rule) and footprints_intersect(a, b):
            out.append(
                _issue(
                    ValidationSeverity.WARNING,
                    IssueKind.CONFLICT,
                    "multiple_exclusive_promotions",
                    (
                        f"Exclusive promotions '{a.rule.name}' and '{b.rule.name}' can both "
                        "match the same usage."
                    ),
                    rules=[a.rule, b.rule],
                    hint="Put them in the same conflict group so precedence is explicit.",
                )
            )
    return out


def _detect_circular_tariff_selection(rules: list[Rule]) -> list[ValidationIssue]:
    """A SELECT_TARIFF rule that routes to a plan whose own rule routes back.

    Left unchecked this loops the tariff-selection stage forever, so it is an
    error rather than a warning.
    """
    edges: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, Rule] = {}
    for rule in rules:
        source = (rule.tariff_plan_id or "").strip()
        for action in rule.actions:
            if action.action_type != ActionType.SELECT_TARIFF.value:
                continue
            target = str((action.params or {}).get("tariff_plan") or "").strip().upper()
            if source and target:
                edges[source].add(target)
                labels.setdefault(source, rule)

    out: list[ValidationIssue] = []
    seen: set[str] = set()

    def walk(node: str, path: list[str]) -> None:
        if node in path:
            cycle = " → ".join([*path[path.index(node) :], node])
            key = "|".join(sorted(set(path)))
            if key in seen:
                return
            seen.add(key)
            out.append(
                _issue(
                    ValidationSeverity.ERROR,
                    IssueKind.CONFLICT,
                    "circular_tariff_selection",
                    f"Tariff selection loops: {cycle}.",
                    rules=[labels[n] for n in path if n in labels],
                    hint="Break the cycle — the tariff-selection stage would never terminate.",
                )
            )
            return
        for nxt in edges.get(node, ()):
            walk(nxt, [*path, node])

    for start in list(edges):
        walk(start, [])
    return out


# --- Coverage ---------------------------------------------------------------


async def detect_coverage_gaps(
    db: AsyncSession, rules: list[Rule]
) -> list[ValidationIssue]:
    """Gaps that would make CDRs fall through with no price.

    Coverage is reported as warnings, not errors: an estate mid-build is
    legitimately incomplete, and blocking a compile on it would stop teams
    publishing the part that is ready.
    """
    issues: list[ValidationIssue] = []
    base_rules = [r for r in rules if r.execution_stage == "BASE_CHARGE"]

    # --- A default per service ---------------------------------------------
    services = {r.service_type for r in rules}
    for service in sorted(services):
        footprints = [
            build_footprint(r) for r in base_rules if r.service_type == service
        ]
        has_default = any(
            not fp.has_residual
            and all(v is None for k, v in fp.dimensions.items() if k != "service_type")
            for fp in footprints
        )
        if not has_default:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="no_default_rule",
                    message=f"{service} has no unconditional fallback tariff.",
                    path=service,
                    hint=(
                        "Usage that matches no specific rule will be flagged "
                        "NO_MATCHING_RULE instead of being priced."
                    ),
                )
            )

    # --- Product x service --------------------------------------------------
    products = list(
        (
            await db.execute(
                select(cm.Product).where(cm.Product.status == "ACTIVE")
            )
        ).scalars().all()
    )
    priced_products: set[tuple[str, str]] = set()
    wildcard_services: set[str] = set()
    for rule in base_rules:
        fp = build_footprint(rule)
        codes = fp.value("product_code")
        if codes is None:
            wildcard_services.add(rule.service_type)
        else:
            for code in codes:
                priced_products.add((code, rule.service_type))

    for product in products:
        for service in product.service_types or []:
            if service in wildcard_services:
                continue
            if (product.code.upper(), service) not in priced_products:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="missing_product_tariff",
                        message=f"Product {product.code} has no {service} tariff.",
                        path=product.code,
                        hint="Add a base tariff rule, or a service-wide default that covers it.",
                    )
                )

    # --- Destination zones --------------------------------------------------
    zones = list(
        (
            await db.execute(
                select(cm.DestinationZone).where(cm.DestinationZone.status == "ACTIVE")
            )
        ).scalars().all()
    )
    voice_rules = [r for r in base_rules if r.service_type in {"VOICE", "SMS"}]
    covered: set[str] = set()
    zone_wildcard = False
    for rule in voice_rules:
        codes = build_footprint(rule).value("destination_zone")
        if codes is None:
            zone_wildcard = True
        else:
            covered |= codes
    if not zone_wildcard:
        for zone in zones:
            if zone.code.upper() not in covered:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="missing_destination_tariff",
                        message=f"Destination zone {zone.code} has no voice or SMS tariff.",
                        path=zone.code,
                        hint="Calls to it would resolve to NO_MATCHING_RULE.",
                    )
                )

    # --- Expiring with no successor ----------------------------------------
    by_key: dict[str, list[Rule]] = defaultdict(list)
    for rule in rules:
        by_key[rule.rule_key].append(rule)
    today = date.today()
    for key, versions in by_key.items():
        newest = max(versions, key=lambda r: r.version)
        if newest.effective_to and newest.effective_to >= today:
            successor = any(
                r.rule_key != key
                and r.service_type == newest.service_type
                and r.execution_stage == newest.execution_stage
                and r.effective_from > newest.effective_to
                for r in rules
            )
            if not successor:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="expiring_without_successor",
                        message=(
                            f"'{newest.name}' expires on {newest.effective_to} and nothing "
                            "takes over."
                        ),
                        path=f"{key}:v{newest.version}",
                        hint="Extend it or publish a replacement before that date.",
                    )
                )

    return issues


def selectable(rules: list[Rule]) -> list[Rule]:
    """The rules a snapshot may contain: approved and not retired."""
    allowed = {
        RuleStatus.APPROVED.value,
        RuleStatus.COMPILED.value,
        RuleStatus.PUBLISHED.value,
        RuleStatus.ACTIVE.value,
    }
    return [r for r in rules if r.status in allowed]
