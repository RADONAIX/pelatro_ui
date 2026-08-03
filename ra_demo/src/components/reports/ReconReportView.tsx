import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Download, Play, RefreshCw, Search, X } from "lucide-react";
import { toast } from "sonner";
import {
  RECON_STATUSES,
  fetchExecutionReport,
  fetchReconExecutions,
  fetchReconPage,
  downloadReport,
  reportFileName,
  runReconNow,
  type ExecutionSummary,
  type ReconExecution,
  type ReconPage,
  type ReconStatus,
} from "@/lib/assurance/reconciliation-api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// A generated reconciliation report.
//
// The table renders exactly the columns the server returns — the author's
// comparison keys, their comparison metrics, and the computed status. Both
// sides of every pair keep their own column, so a processed amount and a raw
// amount sit side by side and the origin of each value stays obvious.
//
// Columns are NOT declared here: they come from the rule, so a rule comparing
// three metrics renders six value columns and one comparing one renders two.
// ---------------------------------------------------------------------------

const PAGE_SIZE = 25;

const STATUS_TONE: Record<ReconStatus, string> = {
  // Reconciliation outcomes.
  MATCH: "border-success/40 bg-success/10 text-success",
  MISMATCH: "border-destructive/40 bg-destructive/10 text-destructive",
  TABLE1_MISSING: "border-info/40 bg-info/10 text-info",
  TABLE2_MISSING: "border-warning/40 bg-warning/15 text-warning-foreground",
  // Sequence outcomes. A gap is the finding to act on, so it takes the
  // destructive tone that MISMATCH has on the other kind.
  PRESENT: "border-success/40 bg-success/10 text-success",
  GAP: "border-destructive/40 bg-destructive/10 text-destructive",
  DUPLICATE: "border-warning/40 bg-warning/15 text-warning-foreground",
};

function StatusPill({ value }: { value: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide",
        STATUS_TONE[value as ReconStatus] ?? "border-border bg-muted text-muted-foreground",
      )}
    >
      {value}
    </span>
  );
}

/** Column labels: the raw column name is the honest label — it is what the
 *  author picked and what exists in the source table. Underscores are spaced
 *  out for reading, nothing is renamed. */
function label(column: string): string {
  return column.replace(/_/g, " ");
}

function cell(value: unknown) {
  if (value === null || value === undefined) {
    // A NULL here is meaningful — it is the missing side of an unmatched row —
    // so it is rendered explicitly rather than as an empty cell.
    return <span className="font-mono text-[11px] text-muted-foreground/70">NULL</span>;
  }
  if (typeof value === "number") {
    return <span className="tabular">{value.toLocaleString()}</span>;
  }
  return <span>{String(value)}</span>;
}

