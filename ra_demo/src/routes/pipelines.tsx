import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { pipelineService } from "@/services";
import {
  Download, Activity, Timer, AlertTriangle, Gauge, ChevronRight, ChevronLeft,
  Eye, Cpu, Database, Wand2, ChevronDown, Clock, Layers, RefreshCw,
  Info, CheckCircle2, XCircle, X, RotateCcw,
} from "lucide-react";
import { type BatchLog } from "@/lib/airBatches";
import { ExportDialog } from "@/components/pipelines/ExportDialog";
import { useBatches } from "@/lib/pipelinesApi";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { DateRangePicker } from "@/components/ui-kit/DateRangePicker";
import { LiveBadge } from "@/components/ui-kit/LiveBadge";


export const Route = createFileRoute("/pipelines")({
  component: PipelinesPage,
});

// Absolute date range the dashboard is scoped to.
type DateRange = { start: Date; end: Date };
const HOUR_MS = 3_600_000;

const DAY_MS = 24 * HOUR_MS;
const WEEK_MS = 7 * DAY_MS;

// Adaptive bucket ladder — pick the finest bucket that keeps the bar count
// reasonable (≤ ~90), snapped to natural clock boundaries:
//   ≤ 3 days                → 1 hour  (24–72 bars)  · "Batches per hour"
//   3–14 days               → 4 hours (18–84 bars)  · "Batches per 4 hours"
//   14 days – ~3 months     → 1 day   (14–90 bars)  · "Batches per day"
//   > ~3 months             → 1 week  (13+  bars)   · "Batches per week"
function pickBucket(durationMs: number): { ms: number; subtitle: string } {
  const days = durationMs / DAY_MS;
  if (days <= 3) return { ms: HOUR_MS, subtitle: "Batches per hour" };
  if (days <= 14) return { ms: 4 * HOUR_MS, subtitle: "Batches per 4 hours" };
  if (days <= 92) return { ms: DAY_MS, subtitle: "Batches per day" }; // ~3 months
  return { ms: WEEK_MS, subtitle: "Batches per week" };
}

// Floor a timestamp to the start of its bucket on a natural clock boundary:
// hour buckets snap to the hour (4h → 00/04/08/…), day → local midnight, week →
// the Monday 00:00 that starts the week.
function bucketStart(ms: number, bucketMs: number): number {
  const d = new Date(ms);
  if (bucketMs >= WEEK_MS) {
    d.setHours(0, 0, 0, 0);
    const dow = (d.getDay() + 6) % 7; // Monday = 0
    d.setDate(d.getDate() - dow);
    return d.getTime();
  }
  if (bucketMs >= DAY_MS) { d.setHours(0, 0, 0, 0); return d.getTime(); }
  d.setMinutes(0, 0, 0);
  const stepH = Math.max(1, Math.round(bucketMs / HOUR_MS));
  d.setHours(Math.floor(d.getHours() / stepH) * stepH);
  return d.getTime();
}

// X-axis / tooltip label suited to the bucket width: date for day/week buckets,
// time for hour buckets (prefixed with the date when the window spans days).
function bucketLabel(ms: number, bucketMs: number, spansDays: boolean): string {
  const d = new Date(ms);
  if (bucketMs >= DAY_MS) return `${d.getDate()}/${d.getMonth() + 1}`;
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return spansDays ? `${d.getDate()}/${d.getMonth() + 1} ${time}` : time;
}

// Backend cap for GET /pipelines/batches?hours= (1..168). We fetch the last N
// hours from *now* to cover the range start, then filter to the exact range.
const MAX_FETCH_HOURS = 168;

// Short human label for a date range, e.g. "Jun 29, 14:00 – Jun 30, 14:00".
function fmtRange(start: Date, end: Date): string {
  const opt: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };
  return `${start.toLocaleString(undefined, opt)} – ${end.toLocaleString(undefined, opt)}`;
}

// How often the pipeline data auto-refreshes (also surfaced in the UI). Matches RAFMS_UI.
const REFRESH_MS = 120000;

// Bucket a batch's status using the DAG-level rule the team defined. A DAG
// (batch) processes many files through watcher → decoder → ingestion →
// normalization. A failure in some files does NOT fail the batch:
//  • In progress → the DAG is still running / queued (non-terminal).
//  • Failed      → 100% of the batch's files failed; the backend marks the
//                  whole batch FAILED.
//  • Completed   → the DAG finished, even if SOME files failed underneath
//                  (PARTIAL) or it found no files (NO_FILES). A partial file
//                  failure still counts as a completed batch.
// Only a genuinely unknown / blank status falls through to "other".
type StatusBucket = "completed" | "inProgress" | "failed" | "other";
function statusBucket(s: string | null | undefined): StatusBucket {
  switch (String(s ?? "").trim().toUpperCase()) {
    case "IN_PROGRESS":
    case "RUNNING":
    case "PENDING":
    case "QUEUED":
    case "STARTED":
      return "inProgress";
    case "FAILED":
      return "failed";
    case "SUCCESS":
    case "COMPLETED":
    case "PARTIAL":
    case "NO_FILES":
    case "SKIPPED":
      return "completed";
    default:
      return "other";
  }
}

function PipelinesPage() {
  const t = useT();
  const [exportOpen, setExportOpen] = useState(false);
  // Window is driven either by a quick preset (rolling "last N hours") or by an
  // explicit custom date range. A preset takes priority; picking dates clears it.
  const [presetHours, setPresetHours] = useState<number | null>(24);
  const [customRange, setCustomRange] = useState<DateRange | null>(null);
  const [kpis, setKpis] = useState<any>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Effective absolute range. A preset rolls with "now"; a custom range is fixed.
  const range: DateRange = presetHours != null
    ? { start: new Date(Date.now() - presetHours * HOUR_MS), end: new Date() }
    : customRange ?? { start: new Date(Date.now() - 24 * HOUR_MS), end: new Date() };

  const applyPreset = (hours: number) => { setPresetHours(hours); setCustomRange(null); };
  // Picking a custom date switches off the active preset.
  const onRangeChange = (next: DateRange) => { setPresetHours(null); setCustomRange(next); };

  // Fetch enough hours from now to cover the chosen start (capped at the backend
  // limit); the exact range is applied client-side below.
  const fetchHours = Math.min(MAX_FETCH_HOURS, Math.max(1, Math.ceil((Date.now() - range.start.getTime()) / HOUR_MS)));
  const { data: batches, loading, error, refetch } = useBatches(fetchHours);

  useEffect(() => {
    pipelineService.kpis().then(setKpis);
  }, []);

  // Stamp the last-updated time whenever fresh data arrives.
  useEffect(() => {
    if (batches) setLastUpdated(new Date());
  }, [batches]);

  // Auto-refresh on a fixed cadence (matches RAFMS_UI).
  useEffect(() => {
    const interval = setInterval(() => {
      refetch();
    }, REFRESH_MS);
    return () => clearInterval(interval);
  }, [refetch]);

  // Keep only batches whose start falls inside the selected absolute range.
  const rows: BatchLog[] = (batches ?? []).filter((b) => {
    const ts = parseTs(b.batch_start_time);
    return !isNaN(ts) && ts >= range.start.getTime() && ts <= range.end.getTime();
  });

  return (
    <AppShell>
      <PageHeader
        title={t("Pipelines & Job Monitor")}
        info={t("Monitor processing pipelines, batch status and job health in real time.")}
        // description="Internal processing DAG — File Collection → Decoding → Validation → Reconciliation → Report Generation. Live status, SLA tracking and operational signals."
        actions={
          <div className="flex items-center gap-3">
            <LiveBadge intervalSec={REFRESH_MS / 1000} lastUpdated={lastUpdated} />
            <Tooltip label={t("Reload pipeline data")} side="bottom">
              <button
                onClick={refetch}
                disabled={loading}
                className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-sm hover:bg-muted transition disabled:opacity-60"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> {t("Refresh")}
              </button>
            </Tooltip>
            <div className="inline-flex items-center rounded-lg border border-border bg-card p-0.5">
              {[6, 12, 24].map((h) => (
                <Tooltip key={h} label={`${t("Show data for the last")} ${h}h`} side="bottom">
                  <button
                    onClick={() => applyPreset(h)}
                    className={`h-8 px-3 text-xs font-semibold rounded-md transition ${presetHours === h ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                      }`}
                  >
                    {h}h
                  </button>
                </Tooltip>
              ))}
            </div>
            <Tooltip label={t("Export pipeline data")} side="bottom">
              <button onClick={() => setExportOpen(true)} className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-sm hover:bg-muted transition">
                <Download className="h-4 w-4" /> {t("Export")}
              </button>
            </Tooltip>
          </div>
        }
      />

      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg border border-destructive/30 bg-destructive/10 text-destructive text-sm flex items-center justify-between gap-3">
          <span>{t("Failed to load pipeline data:")} {error}</span>
          <Tooltip label={t("Retry loading the data")} side="bottom">
            <button onClick={refetch} className="h-7 px-3 rounded-md border border-destructive/40 text-xs font-medium hover:bg-destructive/15">
              {t("Retry")}
            </button>
          </Tooltip>
        </div>
      )}
      {loading && !batches && (
        <div className="mb-4 px-4 py-3 rounded-lg border border-border bg-muted/40 text-muted-foreground text-sm">
          {t("Loading pipeline data…")}
        </div>
      )}

      <PipelineMap range={range} onRangeChange={onRangeChange} onResetRange={() => applyPreset(24)} rangeIsDefault={presetHours === 24} kpis={kpis} batches={rows} hourly={presetHours == null} />
      {/* Export Batch Data is fully independent of the dashboard's window — it
          fetches and filters its own data (see ExportDialog). */}
      {exportOpen && <ExportDialog onClose={() => setExportOpen(false)} />}
    </AppShell>
  );
}

