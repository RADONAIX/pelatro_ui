// ---------------------------------------------------------------------------
// Simulated infrastructure metrics for System Monitoring.
//
// Grafana is embedded elsewhere in this product, but the live integration is
// unavailable — the deployed Grafana refuses to be framed cross-origin — so this
// module stands in for it. Everything here is generated in the browser: no
// Prometheus, no Grafana API, no backend call. That is deliberate, and it is why
// the page labels itself simulated rather than passing these off as readings.
//
// The shapes are the ones the real dashboards plot (RADONAIX — System and
// RADONAIX — API), so the panels can be swapped onto live data later without
// the layout changing.
// ---------------------------------------------------------------------------

/** One sample. `t` is epoch ms; every other key is a plotted series. */
export interface Sample {
  t: number;
  [series: string]: number;
}

export interface SeriesSpec {
  key: string;
  label: string;
  /** Where the walk sits when nothing is happening. */
  base: number;
  /** Step size per tick, as a fraction of `base`. */
  drift: number;
  min: number;
  max: number;
  /** Chance per tick of a burst, and how many times `base` it reaches. */
  spike?: { chance: number; magnitude: number; decay: number };
}

// --- The walk ---------------------------------------------------------------
// A mean-reverting random walk rather than plain noise: a metric that wandered
// freely would drift off its axis within a minute, and one that was pure noise
// around a constant would never look like a system under changing load. Pulling
// gently back toward `base` gives the slow swells a real host shows.

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

interface WalkState {
  value: number;
  /** Remaining multiplier from an active spike; decays toward 1. */
  burst: number;
}

function step(spec: SeriesSpec, state: WalkState): WalkState {
  const noise = (Math.random() - 0.5) * 2 * spec.drift * spec.base;
  // Reversion is weak (6%) so the series still wanders visibly.
  const pull = (spec.base - state.value) * 0.06;
  let burst = state.burst > 1 ? 1 + (state.burst - 1) * spec.spike!.decay : 1;
  if (spec.spike && burst <= 1.02 && Math.random() < spec.spike.chance) {
    burst = spec.spike.magnitude;
  }
  const next = clamp((state.value + noise + pull) * burst, spec.min, spec.max);
  return { value: next, burst };
}

/**
 * A live window over a set of series.
 *
 * Fixed point count rather than fixed interval: six hours at a 2.5s tick would
 * be 8,640 points to render, and no screen resolves them. The window keeps
 * `points` samples whatever the range, and the range only changes what each
 * sample's timestamp says — which is what a real dashboard does when it rolls a
 * long range up into display resolution.
 */
export class MetricStream {
  private state = new Map<string, WalkState>();
  private samples: Sample[] = [];

  constructor(
    private readonly specs: SeriesSpec[],
    private readonly points: number,
    rangeMs: number,
  ) {
    for (const spec of this.specs) {
      this.state.set(spec.key, { value: spec.base, burst: 1 });
    }
    // Seed a full window so the panels open with history rather than building
    // up from one point over the next several minutes.
    const now = Date.now();
    const spacing = rangeMs / points;
    for (let i = points - 1; i >= 0; i -= 1) {
      this.samples.push(this.advance(now - i * spacing));
    }
  }

  private advance(t: number): Sample {
    const sample: Sample = { t };
    for (const spec of this.specs) {
      const next = step(spec, this.state.get(spec.key)!);
      this.state.set(spec.key, next);
      sample[spec.key] = next.value;
    }
    return sample;
  }

  /** Append one sample and drop the oldest. Returns the new window. */
  tick(): Sample[] {
    this.samples = [...this.samples.slice(1), this.advance(Date.now())];
    return this.samples;
  }

  current(): Sample[] {
    return this.samples;
  }

  /** The most recent value of one series — what the stat tiles and gauges read. */
  latest(key: string): number {
    return this.samples[this.samples.length - 1]?.[key] ?? 0;
  }
}

// --- Series definitions ------------------------------------------------------
// Grouped the way the panels are, so a panel and its data are declared together.

