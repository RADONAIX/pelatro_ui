import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  FileQuestion,
  Gauge,
  Landmark,
  Wallet,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import {
  useAssuranceDashboard,
  useLeakage,
  useRatingRuns,
} from "@/lib/rating/hooks";
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

export const Route = createFileRoute("/rating/reconciliation")({
  component: ReconciliationDashboardPage,
});

const DIMENSIONS = [
  { value: "product", label: "Product" },
  { value: "service", label: "Service" },
  { value: "destination_zone", label: "Destination zone" },
  { value: "time_band", label: "Time band" },
  { value: "root_cause", label: "Root cause" },
  { value: "status", label: "Status" },
];

function ReconciliationDashboardPage() {
  const t = useT();
  const [runId, setRunId] = useState("");
  const [dimension, setDimension] = useState("product");

  const { data: runs = [] } = useRatingRuns();
  const { data, isLoading, error, refetch } = useAssuranceDashboard({
    run_id: runId || undefined,
  });
  const { data: leakage = [] } = useLeakage(dimension, {
    run_id: runId || undefined,
  });

  const kpis = data?.kpis;
  const hasData = !!kpis && kpis.total_cdrs > 0;

  return (
    <AppShell>
      <PageHeader
        title={t("Reconciliation Dashboard")}
        description={t(
          "Expected versus billed, cut every way that matters: by day, by product, by destination, by root cause — with the two exposures always kept apart.",
        )}
        info={t(
          "Slices are ranked by undercharge plus overcharge, so a product that leaks and overbills cannot hide behind a net of zero.",
        )}
      />

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <Select
          value={runId}
          onChange={setRunId}
          options={[
            { value: "", label: t("All runs") },
            ...runs.map((r) => ({
              value: r.id,
              label: `${r.id.slice(0, 8)} · ${fmtDate(r.created_at)}`,
            })),
          ]}
          minWidth={210}
          ariaLabel={t("Filter by run")}
        />
        <Select
          value={dimension}
          onChange={setDimension}
          options={DIMENSIONS.map((d) => ({
            value: d.value,
            label: t(d.label),
          }))}
          minWidth={180}
          ariaLabel={t("Break down by")}
        />
      </div>

      {isLoading && <RatingLoading label="Loading reconciliation…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {data && !hasData && (
        <RatingEmpty
          icon={Gauge}
          title="Nothing reconciled yet"
          description="Run a rating first — this dashboard fills from its results."
        />
      )}

      {hasData && kpis && (
        <div className="space-y-6">
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
              icon={FileQuestion}
              label={t("Unrated / no rule")}
              value={fmtCount(kpis.unrated_cdrs + kpis.no_matching_rule)}
            />
            <StatTile
              icon={Gauge}
              label={t("Open exceptions")}
              value={fmtCount(kpis.open_exceptions)}
            />
          </div>

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

          {/* --- Exposure by dimension ------------------------------------- */}
          <section className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-semibold text-foreground mb-4">
              {t("Exposure by")}{" "}
              {t(DIMENSIONS.find((d) => d.value === dimension)?.label ?? "")}
            </h2>
            {leakage.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">
                {t("No variance in this dimension.")}
              </p>
            ) : (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={leakage.slice(0, 10).map((r) => ({
                      ...r,
                      key:
                        dimension === "root_cause" || dimension === "status"
                          ? titleCase(r.key)
                          : r.key,
                    }))}
                    margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="hsl(var(--border))"
                    />
                    <XAxis
                      dataKey="key"
                      tick={{ fontSize: 11 }}
                      stroke="hsl(var(--muted-foreground))"
                      interval={0}
                      angle={leakage.length > 6 ? -20 : 0}
                      height={40}
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
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar
                      dataKey="undercharge"
                      name={t("Undercharge")}
                      fill="hsl(var(--primary))"
                      radius={[3, 3, 0, 0]}
                    />
                    <Bar
                      dataKey="overcharge"
                      name={t("Overcharge")}
                      fill="hsl(var(--destructive))"
                      radius={[3, 3, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </section>

          {/* --- Result mix ------------------------------------------------ */}
          <section className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-semibold text-foreground mb-4">
              {t("Result mix")}
            </h2>
            <div className="flex flex-wrap gap-2">
              {Object.entries(kpis.by_status)
                .sort(([, a], [, b]) => b - a)
                .map(([status, count]) => (
                  <Link
                    key={status}
                    to="/rating/records"
                    search={{ status, run_id: runId || undefined }}
                    className={`text-[11px] font-semibold px-2.5 py-1.5 rounded-md border transition hover:opacity-80 ${statusTone(status)}`}
                  >
                    {status}
                    <span className="ml-1.5 tabular-nums">
                      {fmtCount(count)}
                    </span>
                  </Link>
                ))}
            </div>
            <p className="text-[12px] text-muted-foreground mt-3">
              {t("Click a status to open the records behind it.")}
            </p>
          </section>
        </div>
      )}
    </AppShell>
  );
}
