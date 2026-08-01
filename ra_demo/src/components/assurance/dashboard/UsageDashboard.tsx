import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  IndianRupee,
  Percent,
  RefreshCw,
} from "lucide-react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import {
  fetchUsageDashboard,
  type UsageDashboard as UsageData,
} from "@/lib/assurance/usage-dashboard-api";
import { KPICard } from "./KPICard";
import { DashboardCard, LegendItem } from "./DashboardCard";
import { DonutChart } from "./DonutChart";
import { HorizontalBarChart } from "./HorizontalBarChart";
import { VIZ, compact } from "./viz";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Usage Assurance dashboard.
//
// Every panel is computed from assurance.voice_sms_match_report and nothing
// else, so each number traces back to rows in that table. That constraint also
// decides what is NOT here: the template's twelve-month trend and thirty-day
// risk line have no source in this data, and drawing them would mean inventing
// history the table does not contain.
// ---------------------------------------------------------------------------

/** Service-mix slice colours — see the Service Mix donut for why not the default. */
const SERVICE_COLORS = ["var(--viz-c1)", "var(--viz-c6)"] as const;

export function UsageDashboard({ app }: { app: AppMetadata }) {
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchUsageDashboard()
      .then((next) => {
        setData(next);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  if (error) {
    return (
      <div className="ra-viz space-y-4">
        <Header app={app} source={null} onRefresh={load} loading={loading} />
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <span>Could not load the match report: {error}</span>
          <Button variant="outline" size="sm" className="h-7 px-2 text-xs" onClick={load}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="ra-viz space-y-4">
        <Header app={app} source={null} onRefresh={load} loading={loading} />
        <p className="text-sm text-muted-foreground">Loading the match report…</p>
      </div>
    );
  }

  const h = data.headline;
  // A single load means one data point. Saying so beats a chart that implies a
  // measured trend where only one measurement exists.
  const singlePoint = data.daily.length < 2;

  return (
    <div className="ra-viz space-y-5 pb-2">
      <Header app={app} source={data.source} onRefresh={load} loading={loading} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <KPICard
          icon={Activity}
          title="Records Evaluated"
          description="Voice and SMS records compared between MSC and IN."
          value={h.evaluated.toLocaleString()}
        />
        <KPICard
          icon={CheckCircle2}
          title="Matched"
          description="Records where MSC and IN agree."
          value={h.matched.toLocaleString()}
        />
        <KPICard
          icon={AlertTriangle}
          title="Unmatched"
          description="Records with no counterpart, or where the numbers differ."
          value={h.unmatched.toLocaleString()}
        />
        <KPICard
          icon={Percent}
          title="Match Rate"
          description="Matched records as a percentage of those evaluated."
          value={`${h.matchRatePct.toFixed(1)}%`}
        />
        <KPICard
          icon={IndianRupee}
          title="Debit Captured"
          description="Total debit amount on matched records."
          value={`₹${h.debitTotal.toFixed(2)}`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <DashboardCard
          className="xl:col-span-6"
          title="Match Outcome by Service"
          subtitle="MSC vs IN, per service type"
          meta={
            <div className="flex items-center gap-3">
              <LegendItem color={VIZ.healthy} label="Matched" />
              <LegendItem color={VIZ.exceptions} label="Unmatched" />
            </div>
          }
        >
          {/* Centred and matched to the donuts' height: with only two service
              types the old top-aligned list left most of this card empty while
              the cards beside it were full. */}
          <div className="flex min-h-[268px] flex-col justify-center gap-8 px-4 py-3">
            {data.byService.map((s) => (
              <div key={s.service}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-foreground">{s.service}</span>
                  <span className="tabular text-xs text-muted-foreground">
                    {s.matched.toLocaleString()} matched of {s.evaluated.toLocaleString()} ·{" "}
                    {s.matchRatePct.toFixed(1)}%
                  </span>
                </div>
                {/* One bar per service, split by outcome — the proportion is
                    the point, so the two segments share a track. */}
                <div className="mt-1.5 flex h-3 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full"
                    style={{
                      width: `${(s.matched / Math.max(1, s.evaluated)) * 100}%`,
                      background: VIZ.healthy,
                    }}
                    title={`${s.matched} matched`}
                  />
                  <div
                    className="h-full"
                    style={{
                      width: `${(s.unmatched / Math.max(1, s.evaluated)) * 100}%`,
                      background: VIZ.exceptions,
                    }}
                    title={`${s.unmatched} unmatched`}
                  />
                </div>
              </div>
            ))}
          </div>
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-3"
          title="Status Distribution"
          subtitle="Every record in the report"
        >
          <DonutChart
            data={data.statusSplit.map((s) => ({ name: s.status, value: s.count }))}
            centerLabel="Records"
            valueFormatter={compact}
            // Same two colours the card to the left uses for the same two
            // outcomes. Keyed by status rather than by position, because the
            // server orders this list by count and the bigger slice is not
            // always the same outcome.
            colors={data.statusSplit.map((s) =>
              s.status === "MATCHED" ? VIZ.healthy : VIZ.exceptions,
            )}
          />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-3"
          title="Service Mix"
          subtitle="Records by service type"
        >
          <DonutChart
            data={data.byService.map((s) => ({ name: s.service, value: s.evaluated }))}
            centerLabel="Records"
            valueFormatter={compact}
            // Blue and violet, deliberately avoiding the green/orange pair: a
            // service is not an outcome, and SMS drawn in the "unmatched"
            // orange invited exactly that misreading.
            colors={SERVICE_COLORS}
          />
        </DashboardCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <DashboardCard
          className="xl:col-span-6"
          title="Top Calling Numbers — Unmatched"
          subtitle="Subscribers with the most unmatched records"
        >
          <HorizontalBarChart
            data={data.topCalling.map((t) => ({ name: t.number, value: t.unmatched }))}
            color={VIZ.exceptions}
            valueFormatter={(v) => v.toLocaleString()}
          />
        </DashboardCard>

        <DashboardCard
          className="xl:col-span-6"
          title="Top Called Numbers — Unmatched"
          subtitle="Destinations with the most unmatched records"
        >
          <HorizontalBarChart
            data={data.topCalled.map((t) => ({ name: t.number, value: t.unmatched }))}
            // Both top-N panels measure the same thing — unmatched records — so
            // they carry the same colour. The violet here read as a third
            // category that does not exist.
            color={VIZ.exceptions}
            valueFormatter={(v) => v.toLocaleString()}
          />
        </DashboardCard>
      </div>

      {singlePoint && (
        <p className="text-xs text-muted-foreground">
          The report holds a single load
          {data.daily[0] ? ` (${data.daily[0].day})` : ""}, so there is no trend to plot yet.
          These panels describe that load; a second load adds the first comparison.
        </p>
      )}
    </div>
  );
}

function Header({
  app,
  source,
  onRefresh,
  loading,
}: {
  app: AppMetadata;
  source: string | null;
  onRefresh: () => void;
  loading: boolean;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-tight text-foreground">{app.name}</h1>
        <p className="mt-1 max-w-3xl text-[13.5px] leading-relaxed text-muted-foreground">
          Voice and SMS records reconciled between the MSC and the IN.
          {source && (
            <>
              {" "}
              Every figure here comes from <span className="font-mono text-xs">{source}</span>.
            </>
          )}
        </p>
      </div>
      <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={onRefresh} disabled={loading}>
        <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        Refresh
      </Button>
    </header>
  );
}

