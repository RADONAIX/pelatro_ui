import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fetchSummary } from "@/lib/cases";
import * as fallback from "@/components/dashboard/data";

// ---------------------------------------------------------------------------
// The Enterprise Dashboard's numbers, from GET /api/enterprise-dashboard.
//
// The server aggregates assurance.assurance_leakage_monthly and returns every
// panel's data in one payload, already formatted in INR. This module maps that
// payload onto the shapes the panels already render, so a panel does not know
// or care whether it is showing live data or the bundled sample.
//
// The sample in components/dashboard/data.ts is the fallback, not the default:
// it renders while the request is in flight and if the request fails, so an
// unreachable backend degrades to a populated dashboard rather than an empty
// one. `live` says which of the two you are looking at.
// ---------------------------------------------------------------------------

interface ApiKpi {
  key: string;
  label: string;
  display: string;
  delta: number;
  deltaUnit: string;
  good: boolean;
}

interface ApiPayload {
  month: string;
  currency: string;
  kpis: ApiKpi[];
  trend: { period: string; total: number; billed: number; leakage: number }[];
  leakageBreakdown: {
    name: string;
    value: number;
    display: string;
    pct: string;
    color: string;
  }[];
  leakageDrivers: {
    driver: string;
    module: string;
    leakage: string;
    impact: number;
    color: string;
  }[];
  controlHealth: { type: string; effectiveness: string; records: number }[];
  aiActions: { label: string; impact: string; priority: string }[];
  prepaidTech: {
    label: string;
    display: string;
    share: string;
    delta: number;
    icon: string;
  }[];
  postpaidTech: {
    label: string;
    display: string;
    share: string;
    delta: number;
    icon: string;
  }[];
  prepaidFlow: {
    label: string;
    display: string;
    share: string;
    delta: number;
  }[];
  postpaidFlow: {
    label: string;
    display: string;
    share: string;
    delta: number;
  }[];
  dataHealth: { label: string; value: string; good: boolean }[];
  totals: { revenue: Money; leakage: Money; prepaid: Money; postpaid: Money };
  range: { from: string; to: string; display: string };
}

/** The four tiles on Alerts & Cases, all from one case-service summary. */
export interface AlertCounts {
  criticalAlerts: number;
  openCases: number;
  highPriority: number;
  pendingInvestigations: number;
}

interface Money {
  amount: number;
  display: string;
}

export interface DashboardKpi {
  key: string;
  label: string;
  value: string;
  delta: string;
  good: boolean;
}

export interface DashboardData {
  live: boolean;
  loading: boolean;
  error: string | null;
  month: string;
  /** Axis caption for the trend chart — the unit actually being plotted. */
  trendUnit: string;
  /** Pre-formatted INR total for the doughnut's centre label. */
  leakageTotal: string;
  /** What period the figures cover, for the header's date control. */
  range: string;
  /** Null until the case service answers; the panel shows a dash meanwhile. */
  alerts: AlertCounts | null;
  kpis: DashboardKpi[] | null;
  revenueTrend: typeof fallback.revenueTrend;
  leakageBreakdown: typeof fallback.leakageBreakdown;
  leakageDrivers: typeof fallback.leakageDrivers;
  controlHealth: typeof fallback.controlHealth;
  aiActions: typeof fallback.aiActions;
  prepaidTech: typeof fallback.prepaidTech;
  postpaidTech: typeof fallback.postpaidTech;
  prepaidFlow: typeof fallback.prepaidFlow;
  postpaidFlow: typeof fallback.postpaidFlow;
  dataHealth: typeof fallback.dataHealth;
}

/** "12.6%" / "0.3 pp" — Delta renders the arrow, so no sign is carried here. */
const deltaText = (value: number, unit: string) =>
  unit === "pp" ? `${Math.abs(value)} pp` : `${Math.abs(value)}%`;

const SAMPLE: Omit<DashboardData, "live" | "loading" | "error"> = {
  month: "",
  trendUnit: "USD (Millions)",
  leakageTotal: "$3.82M",
  range: "",
  alerts: null,
  kpis: null,
  revenueTrend: fallback.revenueTrend,
  leakageBreakdown: fallback.leakageBreakdown,
  leakageDrivers: fallback.leakageDrivers,
  controlHealth: fallback.controlHealth,
  aiActions: fallback.aiActions,
  prepaidTech: fallback.prepaidTech,
  postpaidTech: fallback.postpaidTech,
  prepaidFlow: fallback.prepaidFlow,
  postpaidFlow: fallback.postpaidFlow,
  dataHealth: fallback.dataHealth,
};

