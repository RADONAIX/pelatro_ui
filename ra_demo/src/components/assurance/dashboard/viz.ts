// ---------------------------------------------------------------------------
// The dashboard's visual constants — one place, so every chart on the executive
// dashboard reads as one system.
//
// Colours are the validated categorical palette (light steps + their dark-mode
// counterparts, chosen for the dark surface rather than flipped). Series colour
// follows the ENTITY, never its rank: slot 1 is always "healthy", slot 2 always
// "exceptions", slot 3 always "leakage", in every assurance.
// ---------------------------------------------------------------------------

/** CSS custom properties defined in styles.css under `.ra-viz`. */
export const VIZ = {
  healthy: "var(--viz-healthy)",
  exceptions: "var(--viz-exceptions)",
  leakage: "var(--viz-leakage)",
  risk: "var(--viz-risk)",
  grid: "var(--viz-grid)",
  axis: "var(--viz-axis)",
  surface: "var(--viz-surface)",
} as const;

/** Donut / bar categorical order — fixed, never cycled. */
export const CATEGORICAL = [
  "var(--viz-c1)",
  "var(--viz-c2)",
  "var(--viz-c3)",
  "var(--viz-c4)",
  "var(--viz-c5)",
  "var(--viz-c6)",
] as const;

export function categorical(index: number): string {
  return CATEGORICAL[index] ?? "var(--viz-muted)";
}

/**
 * Lifts a tooltip above the chart's own overlays.
 *
 * Recharts positions the tooltip absolutely with no z-index, so any later
 * positioned sibling — the donut's centre total, for one — paints on top of it
 * and the two sets of numbers read as one. Goes on `wrapperStyle`, not
 * `contentStyle`: the wrapper is the positioned element.
 */
export const TOOLTIP_WRAPPER: React.CSSProperties = { zIndex: 20 };

export const TOOLTIP_STYLE: React.CSSProperties = {
  background: "var(--color-popover)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxShadow: "0 8px 24px -12px rgb(0 0 0 / 0.25)",
  fontSize: 12,
  padding: "8px 10px",
};

export const AXIS_TICK = {
  fill: "var(--viz-axis)",
  fontSize: 11,
} as const;

export function compact(n: number): string {
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return `${n}`;
}
