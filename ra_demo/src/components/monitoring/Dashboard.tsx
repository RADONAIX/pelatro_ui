// ---------------------------------------------------------------------------
// The System Monitoring dashboard.
//
// Three panel groups — system (node_exporter), api, postgres — assembled per
// host from what that host actually runs. A database box shows postgres panels;
// an app box does not, and neither pretends the other's metrics exist.
//
// All values are simulated in the browser (see lib/monitoring/metrics.ts). The
// header says so, because a monitoring screen that cannot be told from a live
// one is worse than no monitoring screen.
// ---------------------------------------------------------------------------

import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  API_SERIES,
  MetricStream,
  POSTGRES_SERIES,
  REFRESH_OPTIONS,
  SYSTEM_SERIES,
  TIME_RANGES,
  WINDOW_POINTS,
  fmtNumber,
  type Sample,
} from "@/lib/monitoring/metrics";
import { Gauge, NoData, Panel, StatTile, TimeSeriesPanel } from "./panels";
import { PANEL, SERIES_COLORS, STATUS, STATUS_INK } from "./viz";

export type PanelGroup = "system" | "api" | "postgres";

/** Host uptimes are fixed per mount — a counter, not a walk. */
const BOOTED_AT = Date.now() - 3.7 * 86_400_000;

export function MonitoringDashboard({
  groups,
  hostLabel,
}: {
  groups: readonly PanelGroup[];
  /** Names the host in each panel's subtitle, so a screenshot is unambiguous. */
  hostLabel: string;
}) {
  const [rangeId, setRangeId] = useState(TIME_RANGES[2].id); // Last 6 hours
  const [refreshId, setRefreshId] = useState(REFRESH_OPTIONS[2].id); // 3s
  const [data, setData] = useState<Sample[]>([]);
  const streamRef = useRef<MetricStream | null>(null);

  const range = TIME_RANGES.find((r) => r.id === rangeId) ?? TIME_RANGES[2];
  const refresh = REFRESH_OPTIONS.find((r) => r.id === refreshId) ?? REFRESH_OPTIONS[2];

  const specs = useMemo(
    () => [
      ...(groups.includes("system") ? SYSTEM_SERIES : []),
      ...(groups.includes("api") ? API_SERIES : []),
      ...(groups.includes("postgres") ? POSTGRES_SERIES : []),
    ],
    [groups],
  );

  // A new stream per host and per range: the window's timestamps are a function
  // of the range, so changing it re-seeds rather than rescaling stale points.
  useEffect(() => {
    const stream = new MetricStream(specs, WINDOW_POINTS, range.ms);
    streamRef.current = stream;
    setData(stream.current());
  }, [specs, range.ms, hostLabel]);

  useEffect(() => {
    if (refresh.ms == null) return;
    const id = window.setInterval(() => {
      const stream = streamRef.current;
      if (stream) setData(stream.tick());
    }, refresh.ms);
    return () => window.clearInterval(id);
  }, [refresh.ms, specs, range.ms, hostLabel]);

  const manualRefresh = () => {
    const stream = streamRef.current;
    if (stream) setData(stream.tick());
  };

  const latest = (key: string) => data[data.length - 1]?.[key] ?? 0;
  const cpuBusy =
    latest("cpuUser") + latest("cpuSystem") + latest("cpuIowait") + latest("cpuSoftirq") + latest("cpuSteal");
  const memPct = (latest("memUsed") / (latest("memTotal") || 64)) * 100;
  const uptime = (Date.now() - BOOTED_AT) / 86_400_000;

  return (
    <div className="space-y-3">
      <Toolbar
        rangeId={rangeId}
        onRange={setRangeId}
        refreshId={refreshId}
        onRefreshId={setRefreshId}
        onRefreshNow={manualRefresh}
        paused={refresh.ms == null}
      />

      {groups.includes("api") && (
        <section className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              title="Request rate"
              value={fmtNumber(latest("reqRate"), 2)}
              unit="req/s"
              data={data}
              dataKey="reqRate"
              color={SERIES_COLORS[0]}
            />
            <StatTile
              title="Error rate (5xx)"
              value={fmtNumber(latest("errRate"), 2)}
              unit="req/s"
              data={data}
              dataKey="errRate"
              color={STATUS.critical}
            />
            <StatTile
              title="Latency p95"
              value={fmtNumber(latest("p95"), 2)}
              unit="ms"
              data={data}
              dataKey="p95"
              color={SERIES_COLORS[1]}
            />
            <StatTile
              title="Latency p50"
              value={fmtNumber(latest("p50"), 2)}
              unit="ms"
              data={data}
              dataKey="p50"
              color={SERIES_COLORS[2]}
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <TimeSeriesPanel
              title="Latency p50 / p95 / p99"
              info={`${hostLabel} · milliseconds`}
              data={data}
              unit="ms"
              series={[
                { key: "p50", label: "p50", color: SERIES_COLORS[0] },
                { key: "p95", label: "p95", color: SERIES_COLORS[1] },
                { key: "p99", label: "p99", color: SERIES_COLORS[2] },
              ]}
            />
            {/* Status classes ARE states, so they wear the reserved status
                palette — and carry labels, never colour alone. */}
            <TimeSeriesPanel
              title="Responses by status code"
              info={`${hostLabel} · requests per second`}
              data={data}
              unit="req/s"
              series={[
                { key: "status200", label: "200 OK", color: STATUS.good },
                { key: "status401", label: "401 Unauthorized", color: STATUS.warning },
                { key: "status5xx", label: "5xx Server error", color: STATUS.critical },
              ]}
            />
          </div>
        </section>
      )}

      {groups.includes("system") && (
        <section className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Gauge title="CPU busy %" pct={cpuBusy} info={hostLabel} />
            <Gauge title="Memory used %" pct={memPct} info={hostLabel} />
            <Gauge title="Root disk used %" pct={latest("diskUsedPct")} info={hostLabel} />
            <Panel title="Uptime" info={hostLabel} className="h-[192px]">
              <div className="flex h-full flex-col items-center justify-center">
                <span className="text-[38px] font-semibold leading-none" style={{ color: PANEL.primary }}>
                  {fmtNumber(uptime, 1)}
                  <span className="ml-1.5 text-[16px] font-normal" style={{ color: PANEL.secondary }}>
                    days
                  </span>
                </span>
                <span className="mt-2 text-[10px]" style={{ color: PANEL.muted }}>
                  since {new Date(BOOTED_AT).toLocaleDateString()}
                </span>
              </div>
            </Panel>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <TimeSeriesPanel
              title="CPU usage by mode"
              info={`${hostLabel} · percent of total`}
              data={data}
              unit="%"
              series={[
                { key: "cpuUser", label: "user", color: SERIES_COLORS[0] },
                { key: "cpuSystem", label: "system", color: SERIES_COLORS[1] },
                { key: "cpuIowait", label: "iowait", color: SERIES_COLORS[2] },
                { key: "cpuSoftirq", label: "softirq", color: SERIES_COLORS[3] },
                { key: "cpuSteal", label: "steal", color: SERIES_COLORS[4] },
              ]}
            />
            <TimeSeriesPanel
              title="Memory"
              info={`${hostLabel} · GiB`}
              data={data}
              unit="GiB"
              series={[
                { key: "memTotal", label: "total", color: SERIES_COLORS[0] },
                { key: "memUsed", label: "used", color: SERIES_COLORS[3] },
              ]}
              format={(v) => fmtNumber(v, 0)}
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <TimeSeriesPanel
              title="Network traffic"
              info={`${hostLabel} · receive and transmit`}
              data={data}
              unit="KB/s"
              series={[
                { key: "netRx", label: "rx", color: SERIES_COLORS[2] },
                { key: "netTx", label: "tx", color: SERIES_COLORS[3] },
              ]}
            />
            <TimeSeriesPanel
              title="Disk I/O"
              info={`${hostLabel} · read and write`}
              data={data}
              unit="MB/s"
              series={[
                { key: "diskRead", label: "read", color: SERIES_COLORS[2] },
                { key: "diskWrite", label: "write", color: SERIES_COLORS[3] },
              ]}
            />
          </div>
        </section>
      )}

      {groups.includes("postgres") && (
        <section className="space-y-3">
          <p className="text-[10px] uppercase tracking-[0.14em]" style={{ color: PANEL.muted }}>
            Postgres connections
          </p>
          <div className="grid gap-3 lg:grid-cols-3">
            {/* Empty by fact, not by failure: postgres_exporter publishes the
                active count on this deployment and not the rest, so inventing
                a maximum or a per-database split would misreport it. */}
            <Panel title="Max connections" info="postgres_exporter not scraped" className="h-[176px]">
              <NoData height={120} />
            </Panel>
            <StatTile
              title="Active connections"
              value={fmtNumber(latest("pgActive"), 0)}
              data={data}
              dataKey="pgActive"
              color={SERIES_COLORS[0]}
            />
            <Panel title="Connections by database" info="postgres_exporter not scraped" className="h-[176px]">
              <NoData height={120} />
            </Panel>
          </div>
          <TimeSeriesPanel
            title="Connections over time"
            info={`${hostLabel} · active sessions`}
            data={data}
            area
            series={[{ key: "pgActive", label: "active", color: SERIES_COLORS[0] }]}
            format={(v) => fmtNumber(v, 0)}
          />
        </section>
      )}

      <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
        Simulated data. Live Grafana is embedded on the deployed host, where the SPA and Grafana
        share one origin; from here it refuses to be framed cross-origin, so these panels stand in
        for it and are generated entirely in the browser.
      </p>
    </div>
  );
}

