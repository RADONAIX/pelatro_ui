// ---------------------------------------------------------------------------
// Chart tokens for System Monitoring.
//
// Light panels, matching the rest of the application shell rather than the dark
// Grafana look these replaced. Every colour here is therefore the LIGHT step of
// its ramp, chosen for and validated against a light surface — a dark-surface
// step dropped onto white is a different colour to the eye and fails contrast
// independently of how it looked before.
//
// Validated against surface #fcfcfb:
//   categorical 1–5  → all checks PASS (worst adjacent CVD ΔE 9.1, normal 19.6)
//                      with a documented contrast WARN on aqua (2.74), yellow
//                      (2.11) and magenta (2.62). The relief that makes those
//                      legal is the labelled legend every multi-series panel
//                      carries: identity never rests on the colour alone.
//   status trio      → good 3.27, critical 4.68; warning is 1.79 on light BY
//                      DESIGN, which is why status series are always legended
//                      and the gauges always print their number.
// ---------------------------------------------------------------------------

/** Panel chrome, matched to the app's light card. */
export const PANEL = {
  surface: "#fcfcfb",
  border: "rgba(11,11,11,0.10)",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  primary: "#0b0b0b",
  secondary: "#52514e",
  muted: "#898781",
} as const;

/**
 * Categorical slots, in fixed order. A series takes the next unused slot and
 * keeps it — the order is the colourblind-safety mechanism, not decoration, so
 * it is never cycled or reassigned when a chart's series count changes.
 */
export const SERIES_COLORS = [
  "#2a78d6", // 1 blue
  "#eb6834", // 2 orange
  "#1baf7a", // 3 aqua
  "#eda100", // 4 yellow
  "#e87ba4", // 5 magenta
] as const;

/**
 * Status colours, reserved. Used only where the data IS a state — HTTP classes
 * and gauge thresholds — and never as a sixth series colour.
 */
export const STATUS = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
} as const;

/**
 * Ink for a status VALUE rendered as text. The status hues are tuned to be
 * distinguishable as marks, not readable as type: `warning` is 1.79:1 on this
 * surface. A large number printed in it would be unreadable, so text takes a
 * darker step of the same hue while the mark beside it keeps the status colour.
 */
export const STATUS_INK = {
  good: "#0a7a0a",
  warning: "#8a5c00",
  serious: "#a2502a",
  critical: "#b32d2d",
} as const;

/** Axis tick styling, shared so every panel's chrome recedes identically. */
export const AXIS_TICK = {
  fill: PANEL.muted,
  fontSize: 10,
  fontVariantNumeric: "tabular-nums",
} as const;

export const TOOLTIP_STYLE = {
  background: "#ffffff",
  border: `1px solid ${PANEL.border}`,
  borderRadius: 8,
  fontSize: 11,
  color: PANEL.primary,
  padding: "6px 10px",
  boxShadow: "0 8px 24px rgba(11,11,11,0.12)",
} as const;

/** A gauge's colour is its severity, so it reads without the number. */
export function gaugeColor(pct: number): string {
  if (pct >= 90) return STATUS.critical;
  if (pct >= 75) return STATUS.warning;
  return STATUS.good;
}
