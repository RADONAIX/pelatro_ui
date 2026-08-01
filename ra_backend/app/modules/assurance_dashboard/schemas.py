"""Wire shapes for the executive dashboard.

Field names are camelCase to match the UI's AssuranceDashboard type verbatim
(ra_demo/src/lib/assurance/dashboard-config.ts), so the dashboard renders what
the API returns with no mapping layer in between — the same convention the
authored-rules module follows.
"""

from __future__ import annotations

from pydantic import BaseModel


class KpiValue(BaseModel):
    """One headline card.

    `value` is preformatted server-side because the unit differs per KPI —
    currency, count, percentage — and the card renders a bare string.
    """

    value: str
    #: Percentage change against the prior period. 0 when there is no prior data.
    delta: float
    #: True where a rising number is the good outcome (reconciliation success).
    higherIsBetter: bool
    spark: list[float]


class TrendPoint(BaseModel):
    label: str
    healthy: float
    exceptions: float
    leakage: float


class NamedValue(BaseModel):
    name: str
    value: float


class Point(BaseModel):
    label: str
    value: float


class Finding(BaseModel):
    finding: str
    source: str
    category: str
    #: Absolute charge variance in the source currency, NOT crores — the
    #: canonical table's figures are in single rupees.
    impactCr: float


class Kpis(BaseModel):
    revenueAtRisk: KpiValue
    recordsEvaluated: KpiValue
    exceptions: KpiValue
    exceptionRate: KpiValue
    reconciliation: KpiValue


class AssuranceDashboardOut(BaseModel):
    id: str
    name: str
    subtitle: str
    #: What one evaluated record is called here.
    recordUnit: str
    #: ISO currency of every monetary field, so the UI never guesses a symbol.
    currency: str
    kpis: Kpis
    trendTitle: str
    trend: list[TrendPoint]
    revenueAtRisk: list[Point]
    exceptionCategories: list[NamedValue]
    entitiesTitle: str
    entities: list[NamedValue]
    leakageCategories: list[NamedValue]
    businessSegments: list[NamedValue]
    findings: list[Finding]
