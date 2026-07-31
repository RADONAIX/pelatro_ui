import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { toast } from "sonner";
import { exportsService, type ExportJobRow } from "@/services";
import { REPORTS } from "@/lib/reportsCatalog";
import { useT } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Download Center — server-backed store for async export jobs. The list comes
// from GET /exports (so it PERSISTS across reloads), polled every few seconds
// while any job is active. Callers create a job via exportsService.create(...)
// then call refresh(); this provider maps the server ExportJob rows to the
// DownloadItem shape the UI renders and exposes download/stop/remove actions.
// ---------------------------------------------------------------------------

export type DownloadStatus = "queued" | "running" | "completed" | "failed" | "stopped";

const TERMINAL: DownloadStatus[] = ["completed", "failed", "stopped"];
const isTerminal = (s: DownloadStatus) => TERMINAL.includes(s);
const isActive = (s: DownloadStatus) => s === "running" || s === "queued";

// Server status (Queued/Running/Completed/Failed/Cancelled) → UI status.
const STATUS_MAP: Record<string, DownloadStatus> = {
  Queued: "queued",
  Running: "running",
  Completed: "completed",
  Failed: "failed",
  Cancelled: "stopped",
};

export interface DownloadItem {
  id: string;
  requestId: string; // human-facing id, e.g. EXP-8F3A2C9B12
  report: string; // report title
  filters: string[]; // applied-filter chips
  requestedBy: string;
  status: DownloadStatus;
  progress: number; // 0..100; -1 = indeterminate (queued / running-with-unknown-total)
  requestedAt: number;
  finishedAt?: number;
  rows?: number;
  rowsPartial?: boolean; // true while streaming with an unknown total → show "N so far"
  sizeBytes?: number;
  fileName: string; // suggested filename for download
  error?: string;
}

interface DownloadsValue {
  items: DownloadItem[];
  activeCount: number;
  exportsEnabled: boolean; // false when the server reports bulk exports are off (503)
  refresh: () => Promise<void>;
  download: (item: DownloadItem) => Promise<void>;
  stop: (id: string) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
}

const DownloadsContext = createContext<DownloadsValue | null>(null);

// Prefer the catalog title; for stream-driven keys not in the catalog (e.g.
// pipeline_batches, or a newly-added ccn_reconciliation), fall back to a
// Title-Cased version of the key rather than showing raw snake_case.
const titleize = (key: string) =>
  key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const reportTitle = (key: string) => REPORTS.find((r) => r.key === key)?.title ?? titleize(key);

// Build human-readable filter chips from the persisted job params.
function chipsFromParams(params?: ExportJobRow["params"]): string[] {
  if (!params) return ["All"];
  const chips: string[] = [];
  if (params.date_from && params.date_to) chips.push(`${params.date_from} → ${params.date_to}`);
  const f = params.filters;
  if (f?.search) chips.push(`search: "${f.search}"`);
  for (const [col, vals] of Object.entries(f?.categories ?? {})) {
    if (vals?.length) chips.push(`${col}: ${vals.join(", ")}`);
  }
  return chips.length ? chips : ["All"];
}

// totalRows may be null (a Postgres count too slow to run is skipped so
// streaming isn't blocked). When it's unknown and the job is running we can't
// compute a %, so the progress bar goes indeterminate and the row surfaces the
// live processed-row count ("N rows so far") instead of a percentage.
function jobProgress(status: DownloadStatus, pct: number, totalKnown: boolean): number {
  if (status === "running") return totalKnown ? pct : -1; // indeterminate when total unknown
  if (status === "queued") return -1; // indeterminate — waiting for a worker
  return 100;
}

function mapJob(j: ExportJobRow): DownloadItem {
  const status = STATUS_MAP[j.status] ?? "queued";
  const totalKnown = j.totalRows != null;
  const running = status === "running";
  return {
    id: j.id,
    requestId: j.reference,
    report: reportTitle(j.reportKey),
    filters: chipsFromParams(j.params),
    requestedBy: j.requestedBy ?? "—",
    status,
    progress: jobProgress(status, j.progressPct, totalKnown),
    requestedAt: new Date(j.createdAt).getTime(),
    finishedAt: j.completedAt ? new Date(j.completedAt).getTime() : undefined,
    // While running, always show the live processed count (it climbs as rows
    // stream); once finished, prefer the processed count, then the known total.
    rows: running ? j.processedRows : (j.processedRows || j.totalRows || undefined),
    rowsPartial: running && !totalKnown,
    sizeBytes: j.fileSizeBytes ?? undefined,
    fileName: `${j.reference}.csv.gz`,
    error: j.error ?? undefined,
  };
}

