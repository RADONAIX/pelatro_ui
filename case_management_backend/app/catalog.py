"""Assurance catalog — the shared vocabulary between the rules module and cases.

A rule is authored against one assurance (e.g. Usage Assurance), one entity
scope / sub-module (e.g. Usage Events) and one primitive category (e.g.
Completeness). When that rule fires it raises a case carrying exactly those
three identifiers, so the case list filters on the same axes the rules were
designed on.

Everything here is static reference data — no DB round-trip. The frontend reads
it from /api/catalog/meta to populate its filter and authoring dropdowns.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Assurance:
    code: str            # short code used in rule ids, e.g. "UA" -> UA001
    name: str            # "Usage Assurance"
    group: str           # "Revenue Assurance"
    modules: list[str] = field(default_factory=list)   # entity scopes / sub-modules


# Mirrors the assurance switcher in the rules application, including its
# grouping. `modules` are the entity scopes a rule in that assurance can be
# bound to — a case inherits its module from the rule that raised it.
ASSURANCES: list[Assurance] = [
    Assurance("RA", "Rating Assurance", "Commercial Assurance",
              ["Rating", "Tariff", "Discount", "Usage Events", "Subscriber"]),
    Assurance("PA", "Partner Assurance", "Commercial Assurance",
              ["Partner", "Settlement", "Interconnect", "Roaming", "Invoice"]),
    Assurance("MA", "Migration Assurance", "Customer Assurance",
              ["Subscriber", "Account", "Balance", "Product Catalog", "Legacy Feed"]),
    Assurance("UA", "Usage Assurance", "Revenue Assurance",
              ["Usage Events", "Subscriber", "MSC", "CDR", "Mediation", "Rating", "Billing"]),
    Assurance("BA", "Billing Assurance", "Revenue Assurance",
              ["Invoice", "Bill Run", "Account", "Charge", "Adjustment", "Tax"]),
    Assurance("CA", "Charging Assurance", "Revenue Assurance",
              ["Balance", "Session", "OCS", "Voucher", "Subscriber", "Bundle"]),
    Assurance("NA", "Network Assurance", "Operations Assurance",
              ["Node", "MSC", "SGSN", "Probe", "Element Feed"]),
    Assurance("CL", "Collection Assurance", "Financial Assurance",
              ["Payment", "Receivable", "Dunning", "Account", "Bank Feed"]),
    Assurance("ME", "Mediation Assurance", "Operations Assurance",
              ["AIR", "SDP", "MSC", "CDR", "File Feed", "Mediation"]),
]

# The assurance that owns postpaid invoicing. It is the only one linked to the
# canonical_rating schema — see app/canonical.py — so it is named here rather
# than spelled "BA" at each of the places that check.
BILLING_ASSURANCE_CODE = "BA"

#: The assurance that owns rating. A billing shock is almost never a billing
#: fault — billing adds tax to whatever rating handed it — so this is where the
#: cause of one is already recorded.
RATING_ASSURANCE_CODE = "RA"

ASSURANCE_BY_CODE: dict[str, Assurance] = {a.code: a for a in ASSURANCES}
ASSURANCE_BY_NAME: dict[str, Assurance] = {a.name.lower(): a for a in ASSURANCES}


def resolve_assurance(value: str | None) -> Assurance | None:
    """Look an assurance up by code ("UA") or by name ("Usage Assurance")."""
    if not value:
        return None
    key = value.strip()
    return ASSURANCE_BY_CODE.get(key.upper()) or ASSURANCE_BY_NAME.get(key.lower())


# Rule primitive categories — also the "issue type" of any case the rule
# raises. The first block is what the rule explorer surfaces as categories in
# scope; the second is defined but hidden by metadata, and is included so cases
# raised by those primitives still classify.
PRIMARY_RULE_CATEGORIES: list[str] = [
    "Completeness",
    "Reconciliation",
    "Aggregation",
    "Threshold",
    "Sequence",
    "Duplicate",
    "Existence",
    "Statistical",
    "Temporal",
]

HIDDEN_RULE_CATEGORIES: list[str] = [
    "Comparison",
    "Calculation",
    "Referential Integrity",
    "Pattern Matching",
    "ML Prediction",
    "Graph Relationship",
]

# Not a rule primitive: the issue type a case carries when an analyst raised it
# by hand from a customer document rather than a control firing. It is in the
# list so those cases classify and filter like any other.
MANUAL_INVESTIGATION = "Manual Investigation"

RULE_CATEGORIES: list[str] = PRIMARY_RULE_CATEGORIES + HIDDEN_RULE_CATEGORIES + [MANUAL_INVESTIGATION]

# How often the engine executes a control.
FREQUENCIES: list[str] = ["Real-time", "Hourly", "Cycle", "Daily", "Weekly", "Monthly"]

# A rule only raises cases once it is Active.
LIFECYCLE_STATES: list[str] = ["Draft", "Active", "Paused", "Retired"]

# Result of the latest execution.
RULE_STATUSES: list[str] = ["PASS", "FAIL", "WARNING", "NOT_RUN"]

STATUSES: list[str] = ["Open", "In Progress", "Resolved", "Closed", "Cancelled"]
SEVERITIES: list[str] = ["low", "medium", "high", "critical"]
ORIGINS: list[str] = ["auto_detected", "analyst_raised"]
ACTIONS: list[str] = [
    "NA",
    "Escalated to carrier",
    "Adjusted & rebilled",
    "Waived",
    "Config fix raised",
    "Re-ingest requested",
    "Under review",
]

# Statuses that mean the case no longer needs work — used by the summary tiles
# and the open-workload metrics.
TERMINAL_STATUSES = {"Resolved", "Closed", "Cancelled"}

# The parameter block a rule shows once a primitive category is chosen. The
# authoring form renders these; the engine reads them back when it compiles the
# control. `tolerancePct` is promoted to its own column on the rule.
CATEGORY_PARAMETERS: dict[str, list[str]] = {
    "Completeness": ["sourceFeed", "targetFeed", "tolerancePct"],
    "Reconciliation": ["sourceFeed", "targetFeed", "matchKey", "tolerancePct"],
    "Aggregation": ["sourceFeed", "aggregateField", "grainField", "tolerancePct"],
    "Threshold": ["field", "operator", "thresholdValue", "window"],
    "Sequence": ["sourceFeed", "sequenceField", "maxGap"],
    "Duplicate": ["sourceFeed", "signatureFields", "window"],
    "Existence": ["sourceFeed", "targetFeed", "lookupKey"],
    "Statistical": ["field", "baselineWindow", "sigma"],
    "Temporal": ["sourceFeed", "expectedIntervalMinutes", "graceMinutes"],
    "Comparison": ["sourceFeed", "targetFeed", "compareField", "tolerancePct"],
    "Calculation": ["formula", "expectedField", "tolerancePct"],
    "Referential Integrity": ["sourceFeed", "targetFeed", "foreignKey"],
    "Pattern Matching": ["field", "pattern"],
    "ML Prediction": ["model", "featureSet", "confidenceFloor"],
    "Graph Relationship": ["nodeType", "edgeType", "maxDepth"],
}


def meta() -> dict:
    """Everything a filter bar or a rule-authoring form needs, in one payload."""
    return {
        "assurances": [
            {"code": a.code, "name": a.name, "group": a.group, "modules": a.modules}
            for a in ASSURANCES
        ],
        "groups": sorted({a.group for a in ASSURANCES}),
        "modules": sorted({m for a in ASSURANCES for m in a.modules}),
        "ruleCategories": RULE_CATEGORIES,
        "primaryRuleCategories": PRIMARY_RULE_CATEGORIES,
        "hiddenRuleCategories": HIDDEN_RULE_CATEGORIES,
        "categoryParameters": CATEGORY_PARAMETERS,
        "frequencies": FREQUENCIES,
        "lifecycleStates": LIFECYCLE_STATES,
        "ruleStatuses": RULE_STATUSES,
        "statuses": STATUSES,
        "severities": SEVERITIES,
        "origins": ORIGINS,
        "actions": ACTIONS,
    }
