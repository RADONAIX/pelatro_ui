// ---------------------------------------------------------------------------
// Shared display conventions for the Rating Assurance module, so they cannot
// drift screen by screen:
//   dates    -> "30 Jul 2026"  (ISO stays in exports and APIs only)
//   money    -> "£21.44"       (always with the symbol; a bare 21.44 in a
//                               variance column reads as a count, not cash)
//   statuses -> one tone map    (green matched, red failed/overcharge, amber
//                               pending, blue investigating, grey inactive)
// Used only by rating screens — the shared design system is untouched.
// ---------------------------------------------------------------------------

const dateFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function fmtDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(d.getTime()) ? "—" : dateFmt.format(d);
}

export function fmtDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(d.getTime()) ? "—" : dateTimeFmt.format(d);
}

const symbolCache = new Map<string, Intl.NumberFormat>();

export function money(
  value: number | null | undefined,
  currency: string | null = "GBP",
): string {
  if (value === null || value === undefined) return "—";
  const code = currency || "GBP";
  let fmt = symbolCache.get(code);
  if (!fmt) {
    try {
      fmt = new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: code,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    } catch {
      // An unknown code from a source system must not crash a dashboard.
      fmt = new Intl.NumberFormat("en-GB", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
    }
    symbolCache.set(code, fmt);
  }
  return fmt.format(value);
}

export function fmtCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-GB");
}

export function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(2)}%`;
}

/**
 * One tone per status family. Keys are matched by inclusion so
 * COMPLETED_WITH_WARNINGS picks up the warning tone without its own entry.
 */
const TONES: [string[], string][] = [
  [
    [
      "MATCHED",
      "ZERO_CHARGE",
      "COMPLETED",
      "ACTIVE",
      "RESOLVED",
      "CLOSED",
      "SUCCEEDED",
    ],
    "bg-success/10 text-success border-success/20",
  ],
  [
    ["OVERCHARGED", "FAILED", "CRITICAL", "ERROR"],
    "bg-destructive/10 text-destructive border-destructive/20",
  ],
  [
    ["UNDERCHARGED", "UNRATED", "PENDING", "WARNING", "NEW", "QUEUED", "HIGH"],
    "bg-warning/15 text-warning-foreground border-warning/30",
  ],
  [
    ["INVESTIGATING", "RUNNING", "ASSIGNED", "REPROCESSED", "IN_PROGRESS"],
    "bg-info/10 text-info border-info/20",
  ],
];

const NEUTRAL_TONE = "bg-muted text-muted-foreground border-border";

export function statusTone(status: string | null | undefined): string {
  if (!status) return NEUTRAL_TONE;
  const upper = status.toUpperCase();
  for (const [keys, cls] of TONES) {
    if (keys.some((k) => upper.includes(k))) return cls;
  }
  return NEUTRAL_TONE;
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/_/g, " ").toLowerCase();
}
