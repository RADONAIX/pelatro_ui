import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Play, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  RECON_STATUSES,
  fetchReconExecutions,
  fetchReconPage,
  runReconNow,
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
  RAW_MISSING: "border-warning/40 bg-warning/15 text-warning-foreground",
  PROCESSED_MISSING: "border-info/40 bg-info/10 text-info",
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
  const [status, setStatus] = useState<ReconStatus | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchReconPage(reportKey, {
        status,
        limit: PAGE_SIZE,
        offset,
      });
      setPage(next);
      setError(null);
      if (next.ruleId) setExecutions(await fetchReconExecutions(next.ruleId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [reportKey, status, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // A different report, or a different filter, starts at the first page —
  // otherwise page 4 of one report opens page 4 of the next.
  useEffect(() => {
    setOffset(0);
  }, [reportKey, status]);

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
                Raw missing <span className="tabular text-foreground">{latest.rows_raw_missing.toLocaleString()}</span>
              </span>
              <span>
                Processed missing{" "}
                <span className="tabular text-foreground">{latest.rows_processed_missing.toLocaleString()}</span>
              </span>
              {latest.duration_ms != null && <span>{latest.duration_ms} ms</span>}
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
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
        {page && (
          <span className="ml-auto text-xs text-muted-foreground">
            {page.total.toLocaleString()} row{page.total === 1 ? "" : "s"}
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
                  No rows for this filter.
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
