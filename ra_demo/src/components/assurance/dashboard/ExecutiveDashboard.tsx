import {
  AlertTriangle,
  Database,
  IndianRupee,
  Percent,
  ShieldCheck,
} from "lucide-react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { formatCr, formatMoney } from "@/lib/assurance/dashboard-config";
import { useAssuranceDashboard } from "@/lib/assurance/use-assurance-dashboard";
import { Button } from "@/components/ui/button";
import { KPICard } from "./KPICard";
import { DashboardCard, LegendItem } from "./DashboardCard";
import { DashboardFilters } from "./DashboardFilters";
import { AREA_SERIES, AreaTrendChart } from "./AreaTrendChart";
import { RiskLineChart } from "./RiskLineChart";
import { DonutChart } from "./DonutChart";
import { HorizontalBarChart } from "./HorizontalBarChart";
import { VIZ, compact } from "./viz";

// ---------------------------------------------------------------------------
// The executive dashboard, shared by all eight assurance apps.
//
// Nothing below branches on the app. The layout, the five KPIs, the six cards
// and the table are fixed; the hook supplies the titles, labels and datasets —
// from the API where an assurance has real reconciliation results, from the
// profiles in dashboard-config.ts where it does not.
//
// It DOES branch on the source's scale, and only there: real figures are whole
// currency units while the synthetic profiles are authored in ₹ Cr, so the
// money formatters and the three subtitles that name a unit follow `live`.
//
// The reading order is deliberate — how much is at risk, how much was analysed,
// how many issues, are reconciliations healthy, is it improving, which systems
// are responsible, which categories leak most, which findings cost most.
// ---------------------------------------------------------------------------

export function ExecutiveDashboard({ app }: { app: AppMetadata }) {
  const {
    dashboard: d,
    loading,
    error,
    live,
    reload,
  } = useAssuranceDashboard(app);
  const k = d.kpis;

  const money = (v: number) =>
    live ? formatMoney(v, d.currency) : formatCr(v);
  const moneyUnit = live ? (d.currency ?? "₹") : "₹ Cr";

  return (
    <div className="ra-viz space-y-5 pb-2">
      {/* Header */}
      <header className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-[22px] font-semibold tracking-tight text-foreground">
              {d.name}
            </h1>
            <p className="mt-1 max-w-3xl text-[13.5px] leading-relaxed text-muted-foreground">
              {d.subtitle}
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="size-1.5 animate-pulse rounded-full bg-success" />
            {loading ? "Loading assurance results…" : "Assurance engine live"}
            <span className="mx-1 text-border">·</span>
            <span className="font-mono">{app.controlRange}</span>
          </div>
        </div>

        <DashboardFilters onRefresh={reload} />
      </header>

      {error && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive">
          {/* Figures stay on screen behind this banner — they are the last good
              response, not the failed one — so it says which, rather than
              letting a stale number pass for a current one. */}
          <span>
            Could not load live assurance results: {error}. Showing reference
            figures.
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={reload}
          >
            Retry
          </Button>
        </div>
      )}

      {/* 1–5 — the five KPIs, identical on every assurance */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {/* Only `value` is passed: the card no longer renders the delta or the
            sparkline the KpiValue also carries. */}
        <KPICard
          icon={IndianRupee}
          title="Revenue at Risk"
          description="Estimated financial exposure detected during assurance validation."
          value={k.revenueAtRisk.value}
        />
        <KPICard
          icon={Database}
          title={`Records Evaluated · ${d.recordUnit}`}
          description="Total business records analysed during the selected period."
          value={k.recordsEvaluated.value}
        />
        <KPICard
          icon={AlertTriangle}
          title="Assurance Exceptions"
          description="Business records violating assurance rules."
          value={k.exceptions.value}
        />
        <KPICard
          icon={Percent}
          title="Exception Rate"
          description="Percentage of exception records against evaluated records."
          value={k.exceptionRate.value}
        />
        <KPICard
          icon={ShieldCheck}
          title="Reconciliation Success"
          description="Percentage of records successfully reconciled between source systems."
          value={k.reconciliation.value}
        />
      </div>

      {/* Row 1 — trend, risk, distribution */}
      <div className="grid gap-4 xl:grid-cols-12">
        <DashboardCard
          className="xl:col-span-6"
          title={d.trendTitle}
          subtitle={
            live
              ? `Daily · ${d.recordUnit.toLowerCase()} reconciled`
              : "Last 12 months · thousands of records"
          }
          meta={
            <div className="flex items-center gap-3">
              {AREA_SERIES.map((s) => (
                <LegendItem key={s.key} color={s.color} label={s.label} />
              ))}
            </div>
          }
        >
          <AreaTrendChart data={d.trend} unit={d.recordUnit} />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-3"
          title="Revenue at Risk Trend"
          subtitle={
            d.riskTrendSubtitle ??
            (live ? `Daily · ${moneyUnit}` : "Last 30 days · ₹ Cr")
          }
        >
          <RiskLineChart data={d.revenueAtRisk} valueFormatter={money} />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-3"
          title="Exception Distribution"
          subtitle="By exception category"
        >
          <DonutChart data={d.exceptionCategories} centerLabel="Exceptions" />
        </DashboardCard>
      </div>

      {/* Row 2 — responsible systems, leaking categories, business mix */}
      <div className="grid gap-4 xl:grid-cols-12">
        <DashboardCard
          className="xl:col-span-4"
          title={d.entitiesTitle}
          subtitle="Exceptions attributed"
        >
          <HorizontalBarChart
            data={d.entities}
            color={VIZ.exceptions}
            valueFormatter={compact}
          />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-4"
          title="Top Leakage Categories"
          subtitle={`Sorted by revenue impact · ${moneyUnit}`}
        >
          <HorizontalBarChart
            data={d.leakageCategories}
            color={VIZ.leakage}
            valueFormatter={money}
          />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-4"
          title="Business Distribution"
          subtitle={`${d.recordUnit} by business segment`}
        >
          <DonutChart
            data={d.businessSegments}
            centerLabel="Records"
            valueFormatter={compact}
          />
        </DashboardCard>
      </div>
      {/* No "Highest Revenue Impact Findings" table — removed by request. The
          dashboard now ends on the two chart rows; `findings` is still carried
          in the dataset and by the API, so nothing needs recomputing to bring
          it back. */}
    </div>
  );
}
