import { createFileRoute, Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  FileSearch,
  Landmark,
  PlayCircle,
  Wallet,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import { useAssuranceDashboard } from "@/lib/rating/hooks";
import {
  fmtCount,
  fmtDate,
  fmtPct,
  money,
  statusTone,
  titleCase,
} from "@/lib/rating/format";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { LeakageRow } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/")({
  component: AssuranceOverviewPage,
});

function AssuranceOverviewPage() {
  const t = useT();
  const { data, isLoading, error, refetch } = useAssuranceDashboard();

  const kpis = data?.kpis;
  const hasData = !!kpis && kpis.total_cdrs > 0;

  return (
    <AppShell>
      <PageHeader
        title={t("Rating Assurance Overview")}
        description={t(
          "Expected versus billed revenue across every rating run: where money is leaking, who is being overcharged, and what is still uninvestigated.",
        )}
        info={t(
          "Undercharge and overcharge are never netted — one is revenue leakage, the other is customer harm.",
        )}
      />

      {isLoading && <RatingLoading label="Loading assurance KPIs…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {data && !hasData && (
        <RatingEmpty
          icon={BarChart3}
          title="No rated CDRs yet"
          description="Upload a CDR batch and start a rating run — every screen here fills from its results."
          action={
            <Link
              to="/rating/runs"
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <PlayCircle className="h-4 w-4" /> {t("Go to Rating Runs")}
            </Link>
          }
        />
      )}

      {hasData && kpis && (
        <div className="space-y-6">
          {/* --- Volume KPIs ------------------------------------------------ */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <StatTile
              icon={BarChart3}
              label={t("Total CDRs")}
              value={fmtCount(kpis.total_cdrs)}
            />
            <StatTile
              icon={CheckCircle2}
              label={`${t("Matched")} · ${fmtPct(kpis.match_rate)}`}
              value={fmtCount(kpis.matched_cdrs)}
            />
            <StatTile
              icon={ArrowUpRight}
              label={t("Undercharged")}
              value={fmtCount(kpis.by_status.UNDERCHARGED ?? 0)}
            />
            <StatTile
              icon={ArrowDownRight}
              label={t("Overcharged")}
              value={fmtCount(kpis.by_status.OVERCHARGED ?? 0)}
            />
          </div>

          {/* --- Revenue KPIs ----------------------------------------------- */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <StatTile
              icon={Landmark}
              label={t("Expected revenue")}
              value={money(kpis.expected_revenue)}
            />
            <StatTile
              icon={Wallet}
              label={t("Billed revenue")}
              value={money(kpis.billed_revenue)}
            />
            <StatTile
              icon={ArrowUpRight}
              label={t("Revenue leakage")}
              value={money(kpis.revenue_leakage)}
            />
            <StatTile
              icon={ArrowDownRight}
              label={t("Customer overcharge")}
              value={money(kpis.customer_overcharge)}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            {/* --- Revenue trend -------------------------------------------- */}
            <section className="bg-card border border-border rounded-xl p-5 xl:col-span-2">
              <h2 className="text-sm font-semibold text-foreground mb-1">
                {t("Expected vs billed revenue")}
              </h2>
              <p className="text-[12px] text-muted-foreground mb-4">
                {t(
                  "By CDR event date — replays land on the day they belong to.",
                )}
              </p>
              {data.trend.length === 0 ? (
                <p className="text-sm text-muted-foreground py-8 text-center">
                  {t("No traffic in the trend window.")}
                </p>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                      data={data.trend}
                      margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                    >
                      <defs>
                        <linearGradient id="raExp" x1="0" y1="0" x2="0" y2="1">
                          <stop
                            offset="5%"
                            stopColor="hsl(var(--primary))"
                            stopOpacity={0.25}
                          />
                          <stop
                            offset="95%"
                            stopColor="hsl(var(--primary))"
                            stopOpacity={0}
                          />
                        </linearGradient>
                      </defs>
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="hsl(var(--border))"
                      />
                      <XAxis
                        dataKey="date"
                        tickFormatter={(v: string) => fmtDate(v)}
                        tick={{ fontSize: 11 }}
                        stroke="hsl(var(--muted-foreground))"
                      />
                      <YAxis
                        tick={{ fontSize: 11 }}
                        stroke="hsl(var(--muted-foreground))"
                        width={56}
                      />
                      <Tooltip
                        formatter={(value: number, name: string) => [
                          money(value),
                          name,
                        ]}
                        labelFormatter={(v: string) => fmtDate(v)}
                      />
                      <Area
                        type="monotone"
                        dataKey="expected"
                        name={t("Expected")}
                        stroke="hsl(var(--primary))"
                        fill="url(#raExp)"
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="billed"
                        name={t("Billed")}
                        stroke="hsl(var(--muted-foreground))"
                        fill="transparent"
                        strokeWidth={2}
                        strokeDasharray="5 3"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            {/* --- Exception breakdown -------------------------------------- */}
            <section className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center justify-between gap-3 mb-4">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Result breakdown")}
                </h2>
                <Link
                  to="/rating/exceptions"
                  className="text-xs font-medium text-primary hover:underline"
                >
                  {t("Exceptions")} ({fmtCount(kpis.open_exceptions)})
                </Link>
              </div>
              <ul className="space-y-2">
                {Object.entries(kpis.by_status)
                  .sort(([, a], [, b]) => b - a)
                  .map(([status, count]) => (
                    <li key={status} className="flex items-center gap-3">
                      <span
                        className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border shrink-0 ${statusTone(status)}`}
                      >
                        {status}
                      </span>
                      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full rounded-full bg-primary/60"
                          style={{
                            width: `${Math.max(2, (count / kpis.total_cdrs) * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-[12px] font-semibold text-foreground tabular-nums w-16 text-right">
                        {fmtCount(count)}
                      </span>
                    </li>
                  ))}
              </ul>
            </section>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <LeakageTable
              title={t("Top products by exposure")}
              rows={data.by_product}
              keyLabel={t("Product")}
            />
            <LeakageTable
              title={t("Top root causes by exposure")}
              rows={data.by_root_cause}
              keyLabel={t("Root cause")}
              pretty
            />
          </div>

          {/* --- Latest runs ------------------------------------------------ */}
          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Latest rating runs")}
              </h2>
              <Link
                to="/rating/runs"
                className="text-xs font-medium text-primary hover:underline"
              >
                {t("View all runs")}
              </Link>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                    <th className="px-5 py-2.5 font-medium">{t("Run")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("Status")}</th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("CDRs")}
                    </th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("Match")}
                    </th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("Expected")}
                    </th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("Leakage")}
                    </th>
                    <th className="px-5 py-2.5 font-medium text-right">
                      {t("Finished")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.latest_runs.map((run) => (
                    <tr key={run.id} className="hover:bg-muted/30 transition">
                      <td className="px-5 py-2.5">
                        <Link
                          to="/rating/runs/$runId"
                          params={{ runId: run.id }}
                          className="font-mono text-[12px] text-primary hover:underline"
                        >
                          {run.id.slice(0, 8)}
                        </Link>
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${statusTone(run.status)}`}
                        >
                          {run.status}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtCount(run.rated_cdrs)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtPct(run.match_rate)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {money(run.expected_revenue)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-warning-foreground">
                        {money(run.undercharge_total)}
                      </td>
                      <td className="px-5 py-2.5 text-right text-[12px] text-muted-foreground">
                        {fmtDate(run.finished_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="flex flex-wrap gap-3">
            <Link
              to="/rating/records"
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted transition"
            >
              <FileSearch className="h-4 w-4" />
              {t("Browse reconciliation records")}
            </Link>
            <Link
              to="/rating/exceptions"
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted transition"
            >
              <AlertTriangle className="h-4 w-4" />
              {t("Open exceptions")}
            </Link>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function LeakageTable({
  title,
  rows,
  keyLabel,
  pretty,
}: {
  title: string;
  rows: LeakageRow[];
  keyLabel: string;
  pretty?: boolean;
}) {
  const t = useT();
  return (
    <section className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-border">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      </div>
      {rows.length === 0 ? (
        <p className="px-5 py-6 text-sm text-muted-foreground">
          {t("Nothing to report — no variance found.")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                <th className="px-5 py-2.5 font-medium">{keyLabel}</th>
                <th className="px-3 py-2.5 font-medium text-right">
                  {t("CDRs")}
                </th>
                <th className="px-3 py-2.5 font-medium text-right">
                  {t("Undercharge")}
                </th>
                <th className="px-3 py-2.5 font-medium text-right">
                  {t("Overcharge")}
                </th>
                <th className="px-5 py-2.5 font-medium text-right">
                  {t("Match")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.slice(0, 6).map((row) => (
                <tr key={row.key}>
                  <td className="px-5 py-2.5 text-foreground">
                    {pretty ? titleCase(row.key) : row.key}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {fmtCount(row.cdrs)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-warning-foreground">
                    {money(row.undercharge)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-destructive">
                    {money(row.overcharge)}
                  </td>
                  <td className="px-5 py-2.5 text-right tabular-nums text-muted-foreground">
                    {fmtPct(row.match_rate)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
