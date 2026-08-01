// ---------------------------------------------------------------------------
// The panel vocabulary System Monitoring is built from: a dark card, a stat
// tile, a gauge, and a time-series plot.
//
// Every panel is the same shell so the grid reads as one dashboard, and every
// plot shares the same chrome — recessive grid, tabular ticks, 2px lines, a
// crosshair tooltip. The differences between panels are the data, not the
// styling.
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS_TICK, PANEL, TOOLTIP_STYLE, gaugeColor } from "./viz";
import { fmtNumber, fmtTime, type Sample } from "@/lib/monitoring/metrics";

// --- Shell -------------------------------------------------------------------

export function Panel({
  title,
  info,
  className = "",
  children,
}: {
  title: string;
  /** Shown under the title — the one-line "what am I looking at". */
  info?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`flex flex-col rounded-lg border p-3 ${className}`}
      style={{ background: PANEL.surface, borderColor: PANEL.border }}
    >
      <div className="mb-2 min-w-0">
        <h3 className="truncate text-[13px] font-medium" style={{ color: PANEL.primary }}>
          {title}
        </h3>
        {info && (
          <p className="truncate text-[10px]" style={{ color: PANEL.muted }}>
            {info}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/** What a panel shows when its exporter isn't scraped. Not an error — a fact. */
export function NoData({ height = 120 }: { height?: number }) {
  return (
    <div
      className="flex items-center justify-center text-[13px]"
      style={{ height, color: PANEL.muted }}
    >
      No data
    </div>
  );
}

// --- Stat tile ---------------------------------------------------------------

/**
 * A single headline number over its own recent history.
 *
 * The sparkline is behind the value rather than beside it: the number is the
 * answer and the shape is context, which is the arrangement Grafana's stat
 * panel uses and the one operators here already read.
 */
export function StatTile({
  title,
  value,
  unit,
  data,
  dataKey,
  color,
}: {
  title: string;
  value: string;
  unit?: string;
  data: Sample[];
  dataKey: string;
  color: string;
}) {
  return (
    <Panel title={title} className="h-[132px]">
      <div className="relative h-full">
        <div className="absolute inset-x-0 bottom-0 h-[64px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`stat-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={color} stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey={dataKey}
                stroke={color}
                strokeWidth={2}
                fill={`url(#stat-${dataKey})`}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {/* The number wears text ink, not the series colour. A headline in
            2.1:1 yellow is decorative rather than legible; the sparkline behind
            it already carries the identity that colour is for. */}
        <div className="relative flex h-full items-center justify-center">
          <span
            className="text-[30px] font-semibold leading-none"
            style={{ color: PANEL.primary }}
          >
            {value}
            {unit && (
              <span className="ml-1 text-[15px] font-normal" style={{ color: PANEL.secondary }}>
                {unit}
              </span>
            )}
          </span>
        </div>
      </div>
    </Panel>
  );
}

// --- Gauge -------------------------------------------------------------------

/**
 * A percentage as a 270° arc.
 *
 * Drawn rather than charted: one value on a fixed 0–100 scale has no axis worth
 * rendering, and the arc's fill IS the reading. Colour comes from the threshold
 * (see gaugeColor), so severity is legible before the number is.
 */
export function Gauge({
  title,
  pct,
  info,
}: {
  title: string;
  pct: number;
  info?: string;
}) {
  const clamped = Math.min(100, Math.max(0, pct));
  const color = gaugeColor(clamped);
  // 270° sweep starting at the lower-left, the orientation Grafana's gauge uses.
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const sweep = 0.75; // three quarters of the circle
  const track = circumference * sweep;
  const filled = track * (clamped / 100);

  return (
    <Panel title={title} info={info} className="h-[192px]">
      <div className="flex h-full items-center justify-center">
        <svg viewBox="0 0 140 140" className="h-[128px] w-[128px]" role="img" aria-label={`${title} ${clamped.toFixed(1)} percent`}>
          <g transform="rotate(135 70 70)">
            <circle
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={PANEL.axis}
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={`${track} ${circumference}`}
            />
            <circle
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={color}
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={`${filled} ${circumference}`}
              style={{ transition: "stroke-dasharray 700ms ease-out, stroke 400ms linear" }}
            />
          </g>
          <text
            x="70"
            y="76"
            textAnchor="middle"
            fill={PANEL.primary}
            fontSize="24"
            fontWeight="600"
          >
            {fmtNumber(clamped, 1)}
            <tspan fontSize="13" fontWeight="400" fill={PANEL.secondary}>
              %
            </tspan>
          </text>
        </svg>
      </div>
    </Panel>
  );
}

// --- Time series -------------------------------------------------------------

export interface PlotSeries {
  key: string;
  label: string;
  color: string;
}

/**
 * The workhorse panel: one plot, several series, one y-axis.
 *
 * One axis is deliberate. Two measures of different magnitude get two panels —
 * a second y-scale makes any two lines cross wherever the author chose the
 * scales, which is a picture of the axis rather than of the data.
 */
export function TimeSeriesPanel({
  title,
  info,
  data,
  series,
  unit,
  height = 176,
  area = false,
  format,
}: {
  title: string;
  info?: string;
  data: Sample[];
  series: PlotSeries[];
  unit?: string;
  height?: number;
  /** Filled area suits a single volume series; lines suit several compared. */
  area?: boolean;
  format?: (v: number) => string;
}) {
  // Decimals are chosen ONCE from the window's magnitude, not per tick. Deriving
  // them per value gives an axis reading 5.00 / 10 / 20 — three different
  // precisions on one scale, which reads as three different measurements.
  const peak = data.reduce(
    (hi, row) => series.reduce((m, s) => Math.max(m, Number(row[s.key]) || 0), hi),
    0,
  );
  const decimals = peak >= 100 ? 0 : peak >= 10 ? 1 : 2;
  const fmt = format ?? ((v: number) => fmtNumber(v, decimals));
  const Chart = area ? AreaChart : LineChart;

  return (
    <Panel title={title} info={info}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <Chart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              {series.map((s) => (
                <linearGradient key={s.key} id={`ts-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid stroke={PANEL.grid} vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={fmtTime}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: PANEL.axis }}
              minTickGap={44}
            />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(v: number) => `${fmt(v)}${unit ? ` ${unit}` : ""}`}
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={{ color: PANEL.secondary, fontSize: 10 }}
              // The crosshair: a chart like this is read by hovering, so the
              // pointer gets a visible line, not just a floating box.
              cursor={{ stroke: PANEL.secondary, strokeWidth: 1, strokeDasharray: "3 3" }}
              labelFormatter={(t) => fmtTime(Number(t))}
              formatter={(v: number, name: string) => [
                `${fmt(v)}${unit ? ` ${unit}` : ""}`,
                name,
              ]}
              isAnimationActive={false}
            />
            {series.map((s) =>
              area ? (
                <Area
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.label}
                  stroke={s.color}
                  strokeWidth={2}
                  fill={`url(#ts-${s.key})`}
                  dot={false}
                  isAnimationActive={false}
                />
              ) : (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.label}
                  stroke={s.color}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              ),
            )}
          </Chart>
        </ResponsiveContainer>
      </div>
      {/* A legend whenever more than one series is plotted — identity must never
          rest on colour alone. One series is named by the panel title. */}
      {series.length > 1 && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {series.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5 text-[10px]" style={{ color: PANEL.secondary }}>
              <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
    </Panel>
  );
}
