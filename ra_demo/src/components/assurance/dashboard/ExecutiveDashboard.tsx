import {
  AlertTriangle,
  Database,
  IndianRupee,
  Percent,
  ShieldCheck,
} from "lucide-react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { getDashboard } from "@/lib/assurance/dashboard-config";
import { KPICard } from "./KPICard";
import { DashboardCard, LegendItem } from "./DashboardCard";
import { DashboardFilters } from "./DashboardFilters";
import { AREA_SERIES, AreaTrendChart } from "./AreaTrendChart";
import { RiskLineChart } from "./RiskLineChart";
import { DonutChart } from "./DonutChart";
import { HorizontalBarChart } from "./HorizontalBarChart";
import { ExecutiveTable } from "./ExecutiveTable";
import { VIZ, compact } from "./viz";

// ---------------------------------------------------------------------------
// The executive dashboard, shared by all eight assurance apps.
//
// Nothing below branches on the app. The layout, the five KPIs, the six cards
// and the table are fixed; `getDashboard(app)` supplies the titles, labels and
// datasets. Adding a ninth assurance means adding a profile to
// dashboard-config.ts and nothing else.
//
// The reading order is deliberate — how much is at risk, how much was analysed,
// how many issues, are reconciliations healthy, is it improving, which systems
// are responsible, which categories leak most, which findings cost most.
// ---------------------------------------------------------------------------

export function ExecutiveDashboard({ app }: { app: AppMetadata }) {
  const d = getDashboard(app);
  const k = d.kpis;

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
            Assurance engine live
            <span className="mx-1 text-border">·</span>
            <span className="font-mono">{app.controlRange}</span>
          </div>
        </div>

        <DashboardFilters />
      </header>

      {/* 1–5 — the five KPIs, identical on every assurance */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <KPICard
          icon={IndianRupee}
          title="Revenue at Risk"
          description="Estimated financial exposure detected during assurance validation."
          sparkId={`spark-risk-${d.id}`}
          {...k.revenueAtRisk}
        />
        <KPICard
          icon={Database}
          title={`Records Evaluated · ${d.recordUnit}`}
          description="Total business records analysed during the selected period."
          sparkId={`spark-records-${d.id}`}
          {...k.recordsEvaluated}
        />
        <KPICard
          icon={AlertTriangle}
          title="Assurance Exceptions"
          description="Business records violating assurance rules."
          sparkId={`spark-exceptions-${d.id}`}
          {...k.exceptions}
        />
        <KPICard
          icon={Percent}
          title="Exception Rate"
          description="Percentage of exception records against evaluated records."
          sparkId={`spark-rate-${d.id}`}
          {...k.exceptionRate}
        />
        <KPICard
          icon={ShieldCheck}
          title="Reconciliation Success"
          description="Percentage of records successfully reconciled between source systems."
          sparkId={`spark-recon-${d.id}`}
          {...k.reconciliation}
        />
      </div>

      {/* Row 1 — trend, risk, distribution */}
      <div className="grid gap-4 xl:grid-cols-12">
        <DashboardCard
          className="xl:col-span-6"
          title={d.trendTitle}
          subtitle="Last 12 months · thousands of records"
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
          subtitle="Last 30 days · ₹ Cr"
        >
          <RiskLineChart data={d.revenueAtRisk} />
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
          subtitle="Sorted by revenue impact · ₹ Cr"
        >
          <HorizontalBarChart
            data={d.leakageCategories}
            color={VIZ.leakage}
            valueFormatter={(v) => `₹${v.toFixed(2)} Cr`}
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

      {/* 8 — the money shot */}
      <DashboardCard
        title="Highest Revenue Impact Findings"
        subtitle="Top five findings by financial exposure"
        bodyClassName="px-0 pb-0"
      >
        <ExecutiveTable findings={d.findings} />
      </DashboardCard>
    </div>
  );
}