// --- Toolbar -----------------------------------------------------------------

function Toolbar({
  rangeId,
  onRange,
  refreshId,
  onRefreshId,
  onRefreshNow,
  paused,
}: {
  rangeId: string;
  onRange: (id: string) => void;
  refreshId: string;
  onRefreshId: (id: string) => void;
  onRefreshNow: () => void;
  paused: boolean;
}) {
  const control =
    "h-8 rounded-md border bg-transparent px-2 text-[12px] outline-none focus:ring-1";

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <label className="sr-only" htmlFor="mon-range">
        Time range
      </label>
      <select
        id="mon-range"
        value={rangeId}
        onChange={(e) => onRange(e.target.value)}
        className={control}
        style={{ borderColor: PANEL.border, color: PANEL.secondary, background: PANEL.surface }}
      >
        {TIME_RANGES.map((r) => (
          <option key={r.id} value={r.id}>
            {r.label}
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={onRefreshNow}
        className={`${control} inline-flex items-center gap-1.5`}
        style={{ borderColor: PANEL.border, color: PANEL.secondary, background: PANEL.surface }}
      >
        <RefreshCw className="h-3.5 w-3.5" />
        Refresh
      </button>

      <label className="sr-only" htmlFor="mon-refresh">
        Refresh interval
      </label>
      <select
        id="mon-refresh"
        value={refreshId}
        onChange={(e) => onRefreshId(e.target.value)}
        className={control}
        style={{ borderColor: PANEL.border, color: PANEL.secondary, background: PANEL.surface }}
      >
        {REFRESH_OPTIONS.map((r) => (
          <option key={r.id} value={r.id}>
            {r.label}
          </option>
        ))}
      </select>

      <span
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px]"
        style={{ color: paused ? PANEL.muted : STATUS_INK.good }}
      >
        {paused ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
        {paused ? "Paused" : "Live"}
      </span>
    </div>
  );
}
