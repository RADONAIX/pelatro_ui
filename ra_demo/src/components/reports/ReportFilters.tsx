import { Search, X, SlidersHorizontal, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { useT } from "@/lib/i18n";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { Select } from "@/components/ui-kit/Select";
import { DateRangePicker } from "@/components/ui-kit/DateRangePicker";

// The filter's date bounds are stored as "YYYY-MM-DD HH:mm" so the range can be
// constrained by time as well as date.
const p2 = (n: number) => String(n).padStart(2, "0");
const dtStr = (d: Date) =>
  `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
const parseDt = (s: string): Date | null => {
  const ts = toLocalTs(s);
  return isNaN(ts) ? null : new Date(ts);
};
// Parse a date/datetime cell OR a filter bound into a local-time epoch. The
// common backend shape ("YYYY-MM-DD[ T]HH:mm[:ss][.ffffff][Z]") is handled
// explicitly; anything else (ISO with timezone, "2025/05/10", "May 10 2025", …)
// falls back to the engine's parser — so a differently-formatted date column
// never silently excludes every row. Returns NaN only for truly unparseable /
// empty values.
function toLocalTs(s: string): number {
  const str = String(s).trim();
  if (!str || str.toLowerCase() === "none") return NaN;
  const m = str.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0)).getTime();
  const native = Date.parse(str);
  return isNaN(native) ? NaN : native;
}

// True when a value carries a time-of-day (HH:mm) — i.e. NOT a date-only cell.
const hasTimeOfDay = (s: string) => /\d{1,2}:\d{2}/.test(String(s));
// Midnight epoch for a value's calendar day. Used when the target column is
// date-only, so the picker's time-of-day is ignored and the whole day counts as
// in range (otherwise a 00:00 cell falls outside a same-day 13:00–14:00 range).
function toLocalDay(s: string): number {
  const ts = toLocalTs(s);
  if (isNaN(ts)) return NaN;
  const d = new Date(ts);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

// ---------------------------------------------------------------------------
// Report filtering — generic over the backend's dynamic {columns, rows} shape.
// Column kinds are auto-detected so each report gets filters that suit its own
// table: date/time columns drive a time-range filter, low-cardinality columns
// become dropdowns, everything else is covered by the global text search.
// All filtering is client-side over the loaded rows (server caps at 100).
// ---------------------------------------------------------------------------

export type ColumnKind = "date" | "category" | "text";

export interface ColumnMeta {
  index: number;
  name: string;
  kind: ColumnKind;
  options: string[]; // distinct values (for category columns)
}

export interface FilterState {
  search: string;
  values: Record<number, string[]>; // category column index -> selected values ([] / absent = all)
  dateCol: number | null; // which date column the range applies to
  from: string; // yyyy-mm-dd
  to: string; // yyyy-mm-dd
}

export const emptyFilterState: FilterState = { search: "", values: {}, dateCol: null, from: "", to: "" };

const DATE_NAME = /(date|time)/i;
const DATE_VALUE = /^\d{4}-\d{2}-\d{2}/;
const NUMERIC = /^-?\d[\d,]*(\.\d+)?$/;
// Node identifiers aren't a useful filter — exclude them from auto-detection.
const NODE_LIKE = /node_?id/i;
// Tag columns that should ALWAYS be filters with their full domain, even when
// the loaded data only contains one of the values.
const FIXED_OPTIONS: Record<string, string[]> = {
  source: ["AIR", "SDP"],
  stream: ["Raw", "Processed"],
};

// A column only becomes a filter if it's a genuine low-cardinality enum.
const MAX_CATEGORY_OPTIONS = 15; // too many distinct values → not a useful filter
const MAX_CATEGORY_LEN = 40; // long values (filenames, free text, ids) → not a filter

function cell(v: unknown): string {
  return v == null ? "" : String(v);
}

/** Inspect the data to classify each column. */
export function detectColumns(columns: string[], rows: unknown[][]): ColumnMeta[] {
  const metas = columns.map((name, index) => {
    const lname = name.toLowerCase();
    const raw = rows.map((r) => cell(r[index])).filter((v) => v !== "" && v.toLowerCase() !== "none");
    const distinct = Array.from(new Set(raw));

    // Always-on filters (source / stream) with their full domain + anything else
    // actually present in the data.
    if (lname in FIXED_OPTIONS) {
      const options = Array.from(new Set([...FIXED_OPTIONS[lname], ...distinct]));
      return { index, name, kind: "category" as ColumnKind, options };
    }

    const looksDate =
      DATE_NAME.test(name) ||
      (raw.length > 0 && raw.filter((v) => DATE_VALUE.test(v)).length >= raw.length * 0.6);

    const numericShare = raw.length ? raw.filter((v) => NUMERIC.test(v)).length / raw.length : 0;
    const maxLen = distinct.reduce((m, v) => Math.max(m, v.length), 0);

    let kind: ColumnKind = "text";
    if (looksDate && raw.some((v) => DATE_VALUE.test(v))) {
      kind = "date";
    } else if (
      !NODE_LIKE.test(name) && // node_id / file_node_id aren't useful filters
      distinct.length >= 2 &&
      distinct.length <= MAX_CATEGORY_OPTIONS &&
      numericShare < 0.6 && // numbers (counts, sequences, amounts, ids) aren't categories
      maxLen <= MAX_CATEGORY_LEN // filenames / long free text aren't categories
    ) {
      kind = "category";
    }

    return { index, name, kind, options: distinct.sort() };
  });

  // Keep a SINGLE date filter (no confusing column picker). When a report has
  // more than one date column, the range applies to the primary one and the rest
  // are demoted to plain (searchable) columns. Preference mirrors the backend's
  // per-report `date_column`: the canonical processing/business date, not a
  // secondary origin timestamp (Record Sequence Check ships record_date plus two
  // *_origin_timestamp columns; File Exception ships batch_date + file_date).
  const PRIMARY_DATE = ["batch_date", "file_date", "record_date", "start_time"];
  const dateMetas = metas.filter((m) => m.kind === "date");
  if (dateMetas.length > 1) {
    const primary =
      PRIMARY_DATE.map((n) => dateMetas.find((m) => m.name.toLowerCase() === n)).find(Boolean) ?? dateMetas[0];
    for (const m of dateMetas) {
      if (m !== primary) { m.kind = "text"; m.options = []; }
    }
  }

  return metas;
}

export const dateColumns = (metas: ColumnMeta[]) => metas.filter((m) => m.kind === "date");
export const categoryColumns = (metas: ColumnMeta[]) => metas.filter((m) => m.kind === "category");

export function hasActiveFilters(s: FilterState): boolean {
  return Boolean(s.search.trim() || s.from || s.to || Object.values(s.values).some((v) => v && v.length));
}

/** Compact chip list summarizing the active filters (for the Download Center). */
export function summarizeFilters(state: FilterState, metas: ColumnMeta[]): string[] {
  const chips: string[] = [];
  for (const m of metas) {
    if (m.kind === "category") {
      const vals = state.values[m.index];
      if (vals && vals.length) chips.push(...vals);
    }
  }
  if (state.from || state.to) chips.push([state.from, state.to].filter(Boolean).join(" → "));
  if (state.search.trim()) chips.push(`"${state.search.trim()}"`);
  return chips.length ? chips : ["All"];
}

/** Apply the active filters to the rows. */
export function applyFilters(rows: unknown[][], state: FilterState): unknown[][] {
  const q = state.search.trim().toLowerCase();
  return rows.filter((row) => {
    if (q && !row.some((v) => cell(v).toLowerCase().includes(q))) return false;

    // Multi-select: an empty selection means "all"; otherwise the cell must
    // match one of the selected values.
    for (const [idx, vals] of Object.entries(state.values)) {
      if (vals && vals.length && !vals.includes(cell(row[Number(idx)]))) return false;
    }

    if (state.dateCol != null && (state.from || state.to)) {
      const raw = cell(row[state.dateCol]);
      const ts = toLocalTs(raw);
      if (isNaN(ts)) return false; // unparseable date excluded when range active
      if (hasTimeOfDay(raw)) {
        // Datetime cell → precise date + time comparison.
        if (state.from) { const f = toLocalTs(state.from); if (!isNaN(f) && ts < f) return false; }
        if (state.to) { const tt = toLocalTs(state.to); if (!isNaN(tt) && ts > tt) return false; }
      } else {
        // Date-only cell → compare whole days; the bound's time-of-day is ignored.
        const cd = toLocalDay(raw);
        if (state.from) { const fd = toLocalDay(state.from); if (!isNaN(fd) && cd < fd) return false; }
        if (state.to) { const td = toLocalDay(state.to); if (!isNaN(td) && cd > td) return false; }
      }
    }
    return true;
  });
}

const inputCls =
  "h-9 shrink-0 rounded-lg border border-border bg-card text-xs px-2.5 focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition";

export function ReportFilters({
  metas,
  state,
  onChange,
  shown,
  total,
}: {
  metas: ColumnMeta[];
  state: FilterState;
  onChange: (next: FilterState) => void;
  shown: number;
  total: number;
}) {
  const t = useT();
  const dateCols = dateColumns(metas);
  const catCols = categoryColumns(metas);
  const active = hasActiveFilters(state);
  const set = (patch: Partial<FilterState>) => onChange({ ...state, ...patch });
  const activeDateCol = state.dateCol ?? dateCols[0]?.index ?? null;

  if (dateCols.length === 0 && catCols.length === 0) {
    // Nothing categorical/temporal to filter on — keep just the search box.
  }

  // Short description shown via the (i) icon next to each filter header.
  const colInfo = (name: string): string => {
    const n = name.toLowerCase();
    if (n === "source") return t("Filter by data source — AIR or SDP.");
    if (n === "stream") return t("Filter by stream — Raw or Processed.");
    if (n === "status" || n.endsWith("_status")) return t("Filter rows by their status.");
    return `${t("Filter rows by")} ${t(name)}.`;
  };
  const hdrCls = "flex items-center gap-1 mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground";

  return (
    // All filters on a single row (no wrapping).
    <div className="px-5 py-3 border-b border-border bg-muted/20 flex flex-nowrap items-end gap-3">
      <div className="inline-flex items-center gap-1.5 self-center shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mr-1">
        <SlidersHorizontal className="h-3.5 w-3.5" /> {t("Filters")}
        <InfoHint text={t("Narrow the rows below by text search, by a column's value, or by a date range. Filters apply to the rows currently loaded.")} />
      </div>

      {/* Global search */}
      <div className="shrink-0">
        <div className={hdrCls}>
          {t("Search")}
          <InfoHint text={t("Search across every column in the rows currently loaded.")} />
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            value={state.search}
            onChange={(e) => set({ search: e.target.value })}
            placeholder={t("Search…")}
            maxLength={100}
            className={`${inputCls} pl-8 ${state.search ? "pr-8" : ""} w-40`}
          />
          {state.search && (
            <button
              type="button"
              onClick={() => set({ search: "" })}
              aria-label={t("Clear search")}
              title={t("Clear search")}
              className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Multi-select filter per low-cardinality column (same control as the Pipelines filters). */}
      {catCols.map((c) => (
        <div key={c.index} className="shrink-0">
          <MultiSelect<string>
            label={t(c.name)}
            info={colInfo(c.name)}
            options={c.options}
            selected={new Set(state.values[c.index] ?? [])}
            onChange={(next) => set({ values: { ...state.values, [c.index]: Array.from(next) } })}
            placeholder={`${t("All")} ${t(c.name)}`}
            minWidth={130}
            allowEmpty
          />
        </div>
      ))}

      {/* Time range */}
      {dateCols.length > 0 && (
        <div className="shrink-0">
          <div className={hdrCls}>
            {t("Date range")}
            <InfoHint text={t("Show only rows whose date falls within the selected range.")} />
          </div>
          <DateRangePicker
            showTime
            placeholder={t("All dates")}
            start={parseDt(state.from)}
            end={parseDt(state.to)}
            max={new Date()}
            onChange={(r) => set({ from: dtStr(r.start), to: dtStr(r.end), dateCol: activeDateCol })}
            className="h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border bg-background text-sm hover:bg-muted transition"
          />
        </div>
      )}

      {/* Clear */}
      <button
        onClick={() => onChange({ ...emptyFilterState, dateCol: dateCols[0]?.index ?? null })}
        disabled={!active}
        className="ml-auto shrink-0 inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-border bg-card text-xs font-medium hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
      >
        <X className="h-3.5 w-3.5" /> {t("Clear filters")}
      </button>
    </div>
  );
}

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

export function TablePagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  onPageSize: (n: number) => void;
}) {
  const t = useT();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pageCount);
  const start = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const end = Math.min(current * pageSize, total);
  const btn =
    "h-8 w-8 inline-flex items-center justify-center rounded-lg border border-border bg-card hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed";

  return (
    <div className="px-5 py-3 border-t border-border bg-muted/20 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{t("Rows per page")}</span>
        <Select
          value={String(pageSize)}
          onChange={(v) => onPageSize(Number(v))}
          size="sm"
          dropUp
          minWidth={72}
          ariaLabel={t("Rows per page")}
          options={PAGE_SIZE_OPTIONS.map((n) => ({ value: String(n), label: String(n) }))}
        />
      </div>

      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground">
          {start.toLocaleString()}–{end.toLocaleString()} {t("of")} {total.toLocaleString()}
        </span>
        <div className="flex items-center gap-1">
          <button className={btn} onClick={() => onPage(1)} disabled={current <= 1} aria-label={t("First page")}>
            <ChevronsLeft className="h-4 w-4" />
          </button>
          <button className={btn} onClick={() => onPage(current - 1)} disabled={current <= 1} aria-label={t("Previous page")}>
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="px-2 text-xs text-foreground font-medium tabular-nums">
            {current} / {pageCount}
          </span>
          <button className={btn} onClick={() => onPage(current + 1)} disabled={current >= pageCount} aria-label={t("Next page")}>
            <ChevronRight className="h-4 w-4" />
          </button>
          <button className={btn} onClick={() => onPage(pageCount)} disabled={current >= pageCount} aria-label={t("Last page")}>
            <ChevronsRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
