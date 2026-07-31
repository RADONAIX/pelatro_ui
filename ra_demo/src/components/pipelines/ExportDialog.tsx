import { useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { useNavigate } from "@tanstack/react-router";
import { X, Download, Search, Filter as FilterIcon, ArrowLeft, FileText, Loader2, ArrowDownToLine, RotateCcw } from "lucide-react";
import { type BatchLog } from "@/lib/airBatches";
import { type FileLog } from "@/lib/airFiles";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { Select } from "@/components/ui-kit/Select";
import { useBatches, useBatchFiles } from "@/lib/pipelinesApi";
import { useT } from "@/lib/i18n";
import { useDownloads } from "@/lib/downloads";
import { exportsService, type CreateExportPayload } from "@/services";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { DateRangePicker } from "@/components/ui-kit/DateRangePicker";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import { toast } from "sonner";

// The date bounds are stored as "YYYY-MM-DD HH:mm" (local). Convert to/from the
// Date objects the DateRangePicker works with.
function parseDt(s: string): Date | null {
  if (!s) return null;
  const ms = new Date(s).getTime();
  return isNaN(ms) ? null : new Date(ms);
}
function dtStr(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

type Dag = "AIR" | "MSC" | "SDP";
type Stream = "Raw" | "Processed" | "Reconciled";

type Row = BatchLog & { dag: Dag; stream: Stream };

const ALL_DAGS: Dag[] = ["AIR", "MSC", "SDP"];
const ALL_STREAMS: Stream[] = ["Raw", "Processed", "Reconciled"];

// DAG + stream are encoded in the batch_id (e.g. AIR_RAW_..., AIR_PROCESSED_...),
// not stored as separate columns — derive them rather than hardcoding.
function parseBatchType(batchId: string): { dag: Dag; stream: Stream } {
  const parts = (batchId || "").toUpperCase().split("_");
  const dag = (ALL_DAGS as string[]).includes(parts[0]) ? (parts[0] as Dag) : "AIR";
  const stream: Stream =
    parts[1] === "RAW" ? "Raw" : parts[1] === "RECONCILED" ? "Reconciled" : "Processed";
  return { dag, stream };
}

const COLUMNS: { key: keyof Row; label: string }[] = [
  { key: "batch_id", label: "Batch ID" },
  { key: "batch_start_time", label: "Start" },
  { key: "batch_end_time", label: "End" },
  { key: "dag", label: "DAG" },
  { key: "stream", label: "Stream" },
  { key: "batch_status", label: "Status" },
  { key: "total_files", label: "Files" },
  { key: "decode_complete_count", label: "Decoded" },
  { key: "decode_failed_count", label: "Failed" },
  { key: "load_complete_count", label: "Loaded" },
  { key: "zero_kb_file_count", label: "Zero KB" },
  { key: "duplicate_file_count", label: "Duplicate" },
  { key: "corrupt_file_count", label: "Corrupt" },
];

// Split a "YYYY-MM-DD HH:MM:SS.ssssss" timestamp into date + time (no millis)
// for the two-line Start/End cells. Returns null for empty/unparseable values.
function splitDateTime(v: unknown): { date: string; time: string } | null {
  if (v == null || v === "") return null;
  const [date, rawTime = ""] = String(v).split(/[ T]/);
  if (!date) return null;
  return { date, time: rawTime.split(".")[0].slice(0, 8) };
}

function statusTone(s: string) {
  if (s === "SUCCESS" || s === "COMPLETE") return "bg-success/15 text-success border-success/30";
  if (s === "PARTIAL") return "bg-amber-500/15 text-amber-600 border-amber-500/30";
  if (s === "FAILED") return "bg-destructive/15 text-destructive border-destructive/30";
  if (s === "RUNNING") return "bg-info/15 text-info border-info/30";
  if (s === "DUPLICATE") return "bg-amber-500/15 text-amber-600 border-amber-500/30";
  return "bg-muted text-muted-foreground border-border";
}

// Reporting-catalog key for the pipeline batch-log export. The batch export is
// an async server job (Redis/Celery) via POST /exports — the SAME pipeline the
// Reports module uses — so the backend MUST register this report key (with
// columns dag / stream / batch_status and a batch_start_time date column) for
// the export to succeed. Until then, POST /exports returns 404 "Unknown report".
const BATCH_EXPORT_REPORT_KEY = "pipeline_batches";

// Reporting-catalog key for the per-batch FILE-LEVEL export (File logs view).
// Registered on the backend (file_log rows; date column file_timestamp).
// Filterable columns include batch_id, file_status, dag, stream, filename, etc.
// This view is scoped to one batch, so the export is pinned via batch_id.
const PIPELINE_FILES_REPORT_KEY = "pipeline_files";

// The Export Batch Data dialog scans its OWN window (the backend max of 7 days),
// independent of the Pipelines dashboard's date filter — the two never share
// state. The dialog's From/To picker narrows within this fetched set.
const EXPORT_FETCH_HOURS = 168;

export function ExportDialog({ onClose }: { onClose: () => void }) {
  const [view, setView] = useState<{ kind: "batches" } | { kind: "files"; batch: BatchLog }>({ kind: "batches" });
  const [dags, setDags] = useState<Set<Dag>>(new Set());
  const [streams, setStreams] = useState<Set<Stream>>(new Set());
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  // Default the range to the last 24 hours (not "all dates") when the dialog opens.
  const [from, setFrom] = useState(() => dtStr(new Date(Date.now() - 24 * 3600 * 1000)));
  const [to, setTo] = useState(() => dtStr(new Date()));
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Independent data source: the dialog fetches its own batch window and never
  // reads the dashboard's date filter.
  const { data: batches, loading, error } = useBatches(EXPORT_FETCH_HOURS);
  const allRows: Row[] = (batches ?? []).map((b) => ({ ...b, ...parseBatchType(b.batch_id) }));

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-4 animate-fade-in">
      <div className="bg-card border border-border rounded-2xl shadow-xl w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden animate-scale-in">
        {view.kind === "batches" ? (
          <BatchesView
            rows={allRows}
            loading={loading}
            error={error}
            onClose={onClose}
            onViewFiles={(b) => setView({ kind: "files", batch: b })}
            dags={dags}
            setDags={setDags}
            streams={streams}
            setStreams={setStreams}
            statuses={statuses}
            setStatuses={setStatuses}
            from={from}
            setFrom={setFrom}
            to={to}
            setTo={setTo}
            q={q}
            setQ={setQ}
            page={page}
            setPage={setPage}
            pageSize={pageSize}
            setPageSize={setPageSize}
          />
        ) : (
          <FilesView batch={view.batch} onBack={() => setView({ kind: "batches" })} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

function BatchesView({
  rows: ALL_ROWS,
  loading,
  error,
  onClose,
  onViewFiles,
  dags,
  setDags,
  streams,
  setStreams,
  statuses,
  setStatuses,
  from,
  setFrom,
  to,
  setTo,
  q,
  setQ,
  page,
  setPage,
  pageSize,
  setPageSize,
}: {
  rows: Row[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onViewFiles: (b: BatchLog) => void;
  dags: Set<Dag>;
  setDags: React.Dispatch<React.SetStateAction<Set<Dag>>>;
  streams: Set<Stream>;
  setStreams: React.Dispatch<React.SetStateAction<Set<Stream>>>;
  statuses: Set<string>;
  setStatuses: React.Dispatch<React.SetStateAction<Set<string>>>;
  from: string;
  setFrom: React.Dispatch<React.SetStateAction<string>>;
  to: string;
  setTo: React.Dispatch<React.SetStateAction<string>>;
  q: string;
  setQ: React.Dispatch<React.SetStateAction<string>>;
  page: number;
  setPage: React.Dispatch<React.SetStateAction<number>>;
  pageSize: number;
  setPageSize: React.Dispatch<React.SetStateAction<number>>;
}) {
  const t = useT();
  const { refresh } = useDownloads();
  const [sending, setSending] = useState(false);
  const navigate = useNavigate();

  const filtered = useMemo(() => {
    const fromTs = from ? new Date(from).getTime() : -Infinity;
    const toTs = to ? new Date(to).getTime() : Infinity;
    const needle = q.trim().toLowerCase();
    return ALL_ROWS.filter((r) => {
      // Empty set means "show all" for that filter
      if (dags.size > 0 && !dags.has(r.dag)) return false;
      if (streams.size > 0 && !streams.has(r.stream)) return false;
      if (statuses.size > 0 && !statuses.has(r.batch_status)) return false;
      const t = new Date(r.batch_start_time).getTime();
      if (Number.isFinite(t) && (t < fromTs || t > toTs)) return false;
      if (needle && !r.batch_id.toLowerCase().includes(needle)) return false;
      return true;
    });
    // ALL_ROWS must be a dependency: the dialog fetches its data asynchronously,
    // so the row set arrives AFTER mount. Without it here the memo would keep the
    // empty initial result until some other filter changed (the "no data until I
    // touch the date" bug).
  }, [ALL_ROWS, dags, streams, statuses, from, to, q]);

  // Status options come from the data actually loaded — not a fixed list — so the
  // dropdown always reflects the statuses present (e.g. IN_PROGRESS).
  const statusOptions = useMemo(
    () => Array.from(new Set(ALL_ROWS.map((r) => r.batch_status).filter(Boolean))).sort(),
    [ALL_ROWS],
  );

  // Client-side column sorting (same three-state behaviour as the Reports table).
  const { sortKey, sortDir, onSort } = useSort();
  const sorted = useMemo(
    () => sortRows(filtered, sortKey, sortDir, (r, key) => {
      if (key === "batch_start_time" || key === "batch_end_time") {
        const ms = new Date(r[key] as string).getTime();
        return Number.isNaN(ms) ? -Infinity : ms;
      }
      return r[key as keyof Row] as unknown;
    }),
    [filtered, sortKey, sortDir],
  );

  const total = sorted.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const curPage = Math.min(page, totalPages);
  const start = (curPage - 1) * pageSize;
  const pageRows = sorted.slice(start, start + pageSize);

  // Build the /exports payload from the current filters. The server applies
  // these over the FULL batch dataset (not the on-screen sample), exactly like
  // the Reports export. DAG / Stream / Status map to category columns; the date
  // range applies to batch_start_time; the search matches the batch id.
  const buildBatchExportPayload = (): CreateExportPayload => {
    const categories: Record<string, string[]> = {};
    if (dags.size) categories.dag = [...dags];
    if (streams.size) categories.stream = [...streams];
    if (statuses.size) categories.batch_status = [...statuses];
    return {
      reportKey: BATCH_EXPORT_REPORT_KEY,
      dateFrom: from ? from.slice(0, 10) : undefined, // "YYYY-MM-DD"
      dateTo: to ? to.slice(0, 10) : undefined,
      filters: { search: q.trim() || undefined, categories, dateColumn: "batch_start_time" },
    };
  };

  // Enqueue an async server export (Redis/Celery) via POST /exports — the same
  // pipeline the Reports module uses — then surface it in the Download Center.
  const sendToDownloadCenter = async () => {
    if (sending) return;
    setSending(true);
    try {
      await exportsService.create(buildBatchExportPayload());
      refresh(); // surface the new job in the Download Center at once
      toast.info(t("Export request submitted"), {
        description: (
          <span>
            {t("Your file is being generated.")}{" "}
            <button
              type="button"
              onClick={() => { navigate({ to: "/downloads" }); toast.dismiss(); }}
              className="font-medium text-info underline underline-offset-2 hover:opacity-80"
            >
              {t("Click here")}
            </button>{" "}
            {t("to track it in the Download Center.")}
          </span>
        ),
        duration: 8000,
      });
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Export failed"));
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-9 w-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
            <ArrowDownToLine className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-foreground truncate">{t("Export Batch Data")}</h3>
            <p className="text-xs text-muted-foreground">{t("Filter the dataset, preview results, then download as CSV or drill into file-level logs.")}</p>
          </div>
        </div>
        <Tooltip label={t("Close this dialog")} side="bottom">
          <button onClick={onClose} className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </Tooltip>
      </div>

      <div className="px-5 py-4 border-b border-border space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <MultiSelect label={t("DAG Type")} info={t("Filter batches by their source pipeline")} options={ALL_DAGS} selected={dags} onChange={(s) => { setDags(s); setPage(1); }} allowEmpty />
          <MultiSelect label={t("Stream Type")} info={t("Filter batches by processing stage")} options={ALL_STREAMS} selected={streams} onChange={(s) => { setStreams(s); setPage(1); }} allowEmpty />
          <MultiSelect label={t("Batch Status")} info={t("Filter batches by outcome")} options={statusOptions} selected={statuses} onChange={(s) => { setStatuses(s); setPage(1); }} allowEmpty />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
              {t("Date Range")}
              <InfoHint text={t("Show only batches whose start time falls between the From and To you pick. The To must be after the From and no later than now.")} />
            </div>
            <DateRangePicker
              showTime
              placeholder={t("All dates")}
              start={parseDt(from)}
              end={parseDt(to)}
              max={new Date()}
              onChange={(r) => { setFrom(dtStr(r.start)); setTo(dtStr(r.end)); setPage(1); }}
              className="w-full h-9 px-3 inline-flex items-center gap-2 rounded-lg border border-border bg-background text-sm hover:bg-muted transition"
            />
          </div>
          <div>
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
              {t("Search Batch ID")}
              <InfoHint text={t("Filter to batches whose ID contains this text (e.g. AIR_PROCESSED). Case-insensitive.")} />
            </div>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Batch ID" maxLength={100}
                className="w-full h-9 pl-8 pr-9 rounded-lg border border-border bg-background text-sm" />

              {q && (
                <button
                  type="button"
                  onClick={() => { setQ(""); setPage(1); }}
                  aria-label={t("Clear search")}
                  title={t("Clear search")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between pt-1">
          <Tooltip label={t("Reset all filters to their defaults")} side="bottom">
            <button onClick={() => { setDags(new Set()); setStreams(new Set()); setStatuses(new Set()); setFrom(dtStr(new Date(Date.now() - 24 * 3600 * 1000))); setTo(dtStr(new Date())); setQ(""); setPage(1); }}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-border bg-background text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition">
              <RotateCcw className="h-3.5 w-3.5" /> {t("Reset filters")}
            </button>
          </Tooltip>
          <div className="text-xs text-muted-foreground">
            {t("Showing")} <span className="font-medium text-foreground">{total === 0 ? 0 : start + 1}–{Math.min(start + pageSize, total)}</span> {t("of")} <span className="font-medium text-foreground">{total}</span> {t("records (filtered from")} {ALL_ROWS.length})
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden px-5 py-4 flex flex-col">
        {loading ? (
          <div className="border border-border rounded-lg bg-background py-16 flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("Loading batches…")}
          </div>
        ) : error ? (
          <div className="border border-destructive/30 rounded-lg bg-destructive/10 py-10 px-4 text-center text-sm text-destructive">
            {t("Failed to load batches:")} {error}
          </div>
        ) : (
          <div className="flex-1 min-h-0 border border-border rounded-lg overflow-auto bg-background">
            <table className="w-full min-w-max text-xs">
              <thead className="bg-muted sticky top-0 z-10">
                <tr>
                  {COLUMNS.map((c) => (
                    <SortHeader
                      key={String(c.key)}
                      label={t(c.label)}
                      colKey={String(c.key)}
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                      thClassName="text-left font-medium text-muted-foreground px-3 py-2 whitespace-nowrap"
                    />
                  ))}
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length === 0 ? (
                  <tr><td colSpan={COLUMNS.length + 1} className="px-3 py-10 text-center text-muted-foreground">{t("No records match the current filters.")}</td></tr>
                ) : pageRows.map((r) => (
                  <tr key={r.batch_id} className="border-t border-border hover:bg-muted/30">
                    {COLUMNS.map((c) => {
                      const v = r[c.key];
                      if (c.key === "batch_status") {
                        return <td key={String(c.key)} className="px-3 py-2"><span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${statusTone(String(v))}`}>{t(String(v))}</span></td>;
                      }
                      if (c.key === "batch_id") {
                        return <td key={String(c.key)} className="px-3 py-2 font-mono text-foreground whitespace-nowrap">{String(v)}</td>;
                      }
                      if (c.key === "batch_start_time" || c.key === "batch_end_time") {
                        const dt = splitDateTime(v);
                        return (
                          <td key={String(c.key)} className="px-3 py-2 text-foreground whitespace-nowrap">
                            {dt ? (
                              <div className="leading-tight">
                                <div>{dt.date}</div>
                                <div className="text-muted-foreground">{dt.time}</div>
                              </div>
                            ) : "—"}
                          </td>
                        );
                      }
                      return <td key={String(c.key)} className="px-3 py-2 text-foreground whitespace-nowrap">{v == null || v === "" ? "—" : String(v)}</td>;
                    })}
                    <td className="px-3 py-2 text-right">
                      <Tooltip label={t("View this batch's files")} side="bottom">
                        <button onClick={() => onViewFiles(r)} className="inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border bg-background hover:bg-muted text-[11px] font-medium text-foreground whitespace-nowrap">
                          <FileText className="h-3 w-3" /> {t("View files")}
                        </button>
                      </Tooltip>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="px-5 py-3 border-t border-border flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {t("Rows per page:")}
          <Select
            value={String(pageSize)}
            onChange={(v) => { setPageSize(Number(v)); setPage(1); }}
            options={[10, 25, 50, 100].map((n) => ({ value: String(n), label: String(n) }))}
            size="sm"
            minWidth={72}
            dropUp
            ariaLabel={t("Rows per page")}
          />
        </div>
        <div className="flex items-center gap-1">
          <Tooltip label={t("Go to first page")} side="bottom"><button disabled={curPage <= 1} onClick={() => setPage(1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">«</button></Tooltip>
          <Tooltip label={t("Go to previous page")} side="bottom"><button disabled={curPage <= 1} onClick={() => setPage(curPage - 1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">{t("Prev")}</button></Tooltip>
          <span className="text-xs text-muted-foreground px-2">{t("Page")} {curPage} / {totalPages}</span>
          <Tooltip label={t("Go to next page")} side="bottom"><button disabled={curPage >= totalPages} onClick={() => setPage(curPage + 1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">{t("Next")}</button></Tooltip>
          <Tooltip label={t("Go to last page")} side="bottom"><button disabled={curPage >= totalPages} onClick={() => setPage(totalPages)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">»</button></Tooltip>
        </div>
        <div className="flex items-center gap-2">
          <Tooltip label={t("Generate the CSV and track it in the Download Center")} side="bottom">
            <button onClick={sendToDownloadCenter} disabled={total === 0 || sending}
              className="h-9 px-4 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-2">
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {t("Download CSV")} ({total})
            </button>
          </Tooltip>
        </div>
      </div>
    </>
  );
}

const FILE_COLUMNS: { key: keyof FileLog; label: string }[] = [
  { key: "sequence_number", label: "#" },
  { key: "filename", label: "Filename" },
  { key: "node_id", label: "Node" },
  { key: "file_status", label: "File Status" },
  { key: "decoder_status", label: "Decoder Status" },
  { key: "db_loading_status", label: "DB Load Status" },
  { key: "expected_record_count", label: "Expected Records" },
  { key: "actual_record_count", label: "Actual Records" },
  { key: "retry_count", label: "Retries" },
  { key: "last_error_step", label: "Error Step" },
  { key: "error_message", label: "Error Message" },
  { key: "file_timestamp", label: "Timestamp" },
];

function FilesView({ batch, onBack, onClose }: { batch: BatchLog; onBack: () => void; onClose: () => void }) {
  const t = useT();
  const { data: fetched, loading, error } = useBatchFiles(batch.batch_id);
  const allFiles: FileLog[] = fetched ?? [];
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const { refresh } = useDownloads();
  const [sending, setSending] = useState(false);
  const navigate = useNavigate();

  // Status options are derived from the files actually loaded (empty set = all),
  // so the dropdown only ever lists statuses that are really present.
  const statusOptions = useMemo(
    () => Array.from(new Set(allFiles.map((f) => f.file_status).filter(Boolean))).sort(),
    [allFiles],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return allFiles.filter((f) => {
      if (statuses.size > 0 && f.file_status && !statuses.has(f.file_status)) return false;
      // Global search: match the needle against every displayed column.
      if (needle && !FILE_COLUMNS.some((c) => String(f[c.key] ?? "").toLowerCase().includes(needle))) return false;
      return true;
    });
  }, [allFiles, statuses, q]);

  // Build the /exports payload for the file-level export. The job is scoped to
  // this batch via the `batch_id` category; the server applies these over the
  // full file_log set (not the on-screen sample), like every other export.
  const buildFilesExportPayload = (): CreateExportPayload => {
    const categories: Record<string, string[]> = { batch_id: [batch.batch_id] };
    if (statuses.size) categories.file_status = [...statuses];
    return {
      reportKey: PIPELINE_FILES_REPORT_KEY,
      filters: { search: q.trim() || undefined, categories },
    };
  };

  // Enqueue an async server export (POST /exports) and surface it in the
  // Download Center — same flow as the batch export.
  const sendToDownloadCenter = async () => {
    if (sending) return;
    setSending(true);
    try {
      await exportsService.create(buildFilesExportPayload());
      refresh(); // surface the new job in the Download Center at once
      toast.info(t("Export request submitted"), {
        description: (
          <span>
            {t("Your file is being generated.")}{" "}
            <button
              type="button"
              onClick={() => { navigate({ to: "/downloads" }); toast.dismiss(); }}
              className="font-medium text-info underline underline-offset-2 hover:opacity-80"
            >
              {t("Click here")}
            </button>{" "}
            {t("to track it in the Download Center.")}
          </span>
        ),
        duration: 8000,
      });
    } catch (e: any) {
      toast.error(e?.response?.data?.error?.message ?? t("Export failed"));
    } finally {
      setSending(false);
    }
  };

  // Client-side column sorting (same three-state behaviour as the batch table).
  const { sortKey, sortDir, onSort } = useSort();
  const sorted = useMemo(
    () => sortRows(filtered, sortKey, sortDir, (r, key) => {
      if (key === "file_timestamp") {
        const ms = new Date(r[key] as string).getTime();
        return Number.isNaN(ms) ? -Infinity : ms;
      }
      return r[key as keyof FileLog] as unknown;
    }),
    [filtered, sortKey, sortDir],
  );

  const total = sorted.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const curPage = Math.min(page, totalPages);
  const start = (curPage - 1) * pageSize;
  const pageRows = sorted.slice(start, start + pageSize);

  const dur = Math.round((new Date(batch.batch_end_time).getTime() - new Date(batch.batch_start_time).getTime()) / 1000);
  const { dag, stream } = parseBatchType(batch.batch_id);

  return (
    <>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border">
        <div className="flex items-center gap-3 min-w-0">
          <Tooltip label={t("Go back")} side="bottom">
            <button onClick={onBack} className="h-8 px-3 rounded-lg border border-border bg-background hover:bg-muted inline-flex items-center gap-1.5 text-sm font-medium">
              <ArrowLeft className="h-4 w-4" /> {t("Back")}
            </button>
          </Tooltip>
          <div className="min-w-0">
            <h3 className="font-semibold text-foreground truncate">{t("File logs ·")} {batch.batch_id}</h3>
            {/* <p className="text-xs text-muted-foreground">air_schema.air_{stream.toLowerCase()}_file_log</p> */}
          </div>
        </div>
        <Tooltip label={t("Close this dialog")} side="bottom">
          <button onClick={onClose} className="h-8 w-8 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </Tooltip>
      </div>

      {/* Batch summary strip */}
      <div className="px-5 py-3 border-b border-border bg-muted/20">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-x-4 gap-y-2 text-xs">
          <Meta label="Batch ID" value={<span className="font-mono">{batch.batch_id}</span>} />
          <Meta label={t("Status")} value={<span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${statusTone(batch.batch_status)}`}>{t(batch.batch_status)}</span>} />
          <Meta label={t("Type")} value={`${dag} · ${stream}`} />
          <Meta label={t("Files")} value={`${batch.total_files.toLocaleString()}`} />
          <Meta label={t("Start")} value={fmt(batch.batch_start_time)} />
          <Meta label={t("End")} value={`${fmt(batch.batch_end_time)} · ${dur}s`} />
        </div>
      </div>

      {/* Filters */}
      <div className="px-5 py-4 border-b border-border space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <MultiSelect label={t("File Status")} info={t("Filter files by their processing outcome. Options reflect the statuses present in this batch.")} options={statusOptions} selected={statuses} onChange={(s) => { setStatuses(s); setPage(1); }} allowEmpty />
          <div>
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
              {t("Search")}
              <InfoHint text={t("Filter to files where any column contains this text. Case-insensitive.")} />
            </div>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder={t("Search all columns…")} maxLength={100}
                className="w-full h-9 pl-8 pr-9 rounded-lg border border-border bg-background text-sm" />

              {q && (
                <button
                  type="button"
                  onClick={() => { setQ(""); setPage(1); }}
                  aria-label={t("Clear search")}
                  title={t("Clear search")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between">
           <Tooltip label={t("Reset all filters to their defaults")} side="bottom">
            <button onClick={() => { setStatuses(new Set()); setQ(""); setPage(1); }}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-border bg-background text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition">
              <RotateCcw className="h-3.5 w-3.5" /> {t("Reset filters")}
            </button>
          </Tooltip>
          <div className="text-xs text-muted-foreground">
            {t("Showing")} <span className="font-medium text-foreground">{total === 0 ? 0 : start + 1}–{Math.min(start + pageSize, total)}</span> {t("of")} <span className="font-medium text-foreground">{total}</span> {t("files")} ({t("of")} {allFiles.length})
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden px-5 py-4 flex flex-col">
        {loading ? (
          <div className="border border-border rounded-lg bg-background py-16 flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("Loading files for")} {batch.batch_id}…
          </div>
        ) : error ? (
          <div className="border border-destructive/30 rounded-lg bg-destructive/10 py-10 px-4 text-center text-sm text-destructive">
            {t("Failed to load files:")} {error}
          </div>
        ) : (
          <div className="flex-1 min-h-0 border border-border rounded-lg overflow-auto bg-background">
            <table className="w-full min-w-max text-xs">
              <thead className="bg-muted sticky top-0 z-10">
                <tr>
                  {FILE_COLUMNS.map((c) => (
                    <SortHeader
                      key={String(c.key)}
                      label={t(c.label)}
                      colKey={String(c.key)}
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                      thClassName="text-left font-medium text-muted-foreground px-3 py-2 whitespace-nowrap"
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {pageRows.length === 0 ? (
                  <tr><td colSpan={FILE_COLUMNS.length} className="px-3 py-10 text-center text-muted-foreground">{t("No files match the current filters.")}</td></tr>
                ) : pageRows.map((r) => (
                  <tr key={r.id} className="border-t border-border hover:bg-muted/30">
                    {FILE_COLUMNS.map((c) => {
                      const v = r[c.key];
                      if (c.key === "file_status" || c.key === "decoder_status" || c.key === "db_loading_status") {
                        return <td key={String(c.key)} className="px-3 py-2"><span className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${statusTone(String(v))}`}>{t(String(v))}</span></td>;
                      }
                      if (c.key === "filename") {
                        return <td key={String(c.key)} className="px-3 py-2 font-mono text-foreground whitespace-nowrap max-w-[280px] truncate" title={String(v)}>{String(v)}</td>;
                      }
                      if (c.key === "error_message") {
                        return <td key={String(c.key)} className="px-3 py-2 text-destructive/90 max-w-[220px] truncate" title={String(v)}>{v ? String(v) : "—"}</td>;
                      }
                      return <td key={String(c.key)} className="px-3 py-2 text-foreground whitespace-nowrap">{v == null || v === "" ? "—" : String(v)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="px-5 py-3 border-t border-border flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {t("Rows per page:")}
          <Select
            value={String(pageSize)}
            onChange={(v) => { setPageSize(Number(v)); setPage(1); }}
            options={[10, 25, 50, 100].map((n) => ({ value: String(n), label: String(n) }))}
            size="sm"
            minWidth={72}
            dropUp
            ariaLabel={t("Rows per page")}
          />
        </div>
        <div className="flex items-center gap-1">
          <Tooltip label={t("Go to first page")} side="bottom"><button disabled={curPage <= 1} onClick={() => setPage(1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">«</button></Tooltip>
          <Tooltip label={t("Go to previous page")} side="bottom"><button disabled={curPage <= 1} onClick={() => setPage(curPage - 1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">{t("Prev")}</button></Tooltip>
          <span className="text-xs text-muted-foreground px-2">{t("Page")} {curPage} / {totalPages}</span>
          <Tooltip label={t("Go to next page")} side="bottom"><button disabled={curPage >= totalPages} onClick={() => setPage(curPage + 1)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">{t("Next")}</button></Tooltip>
          <Tooltip label={t("Go to last page")} side="bottom"><button disabled={curPage >= totalPages} onClick={() => setPage(totalPages)} className="h-8 px-2 rounded-md border border-border text-xs disabled:opacity-40 hover:bg-muted">»</button></Tooltip>
        </div>
        <div className="flex items-center gap-2">
          <Tooltip label={t("Go back to the batch list")} side="top">
            <button onClick={onBack} className="h-9 px-4 rounded-lg border border-border text-sm hover:bg-muted inline-flex items-center gap-1.5"><ArrowLeft className="h-4 w-4" /> {t("Back to batches")}</button>
          </Tooltip>
          <Tooltip label={t("Export these files — tracked in the Download Center")} side="top">
            <button onClick={sendToDownloadCenter} disabled={total === 0 || sending}
              className="h-9 px-4 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-2">
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} {t("Download CSV")} ({total})
            </button>
          </Tooltip>
        </div>
      </div>
    </>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">{label}</div>
      <div className="text-xs text-foreground truncate mt-0.5">{value}</div>
    </div>
  );
}

function fmt(d: string) {
  try { return new Date(d).toLocaleString(); } catch { return d; }
}