export function DownloadsProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const [items, setItems] = useState<DownloadItem[]>([]);
  // Bulk exports can be turned off server-side; a 503 (exports_disabled) on the
  // list route is the "feature off" signal — hide/disable the Download Center.
  const [exportsEnabled, setExportsEnabled] = useState(true);
  // Client-side hidden ids (no server hard-delete; rows auto-purge on expiry).
  const hidden = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const jobs = await exportsService.list();
      setExportsEnabled(true);
      setItems(jobs.map(mapJob).filter((it) => !hidden.current.has(it.id)));
    } catch (e: any) {
      // 503 = feature turned off → hide the Download Center and clear the list.
      // Any other (transient) error: keep the last-known list, don't wipe it.
      if (e?.response?.status === 503) {
        setExportsEnabled(false);
        setItems([]);
      }
    }
  }, []);

  // Initial load (persisted jobs survive reload).
  useEffect(() => {
    refresh();
  }, [refresh]);

  const activeCount = items.reduce((n, it) => (isActive(it.status) ? n + 1 : n), 0);

  // Poll only while something is queued/running; stop when all are terminal.
  useEffect(() => {
    if (activeCount === 0) return;
    const iv = window.setInterval(refresh, 4000);
    return () => window.clearInterval(iv);
  }, [activeCount, refresh]);

  // Download is enabled only for Completed jobs, but the file can still be
  // unavailable by the time the click lands: 409 = not ready yet, 410 =
  // expired/purged. Surface a message and refresh so a stale row corrects
  // itself (an expired job drops off the list on the next poll).
  const download = useCallback(
    async (item: DownloadItem) => {
      try {
        await exportsService.download(item.id, item.requestId);
      } catch (e: any) {
        const status = e?.response?.status;
        if (e?.code === "empty_download")
          toast.error(t("The server returned an empty file — the download service isn't configured. Contact your administrator."));
        else if (status === 409) toast.error(t("This export isn't ready to download yet."));
        else if (status === 410) toast.error(t("This export has expired and is no longer available."));
        else toast.error(t("Download failed. Please try again."));
        refresh();
      }
    },
    [refresh, t],
  );

  const stop = useCallback(
    (id: string) => {
      exportsService
        .cancel(id)
        .catch(() => {})
        .finally(() => refresh());
    },
    [refresh],
  );

  // Permanently delete on the server, then optimistically drop from the list.
  // `hidden` guards against a concurrent poll re-adding it before the DELETE lands.
  // Permanently delete on the server, then optimistically drop from the list.
  // `hidden` guards against a concurrent poll re-adding it before the DELETE lands.
  const remove = useCallback(
    (id: string) => {
      hidden.current.add(id);
      setItems((prev) => prev.filter((it) => it.id !== id));
      exportsService.remove(id).catch(() => {}).finally(() => refresh());
    },
    [refresh],
  );

  const clearFinished = useCallback(() => {
    setItems((prev) => {
      prev.forEach((it) => {
        if (isTerminal(it.status)) {
          hidden.current.add(it.id);
          exportsService.remove(it.id).catch(() => {});
        }
      });
      return prev.filter((it) => !isTerminal(it.status));
    });
  }, []);

  const value = useMemo(
    () => ({ items, activeCount, exportsEnabled, refresh, download, stop, remove, clearFinished }),
    [items, activeCount, exportsEnabled, refresh, download, stop, remove, clearFinished],
  );
  return <DownloadsContext.Provider value={value}>{children}</DownloadsContext.Provider>;
}

export function useDownloads(): DownloadsValue {
  const ctx = useContext(DownloadsContext);
  if (!ctx) throw new Error("useDownloads must be used within a DownloadsProvider");
  return ctx;
}

/** Human-readable byte size for the download list. */
export function formatBytes(n?: number): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
