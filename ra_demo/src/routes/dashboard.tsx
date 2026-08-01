import { createFileRoute } from "@tanstack/react-router";
import { createContext, useContext } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BadgePercent,
  Briefcase,
  Calendar,
  ChevronRight,
  Clock,
  Copy,
  Database,
  DollarSign,
  FileCheck2,
  FileInput,
  FileText,
  FileWarning,
  FileX2,
  FolderOpen,
  Gauge,
  Layers,
  Lightbulb,
  MessageSquare,
  Phone,
  RefreshCw,
  Search,
  ShieldCheck,
  Siren,
  Sparkles,
  SunMedium,
  Wifi,
} from "lucide-react";

import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Delta, LinkAction, Panel } from "@/components/dashboard/Panel";

import {
  useEnterpriseDashboard,
  type DashboardData,
} from "@/lib/enterpriseDashboard";

import { ASSURANCE_APPS } from "@/lib/assuranceScope";

export const Route = createFileRoute("/dashboard")({
  head: () => ({
    meta: [
      {
        title: "Enterprise Dashboard — RADONaix Enterprise Assurance",
      },
      {
        name: "description",
        content:
          "Enterprise-level overview of revenue, leakage, controls, cases and data health across all assurance applications.",
      },
    ],
  }),
  component: EnterpriseDashboardPage,
});

// The live payload, or the bundled sample while it loads / if it fails. Panels
// read this rather than importing the sample directly, so none of them knows
// which of the two it is rendering.
const DashboardCtx = createContext<DashboardData | null>(null);

const useDash = (): DashboardData => {
  const value = useContext(DashboardCtx);
  if (!value)
    throw new Error("Dashboard panels must render inside DashboardCtx");
  return value;
};

/**
 * Icon and tint per KPI, in the order the server returns them. The values come
 * from the API; only the presentation lives here, so a KPI whose figure moves
 * does not need a change in this file.
 */
const kpis = [
  {
    label: "Total Revenue (MTD)",
    value: "$24.58M",
    delta: "12.6%",
    good: true,
    icon: DollarSign,
    iconStyle: "bg-blue-50 text-blue-600",
  },
  {
    label: "Total CDRs (MTD)",
    value: "1.25 B",
    delta: "10.9%",
    good: true,
    icon: Database,
    iconStyle: "bg-violet-50 text-violet-600",
  },
  {
    label: "Potential Leakage (MTD)",
    value: "$3.82M",
    delta: "8.4%",
    good: false,
    icon: AlertTriangle,
    iconStyle: "bg-amber-50 text-amber-600",
  },
  {
    label: "Leakage % (MTD)",
    value: "0.18%",
    delta: "0.03 pp",
    good: false,
    icon: BadgePercent,
    iconStyle: "bg-red-50 text-red-600",
  },
  {
    label: "Controls Effectiveness",
    value: "98.6%",
    delta: "1.2 pp",
    good: true,
    icon: ShieldCheck,
    iconStyle: "bg-emerald-50 text-emerald-600",
  },
  {
    label: "Open Critical Cases",
    value: "12",
    delta: "4",
    good: false,
    icon: Briefcase,
    iconStyle: "bg-rose-50 text-rose-600",
  },
];

const techIcons = {
  wifi: Wifi,
  phone: Phone,
  sms: MessageSquare,
} as const;

const flowIcons = [Layers, Layers, Activity, Gauge, FileText, FileCheck2];

const healthIcons = [
  FileInput,
  FileCheck2,
  SunMedium,
  FileWarning,
  Copy,
  Clock,
];

function EnterpriseDashboardPage() {
  const data = useEnterpriseDashboard();

  return (
    <DashboardCtx.Provider value={data}>
      <AppShell>
        <div className="-m-4 min-h-screen bg-muted/25 p-4 sm:-m-6 sm:p-6">
          <div className="space-y-4">
            <PageHeader
              title="Enterprise Dashboard"
              description={`High-level view across all ${ASSURANCE_APPS.length} assurance applications. This dashboard is independent of the assurance scope selected in the header.`}
              actions={<DashboardFilters />}
            />

            {!data.loading && !data.live && (
              <div className="rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-2.5 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                Showing sample figures — the assurance data service did not
                answer{data.error ? `: ${data.error}` : "."}
              </div>
            )}

            <KpiSection />
            <OverviewSection />
            <AssuranceSection />
          </div>
        </div>
      </AppShell>
    </DashboardCtx.Provider>
  );
}

function DashboardFilters() {
  const { range } = useDash();

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-xs font-medium shadow-xs transition-colors hover:bg-muted/50"
      >
        <Calendar className="size-4 text-muted-foreground" />
        {range || "All available months"}
      </button>

      <span className="inline-flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
        <RefreshCw className="size-3.5" />
        Last refreshed: 10:30 AM
      </span>
    </div>
  );
}