function adapt(
  p: ApiPayload,
): Omit<DashboardData, "live" | "loading" | "error"> {
  return {
    month: p.month,
    trendUnit: "INR",
    leakageTotal: p.totals.leakage.display,
    range: p.range.display,
    alerts: null,
    kpis: p.kpis.map((k) => ({
      key: k.key,
      label: k.label,
      value: k.display,
      delta: deltaText(k.delta, k.deltaUnit),
      // A KPI that improved reads green whichever direction "improved" is: a
      // rise in leakage and a fall in effectiveness are both bad.
      good:
        k.deltaUnit === "pp"
          ? k.good === k.delta >= 0
          : k.good === k.delta >= 0,
    })),
    // The chart plots months, but its x key is still `day` — one renamed field
    // in the panel is not worth a second shape.
    revenueTrend: p.trend.map((t) => ({
      day: t.period,
      total: t.total,
      billed: t.billed,
      leakage: t.leakage,
    })),
    leakageBreakdown: p.leakageBreakdown.map((b) => ({
      name: b.name,
      value: b.value,
      pct: b.pct,
      color: b.color,
      display: b.display,
    })),
    leakageDrivers: p.leakageDrivers.map((d) => ({
      driver: d.driver,
      module: d.module,
      leakage: d.leakage,
      impact: d.impact,
      color: d.color,
    })),
    controlHealth: p.controlHealth.map((c) => ({
      type: c.type,
      effectiveness: c.effectiveness,
      // The source has no month-on-month control trend; the record count is
      // what makes a percentage readable, so it takes that column instead of a
      // fabricated arrow.
      trend: `${c.records.toLocaleString("en-IN")} records`,
    })),
    aiActions: p.aiActions,
    prepaidTech: p.prepaidTech.map((t) => ({
      label: t.label,
      value: t.display,
      share: t.share,
      delta: `${Math.abs(t.delta)}%`,
      icon: t.icon,
    })),
    postpaidTech: p.postpaidTech.map((t) => ({
      label: t.label,
      value: t.display,
      share: t.share,
      delta: `${Math.abs(t.delta)}%`,
      icon: t.icon,
    })),
    prepaidFlow: p.prepaidFlow.map((f) => ({
      label: f.label,
      value: f.display,
      share: f.share,
      delta: `${Math.abs(f.delta)}%`,
    })),
    postpaidFlow: p.postpaidFlow.map((f) => ({
      label: f.label,
      value: f.display,
      share: f.share,
      delta: `${Math.abs(f.delta)}%`,
    })),
    dataHealth: p.dataHealth.map((h) => ({
      label: h.label,
      value: h.value,
      // No prior-period figure in the source, so no delta is claimed.
      delta: "",
      up: true,
      good: h.good,
    })),
  };
}

export function useEnterpriseDashboard(): DashboardData {
  const [state, setState] = useState<DashboardData>({
    ...SAMPLE,
    live: false,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    // Two owners, two calls: the leakage table cannot answer how many critical
    // cases are open, and the case service cannot answer anything else here.
    // The case count is allowed to fail on its own — a dashboard missing one
    // card is better than a dashboard that falls back wholesale because the
    // case service is down.
    Promise.all([
      api.get<ApiPayload>("/enterprise-dashboard"),
      fetchSummary({ openOnly: true }).catch(() => null),
    ])
      .then(([{ data }, cases]) => {
        if (cancelled) return;
        const adapted = adapt(data);
        if (cases) {
          const critical = cases.bySeverity?.critical ?? 0;
          adapted.alerts = {
            criticalAlerts: critical,
            openCases: cases.total,
            highPriority: cases.bySeverity?.high ?? 0,
            pendingInvestigations: cases.byStatus?.["In Progress"] ?? 0,
          };
          const card = adapted.kpis?.find((k) => k.key === "critical_cases");
          if (card) card.value = critical.toLocaleString("en-IN");
        }
        setState({ ...adapted, live: true, loading: false, error: null });
      })
      .catch((e: Error) => {
        if (cancelled) return;
        // Keep the sample on screen; the banner says it is not live.
        setState({ ...SAMPLE, live: false, loading: false, error: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