function PipelineMap({ range, onRangeChange, onResetRange, rangeIsDefault, kpis: _kpis, batches: liveBatches, hourly }: { range: DateRange; onRangeChange: (next: DateRange) => void; onResetRange: () => void; rangeIsDefault: boolean; kpis: any; batches: BatchLog[]; hourly: boolean }) {
  const DAGS = ["AIR", "MSC", "SDP"] as const;
  const SUBS = ["Raw", "Processed", "Reconciled"] as const;
  type Dag = typeof DAGS[number];
  type Sub = typeof SUBS[number];

  const t = useT();
  const [selDags, setSelDags] = useState<Set<Dag>>(new Set(["AIR", "MSC", "SDP"]));
  const [selSubs, setSelSubs] = useState<Set<Sub>>(new Set(["Raw", "Processed", "Reconciled"]));

  // Filters are at their defaults when all DAGs/Streams are selected and the
  // window is the default preset. "Clear filters" restores exactly that state.
  const filtersActive = selDags.size !== DAGS.length || selSubs.size !== SUBS.length || !rangeIsDefault;
  const clearFilters = () => {
    setSelDags(new Set(DAGS));
    setSelSubs(new Set(SUBS));
    onResetRange();
  };

  // Build datasets dynamically from liveBatches by grouping on DAG/Stream from batch_id
  const datasets: { dag: Dag; sub: Sub; rows: BatchLog[] }[] = (() => {
    const grouped = new Map<string, BatchLog[]>();

    for (const batch of liveBatches) {
      const { dag: rawDag, stream: rawStream } = parseBatchType(batch.batch_id);

      // Normalize case: RAW → Raw, PROCESSED → Processed, RECONCILED → Reconciled
      const normalizedStream =
        rawStream.toUpperCase() === "RAW" ? "Raw"
          : rawStream.toUpperCase() === "PROCESSED" ? "Processed"
            : rawStream.toUpperCase() === "RECONCILED" ? "Reconciled"
              : null;

      // Only include if DAG and Stream are recognized
      if (!DAGS.includes(rawDag as Dag) || !normalizedStream || !SUBS.includes(normalizedStream as Sub)) {
        continue;
      }

      const key = `${rawDag}|${normalizedStream}`;
      if (!grouped.has(key)) {
        grouped.set(key, []);
      }
      grouped.get(key)!.push(batch);
    }

    // Convert map to datasets array
    const result: { dag: Dag; sub: Sub; rows: BatchLog[] }[] = [];
    for (const [key, rows] of grouped.entries()) {
      const [dag, sub] = key.split("|");
      result.push({ dag: dag as Dag, sub: sub as Sub, rows });
    }
    return result;
  })();

  const activeDatasets = datasets.filter((d) => selDags.has(d.dag) && selSubs.has(d.sub));
  const allBatches = activeDatasets.flatMap((d) => d.rows);
  const filteredBatches = allBatches;
  const flows = activeDatasets.flatMap((d) => d.rows.map((b) => batchToFlow(b, d.dag, d.sub, t)));

  return (
    <div className="space-y-6">
      {/* Filter bar */}
      <div className="bg-card border border-border rounded-xl p-4 shadow-sm flex flex-wrap items-end gap-4">
        <MultiSelect<Dag> label={t("DAG")} info={t("Select one or more source pipelines to filter the displayed batches.")} options={["AIR", "MSC", "SDP"]} selected={selDags} onChange={setSelDags} minWidth={200} allowEmpty />
        <MultiSelect<Sub> label={t("Stream")} info={t("Select one or more processing stages to filter the displayed batches")} options={["Raw", "Processed", "Reconciled"]} selected={selSubs} onChange={setSelSubs} minWidth={220} allowEmpty />
        <div className="ml-auto flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("Date Range")}
            <InfoHint align="right" text={t("Show batches whose start time falls within this window. Overrides the quick presets; the To must be after the From and no later than now.")} />
          </div>
          {/* Custom absolute date range (overrides the quick presets above).
              className matches the DAG/Stream selects (bg-background, text-sm). */}
          <DateRangePicker
            start={range.start}
            end={range.end}
            max={new Date()}
            onChange={onRangeChange}
            className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-background text-sm text-foreground hover:bg-muted transition"
          />
        </div>
        <Tooltip label={filtersActive ? t("Reset DAG, Stream and date range to defaults") : t("No filters to clear")} side="bottom">
          <button
            onClick={clearFilters}
            disabled={!filtersActive}
            className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-background text-sm text-foreground hover:bg-muted transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RotateCcw className="h-4 w-4" /> {t("Clear filters")}
          </button>
        </Tooltip>
      </div>

      {/* High-level status summary + upstream data-quality observations */}
      <StatusSummary batches={filteredBatches} />

      {/* Prefect-style dashboard: Flow Runs / Task Runs / Events */}
      <BatchDashboard batches={filteredBatches} range={range} hourly={hourly} />


      {/* Flow cards parallel to issues list */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4 items-start">
        <div className="xl:col-span-3 bg-card border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between bg-muted/20">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#F8C800] opacity-70" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#F8C800]" />
              </span>
              <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                {t("Active Work Pools · Running")}
                <InfoHint text={t("Batches currently in progress (still running end-to-end). Each card shows the DAG/stream, batch ID and how long it has been running.")} />
              </div>
            </div>
            <span className="text-xs text-muted-foreground">
              {flows.filter((f) => f.overallStatus === "In Progress").length} {t("active")}
            </span>
          </div>
          <div className="p-4 space-y-3">
            {flows.filter((f) => f.overallStatus === "In Progress").length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {t("No pipelines currently running.")}
              </div>
            ) : (
              flows
                .filter((f) => f.overallStatus === "In Progress")
                .map((f) => <FlowCard key={f.id} flow={f} />)
            )}
          </div>
        </div>
        <div className="xl:col-span-2">
          <IssuesPanel batches={filteredBatches} />
        </div>
      </div>
    </div>
  );
}

// How many issue cards to show per page in the Failed / partial / crashed panel.
const ISSUES_PAGE_SIZE = 5;

// The three categories the issues list can be filtered by, used as clickable
// legends in the panel header. A batch that fits none of these (e.g. a SUCCESS
// batch surfaced only because of underlying file failures) has category null.
type IssueCat = "failed" | "partial" | "noFiles";
const ALL_CATS: IssueCat[] = ["failed", "partial", "noFiles"];
function issueCategory(b: BatchLog): IssueCat | null {
  const s = String(b.batch_status ?? "").toUpperCase();
  if (s === "FAILED") return "failed";
  if (s === "PARTIAL") return "partial";
  if (s === "NO_FILES" || (b.total_files ?? 0) === 0) return "noFiles";
  return null;
}