function KpiSection() {
  const { kpis: live } = useDash();
  // Positional merge: the server returns the six in the order this array
  // declares them, and both are keyed off the same product decision about what
  // the top row shows.
  const cards = kpis.map((kpi, i) => ({ ...kpi, ...(live?.[i] ?? {}) }));

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      {cards.map((kpi) => {
        const Icon = kpi.icon;

        return (
          <div
            key={kpi.label}
            className="card-surface flex items-center gap-3 p-4 transition-shadow duration-200 hover:shadow-md"
          >
            <span
              className={`grid size-10 shrink-0 place-items-center rounded-full ${kpi.iconStyle}`}
            >
              <Icon className="size-5" />
            </span>

            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase leading-tight tracking-wide text-muted-foreground">
                {kpi.label}
              </p>

              <p className="mt-0.5 text-xl font-bold tracking-tight text-foreground">
                {kpi.value}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function OverviewSection() {
  return (
    <div className="grid items-stretch gap-3 xl:grid-cols-4">
      <RevenueTrendPanel />
      <LeakageBreakdownPanel />
      <LeakageDriversPanel />
      <ControlHealthPanel />
    </div>
  );
}

function RevenueTrendPanel() {
  const { revenueTrend, trendUnit } = useDash();

  return (
    <Panel
      title="Revenue Trend"
      subtitle="(MTD)"
      action={<LinkAction label="View Full Report" />}
    >
      <p className="mb-1 text-[10px] text-muted-foreground">{trendUnit}</p>

      <div className="h-[240px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={revenueTrend}
            margin={{
              top: 5,
              right: 8,
              bottom: 0,
              left: -18,
            }}
          >
            <CartesianGrid
              stroke="var(--border)"
              strokeDasharray="3 3"
              vertical={false}
            />

            <XAxis
              dataKey="day"
              tick={{ fontSize: 10 }}
              stroke="var(--muted-foreground)"
            />

            <YAxis
              tick={{ fontSize: 10 }}
              stroke="var(--muted-foreground)"
              unit="M"
            />

            <Tooltip
              contentStyle={{
                borderRadius: 12,
                border: "1px solid var(--border)",
                fontSize: 12,
                background: "var(--card)",
              }}
            />

            <Legend wrapperStyle={{ fontSize: 11 }} />

            <Line
              type="monotone"
              dataKey="total"
              name="Total Revenue"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />

            <Line
              type="monotone"
              dataKey="billed"
              name="Billed Revenue"
              stroke="#22c55e"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />

            <Line
              type="monotone"
              dataKey="leakage"
              name="Potential Leakage"
              stroke="#ef4444"
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

function LeakageBreakdownPanel() {
  const { leakageBreakdown, leakageTotal } = useDash();

  return (
    <Panel
      title="Leakage Breakdown"
      subtitle="(MTD)"
      action={<LinkAction label="View Details" />}
    >
      {/* Stacked until there is real width for two columns — side by side in a
          ~260px panel is what squeezed the legend labels to "B…" / "R…". */}
      <div className="grid grid-cols-1 items-center gap-3 2xl:grid-cols-[42%_58%] 2xl:gap-4">
        <div className="mx-auto grid h-[150px] w-[150px] place-items-center rounded-full bg-muted/30 p-1">
          <div className="h-full w-full [grid-area:1/1]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={leakageBreakdown}
                  dataKey="value"
                  cx="50%"
                  cy="50%"
                  innerRadius="62%"
                  outerRadius="91%"
                  paddingAngle={2}
                  stroke="none"
                  label={false}
                  labelLine={false}
                  isAnimationActive={false}
                >
                  {leakageBreakdown.map((item) => (
                    <Cell key={item.name} fill={item.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="pointer-events-none z-10 w-[86px] text-center [grid-area:1/1]">
            <p className="text-base font-bold leading-tight text-foreground">
              {leakageTotal}
            </p>
            <p className="text-[9px] font-medium uppercase leading-tight tracking-wide text-muted-foreground">
              Total Leakage
            </p>
          </div>
        </div>

        <ul className="min-w-0 space-y-1">
          {leakageBreakdown.map((item) => (
            <li
              key={item.name}
              className="grid items-center gap-x-2 rounded-lg px-1.5 py-1 text-[11px] transition-colors hover:bg-muted/60"
              style={{
                gridTemplateColumns: "minmax(0,1fr) max-content max-content",
              }}
            >
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                />

                <span className="truncate font-medium">{item.name}</span>
              </span>

              <span className="whitespace-nowrap font-semibold">
                {item.display}
              </span>

              <span className="whitespace-nowrap text-muted-foreground">
                {item.pct}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}

function LeakageDriversPanel() {
  const { leakageDrivers } = useDash();

  return (
    <Panel title="Top Leakage Drivers" subtitle="(MTD)" action={<LinkAction />}>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full table-auto text-left text-[11px]">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-1 pb-2 font-medium">Driver</th>
              <th className="px-1 pb-2 font-medium">Module</th>
              <th className="px-1 pb-2 font-medium">Leakage (USD)</th>
              <th className="px-1 pb-2 text-right font-medium">% Impact</th>
            </tr>
          </thead>

          <tbody>
            {leakageDrivers.map((driver) => (
              <tr
                key={driver.driver}
                className="border-b border-border/60 last:border-0"
              >
                <td className="px-1 py-2 font-medium">{driver.driver}</td>

                <td className="px-1 py-2 text-muted-foreground">
                  {driver.module}
                </td>

                <td className="px-1 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="tabular whitespace-nowrap font-semibold">
                      {driver.leakage}
                    </span>

                    <span
                      className="h-1.5 shrink-0 rounded-full"
                      style={{
                        backgroundColor: driver.color,
                        width: `${Math.min(24, Math.max(8, driver.impact))}px`,
                      }}
                    />
                  </div>
                </td>

                <td className="tabular whitespace-nowrap px-1 py-2 text-right font-semibold">
                  {driver.impact}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function ControlHealthPanel() {
  const { controlHealth } = useDash();

  return (
    <Panel title="Control Health Overview" action={<LinkAction />}>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full table-auto text-left text-[11px]">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-1 pb-2 font-medium">Control Type</th>
              <th className="px-1 pb-2 font-medium">Effectiveness</th>
              <th className="px-1 pb-2 text-right font-medium">
                Trend vs Apr 2025
              </th>
            </tr>
          </thead>

          <tbody>
            {controlHealth.map((control) => (
              <tr key={control.type} className="border-b border-border/60">
                <td className="px-1 py-2">{control.type}</td>

                <td className="tabular px-1 py-2 font-semibold">
                  {control.effectiveness}
                </td>

                <td className="whitespace-nowrap px-1 py-2 text-right">
                  <Delta value={control.trend} />
                </td>
              </tr>
            ))}

            <tr className="bg-muted/60">
              <td className="px-1 py-2 font-bold">Overall</td>
              <td className="px-1 py-2 font-bold">98.6%</td>
              <td className="px-1 py-2 text-right">
                <Delta value="1.2 pp" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function AssuranceSection() {
  const { prepaidTech, postpaidTech, prepaidFlow, postpaidFlow } = useDash();

  return (
    <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)]">
      <div className="grid content-start gap-3">
        <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,0.62fr)_minmax(0,1.8fr)]">
          <TechPanel
            title="Prepaid Revenue by Technology"
            items={prepaidTech}
          />

          <FlowPanel
            title="Prepaid Assurance – Module Flow"
            items={prepaidFlow}
          />
        </div>

        <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,0.62fr)_minmax(0,1.8fr)]">
          <TechPanel
            title="Postpaid Revenue by Technology"
            items={postpaidTech}
          />

          <FlowPanel
            title="Postpaid Assurance – Module Flow"
            items={postpaidFlow}
          />
        </div>

        <DataHealthPanel />
      </div>

      <div className="grid content-start gap-3">
        <AlertsSnapshotPanel />
        <AiRecommendedActionsPanel />
      </div>
    </div>
  );
}

function DataHealthPanel() {
  const { dataHealth } = useDash();

  return (
    <Panel title="Data Health" subtitle="(MTD)">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {dataHealth.map((item, index) => {
          const Icon = healthIcons[index] ?? FileX2;

          return (
            <div key={item.label} className="flex items-center gap-2">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted">
                <Icon className="size-4 text-muted-foreground" />
              </span>

              <div className="min-w-0">
                <p className="truncate text-[10px] text-muted-foreground">
                  {item.label}
                </p>

                <p className="text-base font-bold leading-tight">
                  {item.value}
                </p>

                <Delta value={item.delta} up={item.up} good={item.good} />
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function AlertsSnapshotPanel() {
  const { alerts } = useDash();
  const show = (n: number | undefined) =>
    n === undefined ? "—" : n.toLocaleString("en-IN");

  return (
    <Panel title="Alerts & Cases Snapshot" action={<LinkAction />}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <AlertTile
          icon={Siren}
          label="Critical Alerts"
          value={show(alerts?.criticalAlerts)}
          delta="open now"
          containerStyle="border-red-200 bg-red-50/80"
          iconStyle="bg-red-100 text-red-600"
          good={false}
        />

        <AlertTile
          icon={FolderOpen}
          label="Open Cases"
          value={show(alerts?.openCases)}
          delta="open now"
          containerStyle="border-amber-200 bg-amber-50/80"
          iconStyle="bg-amber-100 text-amber-600"
          good={false}
        />

        <AlertTile
          icon={Lightbulb}
          label="High Priority Cases"
          value={show(alerts?.highPriority)}
          delta="open now"
          containerStyle="border-violet-200 bg-violet-50/80"
          iconStyle="bg-violet-100 text-violet-600"
          good={false}
        />

        <AlertTile
          icon={Search}
          label="Pending Investigations"
          value={show(alerts?.pendingInvestigations)}
          delta="open now"
          containerStyle="border-blue-200 bg-blue-50/80"
          iconStyle="bg-blue-100 text-blue-600"
          good={false}
        />
      </div>
    </Panel>
  );
}

// Replace AiRecommendedActionsPanel with this.

function AiRecommendedActionsPanel() {
  const { aiActions } = useDash();

  return (
    <Panel title="AI Recommended Actions" action={<LinkAction />}>
      <ul className="space-y-1">
        {aiActions.map((action) => (
          <li
            key={action.label}
            className="flex items-center gap-2 rounded-xl px-2 py-2.5 transition-colors hover:bg-muted/60"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-amber-100 text-amber-600">
              <Sparkles className="size-4" />
            </span>

            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] font-semibold">
                {action.label}
              </p>

              <p className="truncate text-[10px] text-muted-foreground">
                Potential impact: {action.impact}
              </p>
            </div>

            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                action.priority === "High"
                  ? "border-red-200 bg-red-50 text-red-600"
                  : action.priority === "Medium"
                    ? "border-amber-200 bg-amber-50 text-amber-600"
                    : "border-emerald-200 bg-emerald-50 text-emerald-600"
              }`}
            >
              {action.priority}
            </span>

            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
          </li>
        ))}
      </ul>

      <p className="mt-3 flex items-center justify-end gap-1 text-[10px] text-muted-foreground">
        Powered by RADONaix AI
        <Sparkles className="size-3 text-amber-500" />
      </p>
    </Panel>
  );
}

function TechPanel({
  title,
  items,
}: {
  title: string;
  items: {
    label: string;
    value: string;
    share: string;
    delta: string;
    icon: string;
  }[];
}) {
  return (
    <Panel title={title} subtitle="(MTD)">
      <div
        className="grid gap-2"
        style={{
          gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))`,
        }}
      >
        {items.map((item) => {
          const Icon = techIcons[item.icon as keyof typeof techIcons] ?? Wifi;

          return (
            <div key={item.label} className="text-center">
              <span className="mx-auto grid size-9 place-items-center rounded-full bg-muted">
                <Icon className="size-4 text-muted-foreground" />
              </span>

              <p className="mt-1.5 text-[11px] text-muted-foreground">
                {item.label}
              </p>

              <p className="text-sm font-bold">{item.value}</p>

              <p className="text-[10px] text-muted-foreground">{item.share}</p>

              <Delta value={item.delta} />
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function FlowPanel({
  title,
  items,
}: {
  title: string;
  items: {
    label: string;
    value: string;
    share: string;
    delta: string;
  }[];
}) {
  return (
    <Panel title={title} subtitle="(MTD)">
      <div className="flex flex-wrap items-stretch gap-2 lg:flex-nowrap">
        {items.map((item, index) => {
          const Icon = flowIcons[index] ?? Layers;

          return (
            <div
              key={item.label}
              className="flex min-w-0 flex-1 items-stretch gap-1.5"
            >
              <div className="flex min-w-0 flex-1 flex-col justify-start rounded-xl border border-border px-1.5 py-2 text-center">
                <div className="flex items-start justify-center gap-1">
                  <Icon className="mt-[1px] size-3 shrink-0 text-info" />

                  <span className="text-[9px] font-medium leading-tight">
                    {item.label}
                  </span>
                </div>

                <p className="mt-1 text-sm font-bold">{item.value}</p>

                <p className="text-[10px] text-muted-foreground">
                  {item.share}
                </p>

                <Delta value={item.delta} />
              </div>

              {index < items.length - 1 ? (
                <ArrowRight className="hidden size-3.5 shrink-0 self-center text-muted-foreground lg:block" />
              ) : null}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function AlertTile({
  icon: Icon,
  label,
  value,
  delta,
  containerStyle,
  iconStyle,
  good,
}: {
  icon: typeof Siren;
  label: string;
  value: string;
  delta: string;
  containerStyle: string;
  iconStyle: string;
  good: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-3 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md ${containerStyle}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`grid size-8 shrink-0 place-items-center rounded-lg ${iconStyle}`}
        >
          <Icon className="size-4" />
        </span>

        <p className="min-w-0 truncate text-[11px] font-medium text-muted-foreground">
          {label}
        </p>
      </div>

      <p className="mt-2 text-xl font-bold leading-tight text-foreground">
        {value}
      </p>

      <Delta value={delta} good={good} />
    </div>
  );
}