export function ReconReportView({ reportKey }: { reportKey: string }) {
  const [page, setPage] = useState<ReconPage | null>(null);
  const [executions, setExecutions] = useState<ReconExecution[]>([]);
  const [summary, setSummary] = useState<ExecutionSummary | null>(null);
  const [status, setStatus] = useState<ReconStatus | null>(null);
  // What is typed, and what has been sent. Split so each keystroke does not
  // become a query against a table that can hold millions of rows.
  const [term, setTerm] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchReconPage(reportKey, {
        status,
        search,
        limit: PAGE_SIZE,
        offset,
      });
      setPage(next);
      setError(null);
      if (next.ruleId) setExecutions(await fetchReconExecutions(next.ruleId));
      // The stored report carries figures the row page cannot: how many rows
      // were SCANNED to produce it, and whether a case came out of it.
      if (next.executionId) {
        try {
          setSummary(await fetchExecutionReport(next.executionId, { limit: 0, offset: 0 }));
        } catch {
          // A missing report row for an older execution is not worth failing
          // the view over — the table below is still correct.
          setSummary(null);
        }
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [reportKey, status, search, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // A different report, or a different filter, starts at the first page —
  // otherwise page 4 of one report opens page 4 of the next.
  useEffect(() => {
    setOffset(0);
  }, [reportKey, status, search]);

  // Debounced: typing "MISMATCH" is eight keystrokes and should be one query.
  useEffect(() => {
    const id = setTimeout(() => setSearch(term.trim()), 300);
    return () => clearTimeout(id);
  }, [term]);

  // A new report clears the previous one's search, which would otherwise carry
  // over and show an empty table for a term from a different rule.
  useEffect(() => {
    setTerm("");
    setSearch("");
  }, [reportKey]);

  // The status vocabulary depends on the rule's kind, so it is taken from the
  // page rather than assumed; the reconciliation four are the fallback for a
  // report served before the server carried them.
  const statuses = (page?.statuses ?? RECON_STATUSES) as readonly ReconStatus[];

  const latest = executions[0];
  const columns = page?.columns ?? [];
  const statusIndex = columns.indexOf("status");
  const valueColumns = useMemo(
    () => columns.filter((c) => c !== "status"),
    [columns],
  );

  const run = async () => {
    if (!page?.ruleId || running) return;
    setRunning(true);
    try {
      const result = await runReconNow(page.ruleId);
      toast.success("Reconciliation complete", {
        description: `${result.counts.total?.toLocaleString() ?? 0} rows in ${result.durationMs} ms`,
      });
      setOffset(0);
      await load();
    } catch (e) {
      toast.error("Reconciliation failed", { description: (e as Error).message });
    } finally {
      setRunning(false);
    }
  };

  const download = async (executionId: string, fmt: "csv" | "excel") => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadReport(
        executionId,
        fmt,
        { status, search },
        // Named from what this screen already knows, so the file is still
        // "<report name>_<date_time>" if the server's header does not survive
        // the trip.
        reportFileName(
          summary?.ruleName || page?.title || "report",
          summary?.executionStart ?? page?.executedAt,
          fmt,
        ),
      );
    } catch (e) {
      // Silence here would look identical to a browser that blocked the save,
      // so the failure is stated.
      toast.error("Download failed", { description: (e as Error).message });
    } finally {
      setDownloading(false);
    }
  };

  const totalPages = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;
  const current = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-4">
      {/* Execution headline — what ran, when, and what it found. */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
          <span>
            Last run{" "}
            <span className="text-foreground">
              {page?.executedAt ? new Date(page.executedAt).toLocaleString() : "never"}
            </span>
          </span>
          {latest && (
            <>
              <span>
                Total <span className="tabular text-foreground">{latest.rows_total.toLocaleString()}</span>
              </span>
              <span className="text-success">
                Match <span className="tabular">{latest.rows_match.toLocaleString()}</span>
              </span>
              <span className="text-destructive">
                Mismatch <span className="tabular">{latest.rows_mismatch.toLocaleString()}</span>
              </span>
              <span>
                Missing in Table 1{" "}
                <span className="tabular text-foreground">
                  {latest.rows_table1_missing.toLocaleString()}
                </span>
              </span>
              <span>
                Missing in Table 2{" "}
                <span className="tabular text-foreground">
                  {latest.rows_table2_missing.toLocaleString()}
                </span>
              </span>
              {latest.duration_ms != null && <span>{latest.duration_ms} ms</span>}
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* A plain link, not a fetch: the file is streamed and may be very
              large, so the browser downloads it rather than the page pulling it
              into memory first. Lives here rather than only inside the summary
              strip below, which is absent for a report whose stored row never
              got written. */}
          {page?.executionId && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5"
              disabled={downloading}
              onClick={() => void download(page.executionId!, "excel")}
            >
              <Download className="size-3.5" />
              {downloading ? "Preparing…" : "Download Excel"}
            </Button>
          )}
          <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
            Refresh
          </Button>
          <Button size="sm" className="h-8 gap-1.5" onClick={run} disabled={running || !page?.ruleId}>
            <Play className="size-3.5" />
            {running ? "Running…" : "Run now"}
          </Button>
        </div>
      </div>

      {page?.note && (
        <div className="flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-2.5 text-sm">
          <AlertTriangle className="size-4 shrink-0 text-warning" />
          {page.note}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Execution summary — what this run did, and what came of it. */}
      {summary && (
        <div className="grid gap-3 rounded-lg border border-border bg-card p-4 sm:grid-cols-3 lg:grid-cols-6">
          <Figure label="Rows scanned" value={summary.rowsScanned.toLocaleString()} />
          <Figure label="Rows returned" value={summary.rowsReturned.toLocaleString()} />
          <Figure
            label="Execution time"
            value={summary.executionStart ? new Date(summary.executionStart).toLocaleString() : "—"}
          />
          <Figure
            label="Duration"
            value={summary.durationMs != null ? `${summary.durationMs} ms` : "—"}
          />
          <Figure
            label="Case created"
            value={summary.caseCreated ? summary.caseReference || "Yes" : "No"}
            tone={summary.caseCreated ? "warn" : undefined}
          />
          <Figure
            label="Status"
            value={summary.status}
            tone={summary.status === "Succeeded" ? "good" : "warn"}
          />

          <div className="flex items-end gap-2 sm:col-span-3 lg:col-span-6">
            {/* Plain links, not fetches: the file is streamed and may be very
                large, so the browser downloads it rather than the page pulling
                it into memory first. */}
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5"
              disabled={downloading}
              onClick={() => void download(summary.executionId, "csv")}
            >
              <Download className="size-3.5" />
              Download CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5"
              disabled={downloading}
              onClick={() => void download(summary.executionId, "excel")}
            >
              <Download className="size-3.5" />
              Download Excel
            </Button>
            <span className="self-center text-xs text-muted-foreground">
              {summary.reportCount.toLocaleString()} row
              {summary.reportCount === 1 ? "" : "s"} stored ·{" "}
              <span className="font-mono">{summary.outputTable}</span>
            </span>
          </div>
        </div>
      )}

      {/* Status filter — the four outcomes, straight off the indexed column. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => setStatus(null)}
          className={cn(
            "rounded border px-2.5 py-1 text-xs transition-colors",
            status === null
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:bg-muted",
          )}
        >
          All
        </button>
        {statuses.map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={cn(
              "rounded border px-2.5 py-1 font-mono text-[11px] uppercase transition-colors",
              status === s
                ? STATUS_TONE[s]
                : "border-border text-muted-foreground hover:bg-muted",
            )}
          >
            {s}
          </button>
        ))}
        {/* Global search. Applied SERVER-side across every column of the
            report, so it filters all rows rather than the 25 on screen — the
            row count and the pager below reflect the match, not the page. */}
        <div className="relative ml-auto w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="Search all columns…"
            aria-label="Search this report"
            className="h-8 w-full rounded border border-border bg-background pl-8 pr-7 text-xs outline-none placeholder:text-muted-foreground focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
          />
          {term && (
            <button
              onClick={() => setTerm("")}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        {page && (
          <span className="text-xs text-muted-foreground">
            {page.total.toLocaleString()} row{page.total === 1 ? "" : "s"}
            {search && " matching"}
          </span>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr className="border-b border-border bg-muted/40">
              {valueColumns.map((c) => (
                <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                  {label(c)}
                </th>
              ))}
              {statusIndex >= 0 && <th className="px-3 py-2 font-medium">Status</th>}
            </tr>
          </thead>
          <tbody>
            {page?.rows.map((row, i) => (
              <tr key={i} className="border-b border-border/60 last:border-0 hover:bg-accent/40">
                {valueColumns.map((c) => (
                  <td key={c} className="whitespace-nowrap px-3 py-2">
                    {cell(row[c])}
                  </td>
                ))}
                {statusIndex >= 0 && (
                  <td className="px-3 py-2">
                    <StatusPill value={String(row.status)} />
                  </td>
                )}
              </tr>
            ))}
            {!loading && page && page.rows.length === 0 && (
              <tr>
                <td
                  colSpan={valueColumns.length + 1}
                  className="px-3 py-8 text-center text-sm text-muted-foreground"
                >
                  {search
                    ? `No rows match "${search}".`
                    : "No rows for this filter."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Server-side pagination: the table can hold millions of rows, so only a
          page is ever fetched. */}
      {page && page.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            Page {current} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={current >= totalPages || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}


/** One figure on the execution summary strip. */
function Figure({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "warn";
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p
        className={cn(
          "tabular truncate text-sm font-medium",
          tone === "good" && "text-success",
          tone === "warn" && "text-warning-foreground",
        )}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}