function IssuesPanel({ batches }: { batches: BatchLog[] }) {
  const t = useT();
  // Clickable legend filter. All categories start selected (everything shown);
  // deselecting a legend hides that category. Multiple can be active.
  const [selected, setSelected] = useState<Set<IssueCat>>(new Set(ALL_CATS));
  const [page, setPage] = useState(1);
  const allIssues = batches
    // Never surface in-progress batches here — only settled failures/partials.
    .filter((b) => b.batch_status !== "IN_PROGRESS")
    .filter(
      (b) => b.batch_status !== "SUCCESS"
        || b.decode_failed_count > 0 || b.load_failed_count > 0
        || b.zero_kb_file_count > 0 || b.duplicate_file_count > 0 || b.corrupt_file_count > 0,
    )
    // Latest batch first, so the most recent issues land on page 1.
    .sort((a, b) => new Date(b.batch_start_time).getTime() - new Date(a.batch_start_time).getTime());
  // Counts are always over the full issue set so the legends show totals, not
  // the currently-filtered subset.
  const failedCount = allIssues.filter((b) => issueCategory(b) === "failed").length;
  const partialCount = allIssues.filter((b) => issueCategory(b) === "partial").length;
  const noFilesCount = allIssues.filter((b) => issueCategory(b) === "noFiles").length;
  // Show a batch if its category is selected. Uncategorised issues (category
  // null — e.g. SUCCESS batches with underlying file failures) always show.
  const issues = allIssues.filter((b) => { const c = issueCategory(b); return c === null || selected.has(c); });
  const allSelected = ALL_CATS.every((c) => selected.has(c));

  const toggle = (c: IssueCat) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c); else next.add(c);
      return next;
    });
    setPage(1);
  };

  // Legend definitions. Selected pills carry their status colour (tinted fill,
  // coloured text, full opacity); unselected pills are greyed and dimmed so
  // they clearly read as inactive.
  const legends: { key: IssueCat; label: string; count: number; dot: string; active: string }[] = [
    { key: "failed", label: t("failed"), count: failedCount, dot: "bg-destructive", active: "bg-destructive/10 text-destructive border-destructive/40" },
    { key: "partial", label: t("partial"), count: partialCount, dot: "bg-[#F97316]", active: "bg-[#F97316]/10 text-[#F97316] border-[#F97316]/40" },
    { key: "noFiles", label: t("no files"), count: noFilesCount, dot: "bg-muted-foreground", active: "bg-muted-foreground/15 text-foreground border-muted-foreground/40" },
  ];

  // Paginate the cards, 5 per page. Clamp the page so it stays valid as the
  // underlying list changes (filter toggle, 2s auto-refresh).
  const totalPages = Math.max(1, Math.ceil(issues.length / ISSUES_PAGE_SIZE));
  const curPage = Math.min(page, totalPages);
  const pageStart = (curPage - 1) * ISSUES_PAGE_SIZE;
  const pageIssues = issues.slice(pageStart, pageStart + ISSUES_PAGE_SIZE);
  return (
    <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden xl:sticky xl:top-20">
      <div className="px-4 py-3 border-b border-border bg-muted/20 space-y-2.5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
            <div className="flex items-center gap-1.5 min-w-0 text-sm font-semibold text-foreground">
              <span className="truncate">{t("Failed / partial / crashed")}</span>
              <InfoHint className="shrink-0" align="right" text={t("Batches needing attention — failed, partially processed, or with no files. Expand a row to see the reason and file-level details.")} />
            </div>
          </div>
          <span className="text-[11px] text-muted-foreground whitespace-nowrap shrink-0">
            {issues.length} {t("of")} {batches.length}
          </span>
        </div>
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {legends.map((lg) => {
              const on = selected.has(lg.key);
              return (
                <button
                  key={lg.key}
                  type="button"
                  onClick={() => toggle(lg.key)}
                  aria-pressed={on}
                  title={on ? t("Click to hide this status") : t("Click to show this status")}
                  className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[11px] whitespace-nowrap transition ${on
                      ? `${lg.active} font-semibold`
                      : "bg-card text-muted-foreground/50 border-border opacity-50 font-medium hover:opacity-100 hover:text-foreground hover:border-muted-foreground/40"
                    }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${on ? lg.dot : "bg-muted-foreground/40"}`} />
                  {lg.count} {lg.label}
                </button>
              );
            })}
          </div>
          {!allSelected && (
            <Tooltip label={t("Reset the status filter")} side="bottom">
              <button
                type="button"
                onClick={() => { setSelected(new Set(ALL_CATS)); setPage(1); }}
                className="shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-border bg-card text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition"
              >
                <RotateCcw className="h-3 w-3" /> {t("Clear")}
              </button>
            </Tooltip>
          )}
        </div>
      </div>
      <div className="divide-y divide-border max-h-[820px] overflow-y-auto">
        {issues.length === 0 && (
          <div className="px-4 py-10 text-center text-sm text-muted-foreground">{t("No issues in this window.")}</div>
        )}
        {pageIssues.map((b) => (
          <BatchRow key={b.batch_id} batch={b} />
        ))}
      </div>
      {issues.length > 0 && (
        <div className="px-4 py-2.5 border-t border-border flex items-center justify-between gap-2 bg-muted/20">
          <span className="text-[11px] text-muted-foreground">
            {pageStart + 1}–{Math.min(pageStart + ISSUES_PAGE_SIZE, issues.length)} {t("of")} {issues.length}
          </span>
          <div className="flex items-center gap-1">
            <Tooltip label={t("Go to previous page")} side="bottom">
              <button
                onClick={() => setPage(curPage - 1)}
                disabled={curPage <= 1}
                aria-label={t("Go to previous page")}
                className="h-7 w-7 inline-flex items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </Tooltip>
            <span className="text-[11px] text-muted-foreground px-1">{t("Page")} {curPage} / {totalPages}</span>
            <Tooltip label={t("Go to next page")} side="bottom">
              <button
                onClick={() => setPage(curPage + 1)}
                disabled={curPage >= totalPages}
                aria-label={t("Go to next page")}
                className="h-7 w-7 inline-flex items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </Tooltip>
          </div>
        </div>
      )}
    </div>
  );
}



// Title-case a raw stream token from the batch id (RAW → Raw, PROCESSED → Processed).
function prettyStream(stream: string): string {
  if (!stream) return stream;
  return stream.charAt(0).toUpperCase() + stream.slice(1).toLowerCase();
}

// Parse the backend's "YYYY-MM-DD HH:MM:SS.ffffff" timestamps reliably across
// browsers. Safari/JavaScriptCore rejects both the space separator and the
// 6-digit (microsecond) fraction, so normalize to ISO + millisecond precision.
// No timezone in the string → interpreted as local time (same as before).
function parseTs(s: string): number {
  if (!s) return NaN;
  const iso = s.replace(" ", "T").replace(/(\.\d{3})\d+$/, "$1");
  const ms = new Date(iso).getTime();
  return isNaN(ms) ? new Date(s).getTime() : ms;
}

function parseBatchType(batchId: string): { dag: string; stream: string } {
  // Batch ID format: DAG_STREAM_YYYYMMDD_HHMMSS or similar
  // e.g., AIR_RAW_20240605_120000, SDP_PROCESSED_20240605_093045
  const parts = batchId.split("_");
  const dag = parts[0] || "UNKNOWN";
  const stream = parts[1] || "UNKNOWN";
  return { dag, stream };
}

// High-level status counts (Completed / In progress / Failed) plus a strip of
// upstream-source data-quality observations, derived from the batches in view.
function StatusSummary({ batches }: { batches: BatchLog[] }) {
  const t = useT();

  const completed = batches.filter((b) => statusBucket(b.batch_status) === "completed").length;
  const inProgress = batches.filter((b) => statusBucket(b.batch_status) === "inProgress").length;
  const failed = batches.filter((b) => statusBucket(b.batch_status) === "failed").length;

  // Upstream-source observations — counted as "how many batches were affected"
  // (zero-KB also surfaces the total file count, matching the other modules).
  const zeroKbBatches = batches.filter((b) => (b.zero_kb_file_count ?? 0) > 0).length;
  const zeroKbFiles = batches.reduce((acc, b) => acc + (b.zero_kb_file_count ?? 0), 0);
  const noFilesBatches = batches.filter((b) => String(b.batch_status ?? "").toUpperCase() === "NO_FILES" || (b.total_files ?? 0) === 0).length;
  const corruptBatches = batches.filter((b) => (b.corrupt_file_count ?? 0) > 0).length;
  const decodeErrorBatches = batches.filter((b) => (b.decode_failed_count ?? 0) > 0).length;
  const duplicateBatches = batches.filter((b) => (b.duplicate_file_count ?? 0) > 0).length;

  const cards = [
    { icon: CheckCircle2, label: t("Completed"), value: completed, hint: t("DAG ran end-to-end"), border: "border-l-success", text: "text-success" },
    { icon: RefreshCw, label: t("In progress"), value: inProgress, hint: t("Currently running"), border: "border-l-[#F8C800]", text: "text-[#F8C800]" },
    { icon: XCircle, label: t("Failed"), value: failed, hint: t("Needs investigation"), border: "border-l-[#EF4444]", text: "text-[#EF4444]" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <div key={c.label} className={`bg-card border border-border ${c.border} border-l-4 rounded-xl p-5 shadow-sm`}>
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium text-muted-foreground">{c.label}</div>
                <Icon className={`h-4 w-4 ${c.text}`} />
              </div>
              <div className={`mt-2 text-3xl font-semibold tracking-tight ${c.text}`}>{c.value.toLocaleString()}</div>
              <div className="mt-1 text-xs text-muted-foreground">{c.hint}</div>
            </div>
          );
        })}
      </div>

      <div className="bg-card border border-border rounded-xl p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground">
            {t("Source data quality observed across")} {batches.length.toLocaleString()} {t("batches")}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <span><span className="font-semibold text-foreground">{zeroKbBatches.toLocaleString()}</span> {t("batches with zero-KB files")} ({zeroKbFiles.toLocaleString()} {t("files total")})</span>
          <span><span className="font-semibold text-foreground">{noFilesBatches.toLocaleString()}</span> {t("No files")}</span>
          <span><span className="font-semibold text-foreground">{corruptBatches.toLocaleString()}</span> {t("with corrupt files")}</span>
          <span><span className="font-semibold text-foreground">{decodeErrorBatches.toLocaleString()}</span> {t("with decode errors")}</span>
          <span><span className="font-semibold text-foreground">{duplicateBatches.toLocaleString()}</span> {t("with duplicates")}</span>
        </div>
        <div className="mt-2 text-[11px] text-muted-foreground/80">
          {t("These are upstream-source observations, not pipeline failures.")}
        </div>
      </div>
    </div>
  );
}

function BatchDashboard({ batches, range, hourly }: { batches: BatchLog[]; range: DateRange; hourly: boolean }) {
  const t = useT();
  // Clicking a status segment in the Stream breakdown opens a drill-down modal.
  const [drill, setDrill] = useState<{ stream: string; status: StatusBucket } | null>(null);
  const total = batches.length;
  const completed = batches.filter((b) => statusBucket(b.batch_status) === "completed").length;
  const inProgress = batches.filter((b) => statusBucket(b.batch_status) === "inProgress").length;
  const failed = batches.filter((b) => statusBucket(b.batch_status) === "failed").length;
  // Batches with an unrecognised status (normally none — kept as a safety net).
  const other = total - completed - inProgress - failed;

  // Per-stream (DAG + Stream) batch counts, broken down by status, highest first —
  // drives the breakdown bars (bar length = total, segments = status composition).
  type StreamStat = { name: string; total: number; completed: number; inProgress: number; failed: number; other: number };
  const streamStats: StreamStat[] = Object.values(
    batches.reduce((acc, b) => {
      const { dag, stream } = parseBatchType(b.batch_id);
      const name = `${dag} ${prettyStream(stream)}`;
      const s = acc[name] ?? (acc[name] = { name, total: 0, completed: 0, inProgress: 0, failed: 0, other: 0 });
      s[statusBucket(b.batch_status)]++;
      s.total++;
      return acc;
    }, {} as Record<string, StreamStat>),
  ).sort((a, b) => b.total - a.total);
  const streamMax = streamStats.length ? streamStats[0].total : 1;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-foreground">{t("Batch Status")}</h2>
        </div>
      </div>


      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Batch volume over time */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl p-5 shadow-sm">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            {t("Batch volume over time")}
            <InfoHint text={t("Batches started in each time bucket over the selected window, stacked by status (completed / in progress / failed). Hover a bar for exact counts.")} />
          </div>
          <div className="text-[11px] text-muted-foreground mt-0.5">{`${t(pickBucket(range.end.getTime() - range.start.getTime()).subtitle)}${t(", segmented by status")}`}</div>

          {total === 0 ? (
            <div className="h-44 mt-4 flex items-center justify-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
              {t("No batch data for the selected DAG / Stream.")}
            </div>
          ) : (
            <BatchVolumeChart batches={batches} start={range.start.getTime()} end={range.end.getTime()} hourly={hourly} />
          )}

          <div className="flex flex-wrap items-center gap-5 mt-4 pt-4 border-t border-border text-xs">
            <StatusLegend color="bg-success" value={completed} label={t("Completed")} />
            <StatusLegend color="bg-[#F8C800]" value={inProgress} label={t("In progress")} />
            <StatusLegend color="bg-[#EF4444]" value={failed} label={t("Failed")} />
            {other > 0 && <StatusLegend color="bg-muted-foreground/40" value={other} label={t("Other")} />}
          </div>
        </div>

        {/* Stream breakdown */}
        <div className="lg:col-span-1 bg-card border border-border rounded-xl p-5 shadow-sm">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            {t("Stream breakdown")}
            <InfoHint align="right" text={t("Total batches per DAG stream (Raw / Processed) for the selected window. Each bar is split by status — click a colored segment to drill into those batches.")} />
          </div>
          <div className="text-3xl font-semibold text-foreground tracking-tight mt-2">{total.toLocaleString()}</div>
          <div className="text-[11px] text-muted-foreground">{t("total batches")}</div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 text-xs">
            <StatusLegend color="bg-success" value={completed} label={t("Completed")} />
            <StatusLegend color="bg-[#F8C800]" value={inProgress} label={t("In progress")} />
            <StatusLegend color="bg-[#EF4444]" value={failed} label={t("Failed")} />
            {other > 0 && <StatusLegend color="bg-muted-foreground/40" value={other} label={t("Other")} />}
          </div>

          <div className="mt-4 pt-4 border-t border-border space-y-3 max-h-[320px] overflow-y-auto pr-1">
            {streamStats.length === 0 ? (
              <div className="text-xs text-muted-foreground">{t("No batches in this window.")}</div>
            ) : (
              streamStats.map((s) => {
                const seg = (n: number) => (s.total ? (n / s.total) * 100 : 0);
                // One clickable, hover-highlighted segment per status. Opens the drill-down.
                const segments: { key: StatusBucket; value: number; color: string; label: string }[] = [
                  { key: "completed", value: s.completed, color: "bg-success", label: t("Completed") },
                  { key: "inProgress", value: s.inProgress, color: "bg-[#F8C800]", label: t("In progress") },
                  { key: "failed", value: s.failed, color: "bg-[#EF4444]", label: t("Failed") },
                  { key: "other", value: s.other, color: "bg-muted-foreground/40", label: t("Other") },
                ];
                return (
                  <div key={s.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-foreground">{s.name}</span>
                      <span className="text-sm font-semibold text-foreground tabular-nums">{s.total.toLocaleString()}</span>
                    </div>
                    {/* Track is full width; the colored bar takes a share proportional to
                        the busiest stream, and is itself segmented by status. Each
                        segment is clickable and opens a status drill-down. */}
                    <div className="h-3.5 bg-muted rounded-full overflow-hidden">
                      <div className="flex h-full rounded-full overflow-hidden" style={{ width: `${(s.total / streamMax) * 100}%` }}>
                        {/* Each segment keeps a minimum clickable width so tiny status
                        counts (e.g. a handful of failures) stay easy to hit. */}
                    {segments.filter((seg) => seg.value > 0).map((sg) => (
                          <button
                            key={sg.key}
                            type="button"
                            onClick={() => setDrill({ stream: s.name, status: sg.key })}
                            title={`${s.name} · ${sg.value} ${sg.label} — ${t("click to drill down")}`}
                            aria-label={`${s.name} ${sg.value} ${sg.label}`}
                            className={`${sg.color} h-full transition hover:brightness-110 hover:saturate-150 hover:ring-2 hover:ring-foreground/30 hover:ring-inset cursor-pointer first:rounded-l-full last:rounded-r-full`}
                            style={{ width: `${seg(sg.value)}%`, minWidth: "0.875rem" }}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {drill && (
        <StreamDrilldown
          stream={drill.stream}
          status={drill.status}
          batches={batches}
          range={range}
          hourly={hourly}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  );
}

// Drill-down modal: when a status segment of a stream is clicked, show that
// stream + status slice — summary KPIs, a volume-over-time chart for the
// selected window, and the matching batches (scrollable).
function StreamDrilldown({
  stream,
  status,
  batches,
  range,
  hourly,
  onClose,
}: {
  stream: string;
  status: StatusBucket;
  batches: BatchLog[];
  range: DateRange;
  hourly: boolean;
  onClose: () => void;
}) {
  const t = useT();

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const statusLabel =
    status === "completed" ? t("Completed")
      : status === "inProgress" ? t("In progress")
        : status === "failed" ? t("Failed")
          : t("Other");

  // Rows for this stream + status, newest first.
  const rows = batches
    .filter((b) => {
      const { dag, stream: st } = parseBatchType(b.batch_id);
      return `${dag} ${prettyStream(st)}` === stream && statusBucket(b.batch_status) === status;
    })
    .sort((a, b) => parseTs(b.batch_start_time) - parseTs(a.batch_start_time));

  const filesLoaded = rows.reduce((acc, b) => acc + (b.load_complete_count || 0), 0);
  const recordsLoaded = rows.reduce((acc, b) => acc + (b.total_aa_rows || 0) + (b.total_rr_rows || 0), 0);
  const avgDurSec = rows.length
    ? rows.reduce((acc, b) => acc + Math.max(0, (parseTs(b.batch_end_time) - parseTs(b.batch_start_time)) / 1000), 0) / rows.length
    : 0;
  const fmtDur = (sec: number) => `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;

  const cards = [
    { label: t("Batches"), value: rows.length.toLocaleString() },
    { label: t("Files loaded"), value: filesLoaded.toLocaleString() },
    { label: t("Records loaded"), value: recordsLoaded.toLocaleString() },
    { label: t("Avg duration"), value: rows.length ? fmtDur(avgDurSec) : "—" },
  ];

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-2xl shadow-xl w-full max-w-5xl max-h-[92vh] flex flex-col animate-scale-in"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-border">
          <div className="flex items-center gap-2 min-w-0">
            <button
              onClick={onClose}
              className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground shrink-0"
              aria-label={t("Back")}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm text-muted-foreground">{t("Batch Status")}</span>
            <span className="text-muted-foreground">/</span>
            <span className="text-sm font-semibold text-foreground">{t("Drill-down")}</span>
          </div>

          <Tooltip label={t("Close this dialog")} side="bottom">
          <button
            onClick={onClose}
            className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground"
            aria-label={t("Close")}
          >
            <X className="h-4 w-4" />
          </button>
        </Tooltip>
        </div>

        {/* Body (scrolls) */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {/* Filter chips */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-2 h-8 px-3 rounded-full border border-border bg-muted/40 text-xs font-medium text-foreground">
              {t("Status")}: {statusLabel}
            </span>
            <span className="inline-flex items-center gap-2 h-8 px-3 rounded-full border border-border bg-muted/40 text-xs font-medium text-foreground">
              {t("Source")}: {stream}
            </span>
          </div>

          {/* Volume over time */}
          <div className="bg-card border border-border rounded-xl p-5 shadow-sm">
            <div className="text-sm font-semibold text-foreground">{t("Volume over time")}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">{statusLabel} · {fmtRange(range.start, range.end)}</div>
            {rows.length === 0 ? (
              <div className="h-44 mt-4 flex items-center justify-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                {t("No batches in this window.")}
              </div>
            ) : (
              <BatchVolumeChart batches={rows} start={range.start.getTime()} end={range.end.getTime()} hourly={hourly} />
            )}
          </div>

          {/* Matching batches */}
          <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between bg-muted/20">
              <div className="text-sm font-semibold text-foreground">{t("Matching batches")}</div>
              <span className="text-xs text-muted-foreground">{rows.length} {t("batches")}</span>
            </div>
            <div className="divide-y divide-border max-h-[40vh] overflow-y-auto">
              {rows.length === 0 ? (
                <div className="px-4 py-10 text-center text-sm text-muted-foreground">{t("No batches in this window.")}</div>
              ) : (
                rows.map((b) => <BatchRow key={b.batch_id} batch={b} />)
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusLegend({ color, value, label }: { color: string; value: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
      <span className="font-semibold text-foreground tabular-nums">{value.toLocaleString()}</span>
      <span className="text-muted-foreground">{label}</span>
    </span>
  );
}

// Stacked vertical bars, one per clock hour, spanning the selected date range
// (each bar segmented into Completed / In progress / Failed).
function BatchVolumeChart({ batches, start, end }: { batches: BatchLog[]; start: number; end: number; hourly?: boolean }) {
  const t = useT();
  // Adaptive bucket ladder (1h → 4h → 1d → 1w) chosen from the window duration,
  // snapped to natural clock boundaries.
  const { ms: bucketMs } = pickBucket(end - start);
  const windowStartMs = bucketStart(start, bucketMs);
  const bucketCount = Math.max(1, Math.ceil((end - windowStartMs) / bucketMs));

  const buckets = Array.from({ length: bucketCount }, (_, idx) => ({
    startMs: windowStartMs + idx * bucketMs,
    completed: 0,
    inProgress: 0,
    failed: 0,
    other: 0,
    total: 0,
  }));
  for (const b of batches) {
    const ts = parseTs(b.batch_start_time);
    if (isNaN(ts)) continue;
    const idx = Math.floor((ts - windowStartMs) / bucketMs);
    if (idx < 0 || idx >= bucketCount) continue;
    const bucket = buckets[idx];
    const kind = statusBucket(b.batch_status);
    if (kind === "completed") bucket.completed++;
    else if (kind === "inProgress") bucket.inProgress++;
    else if (kind === "failed") bucket.failed++;
    else bucket.other++; // unrecognised status (normally none)
    bucket.total++;
  }
  // Drop trailing empty buckets so the chart ends at the latest batch rather
  // than trailing off into dead space (the window end usually leads the data,
  // e.g. "now" is ahead of the most recent batch).
  let lastFilled = buckets.length - 1;
  while (lastFilled > 0 && buckets[lastFilled].total === 0) lastFilled--;
  const shown = buckets.slice(0, lastFilled + 1);
  const maxTotal = Math.max(...shown.map((b) => b.total), 1);
  // Include the date in hour labels when the window spans more than a day, so
  // hours don't repeat ambiguously (e.g. "29/6 10:00").
  const spansDays = end - windowStartMs > 26 * HOUR_MS;
  const hourLabel = (ms: number) => bucketLabel(ms, bucketMs, spansDays);
  // A handful of evenly-spaced x-axis ticks (first..last) instead of one label
  // per bucket — keeps the axis readable and prevents overflow for wide ranges.
  const tickCount = Math.min(7, shown.length);
  const tickIdxs = Array.from(
    new Set(Array.from({ length: tickCount }, (_, i) => Math.round((i * (shown.length - 1)) / Math.max(1, tickCount - 1)))),
  );

  // Right-click context menu (Drill down) + the resulting quality breakdown
  // popup. Drill-down is only meaningful for the completed segment; other
  // segments still open the menu but with the action disabled.
  const [menu, setMenu] = useState<{ x: number; y: number; kind: StatusBucket } | null>(null);
  const [drillOpen, setDrillOpen] = useState(false);
  const openMenu = (e: React.MouseEvent, kind: StatusBucket) => {
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, kind });
  };

  return (
    <div className="mt-4">
      <div className="flex items-end gap-px h-55">
        {shown.map((bk, idx) => {
          const heightPct = (bk.total / maxTotal) * 100;
          const seg = (n: number) => (bk.total ? (n / bk.total) * 100 : 0);
          return (
            <div key={idx} className="group relative flex-1 min-w-0 flex flex-col justify-end h-full">
              <div
                className="w-full rounded-t-sm overflow-hidden flex flex-col-reverse transition hover:opacity-80 cursor-default"
                style={{ height: `${Math.max(heightPct, 2)}%` }}
              >
                {/* Faint baseline so an interior empty bucket reads as "zero", not a gap. */}
                {bk.total === 0 && <div className="bg-muted-foreground/15 w-full h-full" />}
                {bk.completed > 0 && <div className="bg-success w-full cursor-context-menu" style={{ height: `${seg(bk.completed)}%` }} onContextMenu={(e) => openMenu(e, "completed")} />}
                {bk.inProgress > 0 && <div className="bg-[#F8C800] w-full" style={{ height: `${seg(bk.inProgress)}%` }} onContextMenu={(e) => openMenu(e, "inProgress")} />}
                {bk.failed > 0 && <div className="bg-[#EF4444] w-full" style={{ height: `${seg(bk.failed)}%` }} onContextMenu={(e) => openMenu(e, "failed")} />}
                {bk.other > 0 && <div className="bg-muted-foreground/40 w-full" style={{ height: `${seg(bk.other)}%` }} onContextMenu={(e) => openMenu(e, "other")} />}
              </div>

              <div className="pointer-events-none absolute bottom-full mb-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-lg bg-foreground text-background text-[10px] px-3 py-2 opacity-0 group-hover:opacity-100 transition shadow-lg z-20">
                <div className="font-semibold mb-1.5">{hourLabel(bk.startMs)}</div>
                {bk.total === 0 ? (
                  <div className="text-background/70">{t("No batches")}</div>
                ) : (
                  <div className="space-y-1">
                    <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-success" /><span>{t("Completed:")} <span className="font-semibold">{bk.completed}</span></span></div>
                    <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#F8C800]" /><span>{t("In progress:")} <span className="font-semibold">{bk.inProgress}</span></span></div>
                    <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#EF4444]" /><span>{t("Failed:")} <span className="font-semibold">{bk.failed}</span></span></div>
                    {bk.other > 0 && <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-muted-foreground/40" /><span>{t("Other:")} <span className="font-semibold">{bk.other}</span></span></div>}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-between mt-2 text-[10px] text-muted-foreground">
        {tickIdxs.map((i) => (
          <span key={i}>{hourLabel(shown[i].startMs)}</span>
        ))}
      </div>

      {/* Right-click context menu (Superset-style: label left, info ⓘ right). */}
      {menu && (() => {
        const enabled = menu.kind === "completed";
        const left = Math.min(menu.x, window.innerWidth - 248);
        const top = Math.min(menu.y, window.innerHeight - 64);
        return (
          <>
            <div
              className="fixed inset-0 z-[95]"
              onClick={() => setMenu(null)}
              onContextMenu={(e) => { e.preventDefault(); setMenu(null); }}
            />
            <div
              className="fixed z-[96] min-w-[224px] rounded-lg border border-border bg-popover shadow-lg ring-1 ring-black/5 py-1 animate-scale-in"
              style={{ top, left }}
              role="menu"
            >
              <div className={`mx-1 flex items-center gap-1 rounded-md ${enabled ? "hover:bg-muted" : ""}`}>
                <button
                  role="menuitem"
                  disabled={!enabled}
                  onClick={() => { setDrillOpen(true); setMenu(null); }}
                  className="flex-1 text-left pl-3 pr-1 py-2 text-sm text-foreground whitespace-nowrap disabled:text-muted-foreground/50 disabled:cursor-not-allowed"
                >
                  {t("Drill down data quality")}
                </button>
                <span className="pr-2.5">
                  <InfoHint
                    align="right"
                    text={enabled
                      ? t("Break completed batches down by upstream data-quality category.")
                      : t("Only available for completed batches.")}
                  />
                </span>
              </div>
            </div>
          </>
        );
      })()}

      {/* Completed-batch data-quality breakdown popup — same buckets/x-axis as
          this chart, but each bar stacked by upstream quality category. */}
      {drillOpen && (
        <QualityBreakdownModal
          batches={batches}
          start={start}
          end={end}
          onClose={() => setDrillOpen(false)}
        />
      )}
    </div>
  );
}

// Upstream data-quality predicates for a (completed) batch. Categories overlap
// — a batch can hit several — except "Valid files", which is a completed batch
// with none of the issues.
const isNoFiles = (b: BatchLog) => String(b.batch_status ?? "").toUpperCase() === "NO_FILES" || (b.total_files ?? 0) === 0;
const isZeroKb = (b: BatchLog) => (b.zero_kb_file_count ?? 0) > 0;
const isCorrupt = (b: BatchLog) => (b.corrupt_file_count ?? 0) > 0;
const isDecodeErr = (b: BatchLog) => (b.decode_failed_count ?? 0) > 0;
const isDup = (b: BatchLog) => (b.duplicate_file_count ?? 0) > 0;
const isValidFiles = (b: BatchLog) => !isNoFiles(b) && !isZeroKb(b) && !isCorrupt(b) && !isDecodeErr(b) && !isDup(b);

// Order = stacking order (first sits at the bar's base). Colours double as the
// legend swatches — a soft, light palette (Tailwind ~300/400 tones) so the
// stacked bars read smoothly rather than as harsh primaries.
const QUALITY_CATS: { key: string; label: string; color: string; pred: (b: BatchLog) => boolean }[] = [
  { key: "valid", label: "Valid files", color: "#4ADE80", pred: isValidFiles },
  { key: "noFiles", label: "No files", color: "#CBD5E1", pred: isNoFiles },
  { key: "zeroKb", label: "Zero-KB files", color: "#FCD34D", pred: isZeroKb },
  { key: "corrupt", label: "Corrupt files", color: "#F87171", pred: isCorrupt },
  { key: "decode", label: "Decode errors", color: "#FDBA74", pred: isDecodeErr },
  { key: "duplicate", label: "Duplicates", color: "#C4B5FD", pred: isDup },
];

type QualityBucket = { startMs: number; counts: number[]; total: number };

// Bucket completed batches over the window (same time-bucketing as
// BatchVolumeChart) and tally each data-quality category per bucket. Shared by
// the chart and the CSV export so both stay in lockstep.
function buildQualityBuckets(batches: BatchLog[], start: number, end: number): { shown: QualityBucket[]; windowStartMs: number; bucketMs: number } {
  const { ms: bucketMs } = pickBucket(end - start);
  const windowStartMs = bucketStart(start, bucketMs);
  const bucketCount = Math.max(1, Math.ceil((end - windowStartMs) / bucketMs));
  const completed = batches.filter((b) => statusBucket(b.batch_status) === "completed");
  const buckets: QualityBucket[] = Array.from({ length: bucketCount }, (_, idx) => ({
    startMs: windowStartMs + idx * bucketMs,
    counts: QUALITY_CATS.map(() => 0),
    total: 0, // completed batches in this bucket (may be < sum of counts due to overlap)
  }));
  for (const b of completed) {
    const ts = parseTs(b.batch_start_time);
    if (isNaN(ts)) continue;
    const idx = Math.floor((ts - windowStartMs) / bucketMs);
    if (idx < 0 || idx >= bucketCount) continue;
    buckets[idx].total++;
    QUALITY_CATS.forEach((c, ci) => { if (c.pred(b)) buckets[idx].counts[ci]++; });
  }
  let lastFilled = buckets.length - 1;
  while (lastFilled > 0 && buckets[lastFilled].total === 0) lastFilled--;
  return { shown: buckets.slice(0, lastFilled + 1), windowStartMs, bucketMs };
}

// Round up to a "nice" number (1/2/5 × 10ⁿ) so the y-axis lands on clean ticks.
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return nice * pow;
}

// Every bar covers ONLY completed batches, stacked by data-quality category,
// with a y-axis + gridlines. Heights are relative to a nice axis max so bars
// line up with the gridlines.
function QualityVolumeChart({ batches, start, end, tall = false, hidden }: { batches: BatchLog[]; start: number; end: number; tall?: boolean; hidden?: Set<string> }) {
  const t = useT();
  const { shown, windowStartMs, bucketMs } = buildQualityBuckets(batches, start, end);

  // Visible categories only (legend can toggle series off, Superset-style).
  const visible = QUALITY_CATS.map((c, ci) => ({ c, ci })).filter(({ c }) => !hidden?.has(c.key));
  const stackOf = (bk: QualityBucket) => visible.reduce((a, { ci }) => a + bk.counts[ci], 0);
  const maxStack = Math.max(...shown.map(stackOf), 1);
  const step = niceCeil(maxStack / 5) || 1;
  const axisMax = step * 5;
  const yticks = Array.from({ length: 6 }, (_, i) => axisMax - i * step); // top → bottom

  const spansDays = end - windowStartMs > 26 * HOUR_MS;
  const hourLabel = (ms: number) => bucketLabel(ms, bucketMs, spansDays);
  const tickCount = Math.min(7, shown.length);
  const tickIdxs = Array.from(
    new Set(Array.from({ length: tickCount }, (_, i) => Math.round((i * (shown.length - 1)) / Math.max(1, tickCount - 1)))),
  );
  const plotH = tall ? "h-80" : "h-52";

  return (
    <div className="mt-4">
      <div className="flex">
        {/* Y-axis scale */}
        <div className={`flex flex-col justify-between ${plotH} w-9 shrink-0 pr-3 text-right text-xs tabular-nums text-muted-foreground/80`}>
          {yticks.map((v, i) => <span key={i} className="-translate-y-1/2 first:translate-y-0 last:translate-y-0 leading-none">{v}</span>)}
        </div>

        {/* Plot: dashed gridlines behind, bars in front */}
        <div className="relative flex-1 min-w-0">
          <div className="absolute inset-0 flex flex-col justify-between">
            {yticks.map((_, i) => (
              <div key={i} className={`border-t border-dashed ${i === yticks.length - 1 ? "border-border" : "border-border/50"}`} />
            ))}
          </div>
          <div className={`relative flex items-end gap-1.5 ${plotH}`}>
            {shown.map((bk, idx) => {
              const stack = stackOf(bk);
              const heightPct = (stack / axisMax) * 100;
              const seg = (n: number) => (stack ? (n / stack) * 100 : 0);
              return (
                <div key={idx} className="group relative flex-1 min-w-0 flex flex-col justify-end h-full">
                  <div
                    className="w-full rounded-t-lg overflow-hidden flex flex-col-reverse ring-1 ring-inset ring-black/5 transition group-hover:brightness-[0.97]"
                    style={{ height: `${stack === 0 ? 0 : Math.max(heightPct, 1)}%` }}
                  >
                    {visible.map(({ c, ci }) => bk.counts[ci] > 0 && (
                      <div key={c.key} className="w-full" style={{ height: `${seg(bk.counts[ci])}%`, backgroundColor: c.color }} />
                    ))}
                  </div>

                  <div className="pointer-events-none absolute top-1 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-lg bg-foreground text-background text-[10px] px-3 py-2 opacity-0 group-hover:opacity-100 transition shadow-lg z-30">
                    <div className="font-semibold mb-1.5">{hourLabel(bk.startMs)} · {bk.total} {t("completed")}</div>
                    {stack === 0 ? (
                      <div className="text-background/70">{t("No completed batches")}</div>
                    ) : (
                      <div className="space-y-1">
                        {visible.map(({ c, ci }) => (
                          <div key={c.key} className="flex items-center gap-2">
                            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: c.color }} />
                            <span>{t(c.label)}: <span className="font-semibold">{bk.counts[ci]}</span></span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* X-axis ticks (offset to clear the y-axis gutter) */}
      <div className="flex justify-between mt-2.5 pl-9 text-xs text-muted-foreground/80">
        {tickIdxs.map((i) => (
          <span key={i}>{hourLabel(shown[i].startMs)}</span>
        ))}
      </div>
    </div>
  );
}

function QualityBreakdownModal({ batches, start, end, onClose }: { batches: BatchLog[]; start: number; end: number; onClose: () => void }) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const completed = batches.filter((b) => statusBucket(b.batch_status) === "completed");
  const total = completed.length;
  const totals = QUALITY_CATS.map((c) => completed.filter(c.pred).length);
  const zeroKbFiles = completed.reduce((a, b) => a + (b.zero_kb_file_count ?? 0), 0);

  // Clickable legend filters the chart series (Superset-style). Toggling a
  // category adds/removes it from `hidden`; at least one stays visible.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key);
    else if (next.size < QUALITY_CATS.length - 1) next.add(key); // keep ≥1 series
    return next;
  });

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-card border border-border rounded-2xl shadow-xl w-full max-w-6xl max-h-[94vh] flex flex-col animate-scale-in"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-border">
          <div className="min-w-0">
            <div className="text-base font-semibold text-foreground">{t("Completed batch volume — data quality")}</div>
            <div className="text-xs text-muted-foreground mt-0.5">{total.toLocaleString()} {t("completed batches")} · {fmtRange(new Date(start), new Date(end))}</div>
          </div>
          <Tooltip label={t("Close this dialog")} side="bottom">
            <button
              onClick={onClose}
              className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground shrink-0"
              aria-label={t("Close")}
            >
              <X className="h-4 w-4" />
            </button>
          </Tooltip>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {total === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">{t("No completed batches in this window.")}</div>
          ) : (
            <div>
              {/* Clickable legend — toggles each series in the chart. */}
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
                {QUALITY_CATS.map((c, ci) => {
                  const off = hidden.has(c.key);
                  return (
                    <button
                      key={c.key}
                      type="button"
                      onClick={() => toggle(c.key)}
                      aria-pressed={!off}
                      title={off ? t("Show series") : t("Hide series")}
                      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md cursor-pointer hover:bg-muted transition ${off ? "opacity-40" : ""}`}
                    >
                      <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: c.color }} />
                      <span className={`text-muted-foreground ${off ? "line-through" : ""}`}>
                        {t(c.label)}
                        {c.key === "zeroKb" && zeroKbFiles > 0 && ` (${zeroKbFiles.toLocaleString()} ${t("files total")})`}
                      </span>
                      <span className="font-semibold text-foreground tabular-nums">{totals[ci].toLocaleString()}</span>
                    </button>
                  );
                })}
              </div>

              <QualityVolumeChart batches={batches} start={start} end={end} hidden={hidden} tall />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function BatchRow({ batch }: { batch: BatchLog }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const dur = Math.round((new Date(batch.batch_end_time).getTime() - new Date(batch.batch_start_time).getTime()) / 1000);

  const stages: { key: string; label: string; status: StageStatus; icon: any }[] = [
    { key: "watcher", label: "Watcher", status: batch.watcher_status, icon: Eye },
    { key: "decoder", label: "Decoder", status: batch.decoder_status, icon: Cpu },
    { key: "ingestion", label: "Ingestion", status: batch.ingestion_status, icon: Database },
    { key: "normalization", label: "Normalize", status: batch.normalization_status, icon: Wand2 },
  ];

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-3 flex items-start gap-3 hover:bg-muted/30 transition text-left"
      >
        <span
          className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${batch.batch_status === "FAILED" ? "bg-destructive"
            : batch.batch_status === "PARTIAL" ? "bg-[#F97316]"
              : batch.batch_status === "IN_PROGRESS" ? "bg-[#F8C800]"
                : "bg-success"
            }`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[12px] font-medium text-foreground truncate">{batch.batch_id}</span>
            <StatusBadge value={batch.batch_status} />
          </div>
          <div className="flex items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground mt-1.5 flex-wrap">
            <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" /> {fmt(batch.batch_timestamp)}</span>
            <span className="inline-flex items-center gap-1"><Timer className="h-3 w-3" /> {dur}s</span>
            <span className="inline-flex items-center gap-1"><Layers className="h-3 w-3" /> {batch.total_files} {t("files")}</span>
          </div>
        </div>
        <ChevronDown className={`mt-1 h-4 w-4 text-muted-foreground transition-transform shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="px-4 pb-4 bg-muted/10 border-t border-border">
          {/* Stage pipeline */}
          <div className="grid grid-cols-2 gap-1.5 pt-3">
            {stages.map((s) => {
              const Icon = s.icon;
              const v = String(s.status ?? "").toUpperCase();
              // Green = success, red = failed, amber (#F8C800) = running/in-progress,
              // orange (#F97316) = partial/warning, grey = Skipped/Pending/None.
              const tone =
                v === "SUCCESS" || v === "COMPLETED" ? "border-success/30 bg-success/5 text-success"
                  : v === "FAILED" ? "border-destructive/30 bg-destructive/5 text-destructive"
                    : v === "PARTIAL" || v === "WARNING" ? "border-[#F97316]/40 bg-[#F97316]/10 text-[#F97316]"
                      : v === "RUNNING" || v === "IN_PROGRESS" ? "border-[#F8C800]/50 bg-[#F8C800]/10 text-[#9a7d00]"
                        : "border-border bg-muted/40 text-muted-foreground";
              return (
                <div key={s.key} className={`rounded-lg border px-2.5 py-2 ${tone}`}>
                  <div className="flex items-center gap-1.5">
                    <Icon className="h-3 w-3 shrink-0" />
                    <span className="text-[11px] font-semibold truncate">{t(s.label)}</span>
                  </div>
                  <div className="text-[10px] font-medium opacity-90 mt-0.5 tracking-wide">{t(String(s.status ?? ""))}</div>
                </div>
              );
            })}
          </div>

          {/* Metrics - 2 columns of label+value pairs */}
          <div className="mt-3 rounded-lg border border-border bg-card divide-y divide-border">
            <MetricRow label={t("Archived")} value={batch.archived_file_count} />
            <MetricRow label={t("Decoded")} value={batch.decode_complete_count} />
            <MetricRow label={t("Decode Failed")} value={batch.decode_failed_count} tone={batch.decode_failed_count > 0 ? "danger" : "default"} />
            <MetricRow label={t("Loaded")} value={batch.load_complete_count} />
            <MetricRow label={t("Load Failed")} value={batch.load_failed_count} tone={batch.load_failed_count > 0 ? "danger" : "default"} />
            <MetricRow label={t("Zero KB")} value={batch.zero_kb_file_count} tone={batch.zero_kb_file_count > 0 ? "warning" : "default"} />
            <MetricRow label={t("Duplicates")} value={batch.duplicate_file_count} tone={batch.duplicate_file_count > 0 ? "warning" : "default"} />
            <MetricRow label={t("Corrupt")} value={batch.corrupt_file_count} tone={batch.corrupt_file_count > 0 ? "danger" : "default"} />
          </div>

          {batch.error_message && (
            <div className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive leading-relaxed">
              <span className="font-semibold">{t("Error:")}</span> {batch.error_message}
            </div>
          )}
          {batch.validation_message && (
            <div className="mt-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] text-warning-foreground leading-relaxed">
              <span className="font-semibold">{t("Validation:")}</span> {batch.validation_message}
            </div>
          )}
          {batch.quarantine_reason && (
            <div className="mt-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground leading-relaxed">
              <span className="font-semibold text-foreground">{t("Quarantine:")}</span> {batch.quarantine_reason}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type StageStatus = "SUCCESS" | "PARTIAL" | "FAILED" | "PENDING" | "RUNNING" | "IN_PROGRESS";

function MetricRow({ label, value, tone = "default" }: { label: string; value: string | number; tone?: "default" | "danger" | "warning" }) {
  const cls = tone === "danger" ? "text-destructive" : tone === "warning" ? "text-warning-foreground" : "text-foreground";
  return (
    <div className="flex items-center justify-between px-3 py-1.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className={`text-[12px] font-semibold tabular-nums ${cls}`}>{value}</span>
    </div>
  );
}

// Legacy KPI cards (kept for compatibility but no longer rendered)
function _LegacyKpis({ kpis }: { kpis: any }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <KpiCard icon={Gauge} label="Throughput" value={kpis?.throughput || "—"} tone="text-info" />
      <KpiCard icon={Timer} label="Average Latency" value={kpis?.avgLatency || "—"} tone="text-primary" />
      <KpiCard icon={AlertTriangle} label="Failed in last 24h" value={String(kpis?.failed24h ?? "—")} tone="text-destructive" />
      <KpiCard icon={Activity} label="SLA Breaches" value={String(kpis?.slaBreaches ?? "—")} tone="text-warning" />
    </div>
  );
}


type StageDef = {
  key: "watcher" | "decoder" | "ingestion" | "normalization";
  name: string;
  status: string;
  duration: string;
  primary: string;
  secondary?: string;
  details: { label: string; value: string | number; tone?: "default" | "danger" | "warning" | "success" }[];
};

type Flow = {
  id: string;
  dag: string;
  sub: string;
  runId: string;
  startedAt: string;
  records: number;
  overallStatus: string;
  stages: StageDef[];
};

const stageMeta: Record<StageDef["key"], { icon: any; label: string }> = {
  watcher: { icon: Eye, label: "Watcher" },
  decoder: { icon: Cpu, label: "Decoder" },
  ingestion: { icon: Database, label: "Ingestion" },
  normalization: { icon: Wand2, label: "Normalization" },
};

function dur(start: string, end: string): string {
  if (!start || !end) return "--";
  const startTime = new Date(start).getTime();
  const endTime = new Date(end).getTime();
  if (isNaN(startTime) || isNaN(endTime)) return "--";
  const ms = Math.max(0, endTime - startTime);
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function mapStatus(s: StageStatus | string): string {
  const v = String(s ?? "").trim().toUpperCase();
  // No status reported yet → treat the stage as Pending rather than blank.
  if (!v || v === "NONE" || v === "NULL" || v === "UNKNOWN") return "Pending";
  if (v === "SUCCESS") return "Completed";
  if (v === "PARTIAL") return "Partial";
  if (v === "FAILED") return "Failed";
  if (v === "PENDING") return "Pending";
  if (v === "IN_PROGRESS") return "In Progress";
  return String(s);
}

// Reconcile a stage's reported status against its own counters. The backend can
// lag: a stage still flagged PENDING while its complete/failed counts are
// already non-zero is actually running, not queued — so surface "In Progress"
// instead of the misleading "Pending". Everything else passes through as-is.
function stageStatus(reported: StageStatus | string, progress: number): string {
  const mapped = mapStatus(reported);
  if (mapped === "Pending" && progress > 0) return "In Progress";
  return mapped;
}

function batchToFlow(b: BatchLog, dag: string, sub: string, t: (k: string) => string): Flow {
  const overall = mapStatus(b.batch_status);
  const records = b.decode_complete_count + b.decode_failed_count || b.total_files;
  const stages: StageDef[] = [
    {
      key: "watcher", name: t("Watcher"), status: stageStatus(b.watcher_status, b.total_files),
      duration: dur(b.watcher_start_time, b.watcher_end_time),
      primary: `${b.total_files} ${t("files watched")}`,
      details: [
        { label: t("Files watched"), value: b.total_files },
        { label: t("Archived"), value: b.archived_file_count },
        { label: t("Zero KB"), value: b.zero_kb_file_count, tone: b.zero_kb_file_count > 0 ? "warning" : "default" },
        { label: t("Duplicate"), value: b.duplicate_file_count, tone: b.duplicate_file_count > 0 ? "warning" : "default" },
        { label: t("Corrupt"), value: b.corrupt_file_count, tone: b.corrupt_file_count > 0 ? "danger" : "default" },
      ],
    },
    {
      key: "decoder", name: t("Decoder"), status: stageStatus(b.decoder_status, b.decode_complete_count + b.decode_failed_count),
      duration: dur(b.decoder_start_time, b.decoder_end_time),
      primary: `${b.decode_complete_count} ${t("complete")}`,
      secondary: `${b.decode_failed_count} ${t("failed")}`,
      details: [
        { label: t("Complete"), value: b.decode_complete_count, tone: "success" },
        { label: t("Failed"), value: b.decode_failed_count, tone: b.decode_failed_count > 0 ? "danger" : "default" },
        { label: t("Corrupt"), value: b.corrupt_file_count, tone: b.corrupt_file_count > 0 ? "danger" : "default" },
      ],
    },
    {
      key: "ingestion", name: t("Ingestion"), status: stageStatus(b.ingestion_status, b.load_complete_count + b.load_failed_count),
      duration: dur(b.ingestion_start_time, b.ingestion_end_time),
      primary: `${b.load_complete_count} ${t("loaded")}`,
      secondary: `${b.load_failed_count} ${t("failed")}`,
      details: [
        { label: t("Loaded"), value: b.load_complete_count, tone: "success" },
        { label: t("Failed"), value: b.load_failed_count, tone: b.load_failed_count > 0 ? "danger" : "default" },
        { label: t("AA Rows"), value: b.total_aa_rows.toLocaleString() },
        { label: t("RR Rows"), value: b.total_rr_rows.toLocaleString() },
      ],
    },
    {
      key: "normalization", name: t("Normalization"), status: stageStatus(b.normalization_status, b.total_aa_rows + b.total_rr_rows),
      duration: dur(b.normalization_start_time, b.normalization_end_time),
      primary: `${(b.total_aa_rows + b.total_rr_rows).toLocaleString()} ${t("rows normalized")}`,
      details: [
        { label: t("AA Rows"), value: b.total_aa_rows.toLocaleString() },
        { label: t("RR Rows"), value: b.total_rr_rows.toLocaleString() },
        { label: t("Quarantined"), value: b.quarantined_file_count, tone: b.quarantined_file_count > 0 ? "warning" : "default" },
      ],
    },
  ];
  return {
    id: `${dag}-${sub}-${b.batch_id}`,
    dag, sub,
    runId: b.batch_id,
    overallStatus: overall,
    records,
    startedAt: b.batch_start_time,
    stages,
  };
}

function buildFlows(): Flow[] {
  const mk = (
    dag: string,
    sub: string,
    runId: string,
    overall: string,
    stages: StageDef[],
    records = 249,
  ): Flow => ({
    id: `${dag}-${sub}-${runId}`,
    dag,
    sub,
    runId,
    overallStatus: overall,
    records,
    startedAt: "2026-06-02T09:50:44Z",
    stages,
  });

  return [
    mk("AIR", "Raw", "AIR_RAW_20260602192043", "Partial", [
      {
        key: "watcher", name: "Watcher", status: "Partial", duration: "0m 0s", primary: "249 files watched",
        details: [
          { label: "Files watched", value: 249 },
          { label: "Zero KB", value: 3, tone: "warning" },
          { label: "Duplicate", value: 0 },
          { label: "Corrupt", value: 6, tone: "danger" },
          { label: "Throughput", value: "12k/s" },
        ]
      },
      {
        key: "decoder", name: "Decoder", status: "Partial", duration: "0m 51s", primary: "240 complete", secondary: "0 failed",
        details: [
          { label: "Complete", value: 240, tone: "success" },
          { label: "Failed", value: 0 },
          { label: "Schema errors", value: 2, tone: "warning" },
          { label: "Avg decode time", value: "212 ms" },
          { label: "Decoder version", value: "v3.2.1" },
        ]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Partial", duration: "870m 40s", primary: "240 loaded", secondary: "0 failed",
        details: [
          { label: "Loaded", value: 240, tone: "success" },
          { label: "Failed", value: 0 },
          { label: "Buffered", value: 8 },
          { label: "Throughput", value: "4.2k rec/s" },
          { label: "Target", value: "raw.air_events" },
        ]
      },
      {
        key: "normalization", name: "Normalization", status: "Pending", duration: "—", primary: "0 refill", secondary: "0 adjustment",
        details: [
          { label: "Refill", value: 0 },
          { label: "Adjustment", value: 0 },
          { label: "Enrichment", value: 0 },
          { label: "Pending", value: 240, tone: "warning" },
        ]
      },
    ]),
    mk("AIR", "Processed", "AIR_PRC_20260602192043", "Completed", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 2s", primary: "412 files watched",
        details: [
          { label: "Files watched", value: 412 },
          { label: "Zero KB", value: 0 },
          { label: "Duplicate", value: 4, tone: "warning" },
          { label: "Corrupt", value: 0 },
        ]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "1m 12s", primary: "412 complete", secondary: "0 failed",
        details: [
          { label: "Complete", value: 412, tone: "success" },
          { label: "Failed", value: 0 },
          { label: "Avg decode time", value: "184 ms" },
        ]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Completed", duration: "4m 18s", primary: "412 loaded", secondary: "0 failed",
        details: [
          { label: "Loaded", value: 412, tone: "success" },
          { label: "Failed", value: 0 },
          { label: "Throughput", value: "5.1k rec/s" },
        ]
      },
      {
        key: "normalization", name: "Normalization", status: "Completed", duration: "2m 04s", primary: "412 normalized",
        details: [
          { label: "Refill", value: 12 },
          { label: "Adjustment", value: 3 },
          { label: "Enrichment", value: 412, tone: "success" },
        ]
      },
    ], 412),
    mk("AIR", "Reconciled", "AIR_REC_20260602192043", "Warning", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "98 files watched",
        details: [{ label: "Files watched", value: 98 }, { label: "Zero KB", value: 0 }, { label: "Corrupt", value: 0 }]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "0m 22s", primary: "98 complete",
        details: [{ label: "Complete", value: 98, tone: "success" }, { label: "Failed", value: 0 }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Warning", duration: "3m 02s", primary: "94 loaded", secondary: "4 failed",
        details: [{ label: "Loaded", value: 94 }, { label: "Failed", value: 4, tone: "danger" }, { label: "Buffered", value: 0 }]
      },
      {
        key: "normalization", name: "Normalization", status: "Warning", duration: "1m 30s", primary: "Mismatch 0.4%",
        details: [{ label: "Refill", value: 2 }, { label: "Adjustment", value: 1 }, { label: "Mismatch", value: "0.4%", tone: "warning" }]
      },
    ], 98),

    mk("MSC", "Raw", "MSC_RAW_20260602192043", "Completed", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "1,284 files watched",
        details: [{ label: "Files watched", value: 1284 }, { label: "Zero KB", value: 1 }, { label: "Duplicate", value: 2 }, { label: "Corrupt", value: 0 }]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "2m 41s", primary: "1,284 complete",
        details: [{ label: "Complete", value: 1284, tone: "success" }, { label: "Failed", value: 0 }, { label: "Avg decode time", value: "98 ms" }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Completed", duration: "6m 02s", primary: "1,284 loaded",
        details: [{ label: "Loaded", value: 1284, tone: "success" }, { label: "Failed", value: 0 }, { label: "Throughput", value: "12k rec/s" }]
      },
      {
        key: "normalization", name: "Normalization", status: "Pending", duration: "—", primary: "Queued",
        details: [{ label: "Pending", value: 1284, tone: "warning" }]
      },
    ], 1284),
    mk("MSC", "Processed", "MSC_PRC_20260602192043", "Running", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 2s", primary: "984 files watched",
        details: [{ label: "Files watched", value: 984 }, { label: "Duplicate", value: 0 }, { label: "Corrupt", value: 0 }]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "1m 51s", primary: "984 complete",
        details: [{ label: "Complete", value: 984, tone: "success" }, { label: "Failed", value: 0 }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Running", duration: "in progress", primary: "612 loaded", secondary: "0 failed",
        details: [{ label: "Loaded", value: 612 }, { label: "In flight", value: 372 }, { label: "Throughput", value: "8.3k rec/s" }]
      },
      {
        key: "normalization", name: "Normalization", status: "Pending", duration: "—", primary: "Waiting",
        details: [{ label: "Pending", value: 984, tone: "warning" }]
      },
    ], 984),
    mk("MSC", "Reconciled", "MSC_REC_20260602192043", "Failed", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "212 files watched",
        details: [{ label: "Files watched", value: 212 }, { label: "Zero KB", value: 0 }]
      },
      {
        key: "decoder", name: "Decoder", status: "Failed", duration: "0m 40s", primary: "180 complete", secondary: "32 failed",
        details: [{ label: "Complete", value: 180 }, { label: "Failed", value: 32, tone: "danger" }, { label: "Schema errors", value: 32, tone: "danger" }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Pending", duration: "—", primary: "Blocked",
        details: [{ label: "Blocked by", value: "Decoder" }]
      },
      {
        key: "normalization", name: "Normalization", status: "Pending", duration: "—", primary: "—",
        details: [{ label: "Pending", value: 212, tone: "warning" }]
      },
    ], 212),

    mk("SDP", "Raw", "SDP_RAW_20260602192043", "Completed", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "742 files watched",
        details: [{ label: "Files watched", value: 742 }, { label: "Zero KB", value: 0 }, { label: "Duplicate", value: 0 }, { label: "Corrupt", value: 1, tone: "danger" }]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "1m 02s", primary: "741 complete",
        details: [{ label: "Complete", value: 741, tone: "success" }, { label: "Failed", value: 1 }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Completed", duration: "3m 45s", primary: "741 loaded",
        details: [{ label: "Loaded", value: 741, tone: "success" }, { label: "Failed", value: 0 }]
      },
      {
        key: "normalization", name: "Normalization", status: "Completed", duration: "1m 15s", primary: "741 normalized",
        details: [{ label: "Refill", value: 4 }, { label: "Adjustment", value: 0 }]
      },
    ], 742),
    mk("SDP", "Processed", "SDP_PRC_20260602192043", "Warning", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "318 files watched",
        details: [{ label: "Files watched", value: 318 }, { label: "Duplicate", value: 6, tone: "warning" }]
      },
      {
        key: "decoder", name: "Decoder", status: "Warning", duration: "0m 58s", primary: "312 complete", secondary: "6 warnings",
        details: [{ label: "Complete", value: 312 }, { label: "Warnings", value: 6, tone: "warning" }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Completed", duration: "2m 12s", primary: "312 loaded",
        details: [{ label: "Loaded", value: 312, tone: "success" }]
      },
      {
        key: "normalization", name: "Normalization", status: "Completed", duration: "0m 48s", primary: "312 normalized",
        details: [{ label: "Adjustment", value: 2 }]
      },
    ], 318),
    mk("SDP", "Reconciled", "SDP_REC_20260602192043", "Completed", [
      {
        key: "watcher", name: "Watcher", status: "Completed", duration: "0m 1s", primary: "54 files watched",
        details: [{ label: "Files watched", value: 54 }]
      },
      {
        key: "decoder", name: "Decoder", status: "Completed", duration: "0m 11s", primary: "54 complete",
        details: [{ label: "Complete", value: 54, tone: "success" }]
      },
      {
        key: "ingestion", name: "Ingestion", status: "Completed", duration: "0m 58s", primary: "54 loaded",
        details: [{ label: "Loaded", value: 54, tone: "success" }]
      },
      {
        key: "normalization", name: "Normalization", status: "Completed", duration: "0m 22s", primary: "Match 99.98%",
        details: [{ label: "Refill", value: 0 }, { label: "Adjustment", value: 0 }, { label: "Match", value: "99.98%", tone: "success" }]
      },
    ], 54),
  ];
}

function FlowCard({ flow }: { flow: Flow }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  // Cursor-anchored tooltip: which stage is hovered + the live cursor position.
  const [tip, setTip] = useState<{ x: number; y: number; stage: StageDef } | null>(null);
  // Stream-type tags use neutral, non-status hues (slate / indigo / violet) so
  // they don't collide with the green/amber/red status palette.
  const subTone =
    flow.sub === "Raw" ? "bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/20"
      : flow.sub === "Processed" ? "bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 border-indigo-500/20"
        : "bg-violet-500/10 text-violet-600 dark:text-violet-300 border-violet-500/20";

  return (
    <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
      <div className="p-5 flex items-center justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[10px] font-bold tracking-[0.12em] uppercase text-muted-foreground">{flow.dag}</span>
            <span className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-md border ${subTone}`}>{flow.sub}</span>
          </div>
          <h3 className="text-lg font-semibold text-foreground font-mono truncate">{flow.runId}</h3>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span>{t("Started")} <span className="text-foreground font-medium">{fmt(flow.startedAt)}</span></span>
          <span>· <span className="text-foreground font-medium">{flow.records.toLocaleString()}</span></span>
          <StatusBadge value={flow.overallStatus} />
          <button
            onClick={() => setOpen(!open)}
            className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-border bg-background hover:bg-muted transition"
            aria-label={open ? t("Collapse") : t("Expand")}
          >
            <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {open && (
        <div className="px-5 pb-5 border-t border-border pt-5 bg-muted/10">
          {/* Stages scroll horizontally so every node name + status stays fully visible. */}
          <div className="flex gap-4 overflow-x-auto pb-2 -mx-1 px-1">
            {flow.stages.map((s) => {
              const Icon = stageMeta[s.key].icon;
              return (
                <div
                  key={s.key}
                  onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, stage: s })}
                  onMouseLeave={() => setTip((t) => (t?.stage.key === s.key ? null : t))}
                  className="w-[260px] flex-shrink-0 rounded-xl border border-border bg-background p-5 hover:border-primary/50 hover:shadow-md transition cursor-default"
                >
                  <div className="flex items-center gap-3 mb-3">
                    <div className="h-10 w-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
                      <Icon className="h-5 w-5" />
                    </div>
                    <span className="text-sm font-semibold text-foreground whitespace-nowrap">{s.name}</span>
                  </div>
                  <div className="mb-3">
                    <StatusBadge value={s.status} />
                  </div>
                  <div className="text-xs text-muted-foreground flex items-center gap-1.5 mb-2">
                    <Timer className="h-3.5 w-3.5" /> {s.duration}
                  </div>
                  <div className="text-sm text-foreground/90 font-medium">{s.primary}</div>
                  {s.secondary && <div className="text-xs text-muted-foreground mt-0.5">{s.secondary}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Cursor-anchored details tooltip (replaces the old below-the-card popup). */}
      {tip && <StageTooltip tip={tip} />}
    </div>
  );
}

function StageTooltip({ tip }: { tip: { x: number; y: number; stage: StageDef } }) {
  const { stage } = tip;
  const W = 240;
  const GAP = 16;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  // Flip to the other side of the cursor when we'd overflow the viewport.
  const estHeight = 92 + stage.details.length * 22;
  const left = tip.x + GAP + W > vw ? Math.max(8, tip.x - GAP - W) : tip.x + GAP;
  const top = tip.y + GAP + estHeight > vh ? Math.max(8, tip.y - GAP - estHeight) : tip.y + GAP;

  return (
    <div
      style={{ position: "fixed", left, top, width: W }}
      className="pointer-events-none z-50 rounded-lg border border-border bg-popover shadow-xl p-3"
    >
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-xs font-semibold text-foreground">{stage.name}</span>
        <StatusBadge value={stage.status} />
      </div>
      <div className="text-[11px] text-muted-foreground flex items-center gap-1 mb-2">
        <Timer className="h-3 w-3" /> {stage.duration} · {stage.primary}
      </div>
      <div className="space-y-1.5 border-t border-border pt-2">
        {stage.details.map((d) => {
          const toneCls =
            d.tone === "danger" ? "text-destructive"
              : d.tone === "warning" ? "text-warning-foreground"
                : d.tone === "success" ? "text-success"
                  : "text-foreground";
          return (
            <div key={d.label} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{d.label}</span>
              <span className={`font-semibold ${toneCls}`}>{d.value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, label, value, tone }: any) {
  return (
    <div className="bg-card border border-border rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</div>
        <Icon className={`h-4 w-4 ${tone}`} />
      </div>
      <div className="text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function fmt(d: string) {
  try { return new Date(d).toLocaleString(); } catch { return d; }
}