/** node_exporter-style host metrics. Every monitored box has these. */
export const SYSTEM_SERIES: SeriesSpec[] = [
  { key: "cpuUser", label: "user", base: 1.1, drift: 0.09, min: 0.1, max: 60, spike: { chance: 0.02, magnitude: 4.5, decay: 0.55 } },
  { key: "cpuSystem", label: "system", base: 0.45, drift: 0.1, min: 0.05, max: 40 },
  { key: "cpuIowait", label: "iowait", base: 0.12, drift: 0.2, min: 0, max: 30, spike: { chance: 0.015, magnitude: 6, decay: 0.5 } },
  { key: "cpuSoftirq", label: "softirq", base: 0.09, drift: 0.15, min: 0, max: 10 },
  { key: "cpuSteal", label: "steal", base: 0.03, drift: 0.3, min: 0, max: 8 },
  { key: "memTotal", label: "total", base: 64, drift: 0, min: 64, max: 64 },
  { key: "memUsed", label: "used", base: 16.9, drift: 0.012, min: 8, max: 58 },
  { key: "diskUsedPct", label: "root disk used", base: 58.7, drift: 0.004, min: 40, max: 92 },
  { key: "netRx", label: "rx", base: 42, drift: 0.35, min: 0, max: 4000, spike: { chance: 0.03, magnitude: 9, decay: 0.6 } },
  { key: "netTx", label: "tx", base: 31, drift: 0.35, min: 0, max: 4000, spike: { chance: 0.03, magnitude: 7, decay: 0.6 } },
  { key: "diskRead", label: "read", base: 0.18, drift: 0.5, min: 0, max: 60, spike: { chance: 0.04, magnitude: 8, decay: 0.62 } },
  { key: "diskWrite", label: "write", base: 2.1, drift: 0.3, min: 0, max: 60, spike: { chance: 0.05, magnitude: 3.2, decay: 0.7 } },
];

/** The API dashboard's series — request rate, errors, latency, status codes. */
export const API_SERIES: SeriesSpec[] = [
  { key: "reqRate", label: "req/s", base: 34, drift: 0.14, min: 2, max: 400, spike: { chance: 0.035, magnitude: 2.4, decay: 0.6 } },
  { key: "errRate", label: "5xx req/s", base: 0.02, drift: 0.8, min: 0, max: 12, spike: { chance: 0.012, magnitude: 25, decay: 0.45 } },
  { key: "p50", label: "p50", base: 6.3, drift: 0.12, min: 1, max: 900, spike: { chance: 0.02, magnitude: 3, decay: 0.6 } },
  { key: "p95", label: "p95", base: 9.8, drift: 0.16, min: 2, max: 1200, spike: { chance: 0.025, magnitude: 12, decay: 0.55 } },
  // Kept within an order of magnitude of p50/p95 so all three are readable on
  // ONE axis. A p99 that idles 25x above p50 flattens the other two into the
  // baseline — and the fix for that is never a second y-scale.
  { key: "p99", label: "p99", base: 24, drift: 0.12, min: 4, max: 2000, spike: { chance: 0.03, magnitude: 7, decay: 0.62 } },
  { key: "status200", label: "200", base: 31, drift: 0.15, min: 1, max: 400, spike: { chance: 0.035, magnitude: 2.3, decay: 0.6 } },
  { key: "status401", label: "401", base: 2.4, drift: 0.4, min: 0, max: 90, spike: { chance: 0.03, magnitude: 4, decay: 0.6 } },
  { key: "status5xx", label: "5xx", base: 0.02, drift: 0.9, min: 0, max: 20, spike: { chance: 0.012, magnitude: 22, decay: 0.45 } },
];

/**
 * Postgres connection metrics.
 *
 * Only `pgActive` carries values. Max connections, the per-database breakdown
 * and the connection history are deliberately empty: those panels read "No
 * data" on the real dashboards too, because postgres_exporter is not scraped on
 * every box. Inventing numbers for them would misrepresent the deployment.
 */
export const POSTGRES_SERIES: SeriesSpec[] = [
  { key: "pgActive", label: "active", base: 18, drift: 0.14, min: 2, max: 120, spike: { chance: 0.02, magnitude: 3, decay: 0.6 } },
];

// --- Time ranges & refresh ---------------------------------------------------

export interface TimeRange {
  id: string;
  label: string;
  ms: number;
}

export const TIME_RANGES: TimeRange[] = [
  { id: "15m", label: "Last 15 minutes", ms: 15 * 60_000 },
  { id: "1h", label: "Last 1 hour", ms: 60 * 60_000 },
  { id: "6h", label: "Last 6 hours", ms: 6 * 60 * 60_000 },
  { id: "24h", label: "Last 24 hours", ms: 24 * 60 * 60_000 },
];

export interface RefreshOption {
  id: string;
  label: string;
  /** null = paused; the manual Refresh button still works. */
  ms: number | null;
}

export const REFRESH_OPTIONS: RefreshOption[] = [
  { id: "off", label: "Off", ms: null },
  { id: "2s", label: "2s", ms: 2_000 },
  { id: "3s", label: "3s", ms: 3_000 },
  { id: "10s", label: "10s", ms: 10_000 },
  { id: "30s", label: "30s", ms: 30_000 },
];

/** Points held per window — enough to read a trend, few enough to stay smooth. */
export const WINDOW_POINTS = 90;

// --- Formatting --------------------------------------------------------------

export const fmtNumber = (v: number, digits = 2) =>
  v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmtTime = (t: number) =>
  new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/** Uptime is a counter, not a walk — it only ever goes up. */
export function uptimeDays(startedAt: number): number {
  return (Date.now() - startedAt) / 86_400_000;
}
