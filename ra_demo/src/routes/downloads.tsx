import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { format } from "date-fns";
import { SlidersHorizontal, X, Download, Trash2, Ban, CircleSlash2, Info, Search } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { TablePagination } from "@/components/reports/ReportFilters";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { useDownloads, formatBytes, type DownloadItem, type DownloadStatus } from "@/lib/downloads";
import { ExportDetailModal } from "@/components/downloads/ExportDetailModal";
import { ConfirmDialog } from "@/components/ui-kit/ConfirmDialog";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/downloads")({
  component: DownloadsPage,
});

const STATUS_STYLE: Record<DownloadStatus, { label: string; dot: string; text: string }> = {
  completed: { label: "Completed", dot: "bg-success", text: "text-success" },
  running: { label: "Running", dot: "bg-primary", text: "text-primary" },
  queued: { label: "Queued", dot: "bg-warning", text: "text-warning" },
  failed: { label: "Failed", dot: "bg-destructive", text: "text-destructive" },
  stopped: { label: "Stopped", dot: "bg-muted-foreground", text: "text-muted-foreground" },
};

function DownloadsPage() {
  const t = useT();
  const { items, stop, remove, download, refresh, exportsEnabled } = useDownloads();

  // Pull the latest server list whenever the page is opened.
  useEffect(() => { refresh(); }, [refresh]);

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | DownloadStatus>("all");
  const [detailItem, setDetailItem] = useState<DownloadItem | null>(null);
  // Pending destructive action awaiting confirmation (delete a finished job, or
  // stop a running one).
  const [confirm, setConfirm] = useState<{ type: "delete" | "cancel"; item: DownloadItem } | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const active = query.trim() !== "" || statusFilter !== "all";
  const clear = () => {
    setQuery("");
    setStatusFilter("all");
  };

  // Global search across every visible field, plus the status filter.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      if (statusFilter !== "all" && it.status !== statusFilter) return false;
      if (!q) return true;
      const haystack = [
        it.requestId, it.report, it.requestedBy,
        STATUS_STYLE[it.status]?.label,
        format(new Date(it.requestedAt), "dd MMM yyyy, hh:mm a"),
        it.rows != null ? String(it.rows) : "",
        it.sizeBytes != null ? formatBytes(it.sizeBytes) : "",
        it.filters.join(" "),
      ];
      return haystack.some((v) => String(v ?? "").toLowerCase().includes(q));
    });
  }, [items, query, statusFilter]);

  // Jump back to page 1 whenever the filters change.
  useEffect(() => { setPage(1); }, [query, statusFilter, pageSize]);

  // Column sorting.
  const { sortKey, sortDir, onSort } = useSort();
  const sorted = useMemo(
    () => sortRows(filtered, sortKey, sortDir, (it, key) => {
      switch (key) {
        case "requestId": return it.requestId;
        case "report": return it.report;
        case "filters": return it.filters.join(", ");
        case "requestedBy": return it.requestedBy;
        case "requestedAt": return it.requestedAt;
        case "status": return it.status;
        case "progress": return it.status === "completed" ? 100 : it.progress;
        case "rows": return it.rows ?? -1;
        case "sizeBytes": return it.sizeBytes ?? -1;
        default: return "";
      }
    }),
    [filtered, sortKey, sortDir],
  );

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageItems = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  const cols: { key: string; label: string; sortable?: boolean; justify?: "start" | "center" }[] = [
    { key: "requestId", label: "Request ID", sortable: true },
    { key: "report", label: "Report", sortable: true },
    // { key: "filters", label: "Filters", sortable: true },
    { key: "requestedBy", label: "Requested By", sortable: true },
    { key: "requestedAt", label: "Requested At", sortable: true },
    { key: "status", label: "Status", sortable: true },
    { key: "progress", label: "Progress", sortable: true },
    { key: "rows", label: "Rows", sortable: true },
    { key: "sizeBytes", label: "File Size", sortable: true },
    { key: "actions", label: "Actions" },
  ];

  if (!exportsEnabled) {
    return (
      <AppShell>
        <PageHeader
          title={t("Download Center")}
          description={t("Track every report export — monitor progress, stop running jobs, and re-download completed files.")}
          info={t("Exports you start from the Reports module appear here with their live status.")}
        />
        <div className="bg-card border border-border rounded-xl shadow-sm px-5 py-14 text-center text-sm text-muted-foreground">
          {t("Bulk exports are currently disabled. Contact your administrator to enable the Download Center.")}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      {/* Bounded to the viewport (100vh − header 4rem − main padding 3rem) so the
          table body scrolls internally and the pagination stays pinned. */}
      <div className="flex flex-col h-[calc(100vh-7rem)]">
        <div className="shrink-0">
          <PageHeader
            title={t("Download Center")}
            description={t("Track every report export — monitor progress, stop running jobs, and re-download completed files.")}
            info={t("Exports you start from the Reports module appear here with their live status.")}
          />

          <div className="mb-4 flex items-center gap-2 rounded-xl border border-info/30 bg-info/5 px-4 py-2.5 text-sm text-foreground/80">
            <Info className="h-4 w-4 shrink-0 text-info" />
            {t("Exports are available to download for 48 hours after they're generated, then automatically removed.")}
          </div>
        </div>

        <div className="bg-card border border-border rounded-xl shadow-sm flex flex-col min-h-0 flex-1 overflow-hidden">
        {/* Filter bar (fixed) */}
        <div className="shrink-0 px-5 py-3 border-b border-border bg-muted/20 flex flex-wrap items-end gap-3">
          <div className="inline-flex items-center gap-1.5 self-center shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mr-1">
            <SlidersHorizontal className="h-3.5 w-3.5" /> {t("Filters")}
            <InfoHint text={t("Search across all columns, or filter by status.")} />
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-1">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("Search")}</span>
              <InfoHint text={t("Filter downloads across every column — request ID, report, requester, status, rows, size and applied filters.")} />
            </div>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search all columns…")}
                maxLength={100}
                className="h-9 w-64 pl-8 pr-8 rounded-lg border border-border bg-background text-sm focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition"
              />
              {query && (
                <button type="button" onClick={() => setQuery("")} aria-label={t("Clear")} className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 inline-flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition">
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-1">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("Status")}</span>
              <InfoHint text={t("Filter downloads by their status.")} />
            </div>
            <Select
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as typeof statusFilter)}
              minWidth={140}
              ariaLabel={t("Status")}
              options={[
                { value: "all", label: t("All status") },
                ...(Object.keys(STATUS_STYLE) as DownloadStatus[]).map((s) => ({ value: s, label: t(STATUS_STYLE[s].label) })),
              ]}
            />
          </div>


          <button
            onClick={clear}
            disabled={!active}
            className="ml-auto shrink-0 inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-border bg-card text-xs font-medium hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
          >
            <X className="h-3.5 w-3.5" /> {t("Clear filters")}
          </button>
        </div>

        {/* Table body — scrolls; the header row stays pinned at the top. */}
        <div className="flex-1 min-h-0 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-20 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                {cols.map((c) =>
                  c.sortable ? (
                    <SortHeader
                      key={c.key}
                      label={t(c.label)}
                      colKey={c.key}
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                      thClassName="text-left font-medium px-4 py-2.5 whitespace-nowrap"
                    />
                  ) : (
                    <th key={c.key} className={`text-left font-medium px-4 py-2.5 whitespace-nowrap ${c.key === "actions" ? "sticky top-0 right-0 z-30 bg-muted shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.12)]" : ""}`}>{t(c.label)}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {pageItems.length > 0 ? (
                pageItems.map((it) => (
                  <DownloadRow key={it.id} item={it} onStop={() => setConfirm({ type: "cancel", item: it })} onRemove={() => setConfirm({ type: "delete", item: it })} onDownload={() => download(it)} onDetails={() => setDetailItem(it)} />
                ))
              ) : (
                <tr>
                  <td colSpan={cols.length} className="px-5 py-14 text-center text-sm text-muted-foreground">
                    {items.length === 0
                      ? t("No downloads yet. Export a report to see it here.")
                      : t("No downloads match the current filters.")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination — fixed at the card bottom, always visible. */}
        <div className="shrink-0">
          <TablePagination
            page={safePage}
            pageSize={pageSize}
            total={filtered.length}
            onPage={setPage}
            onPageSize={setPageSize}
          />
        </div>
      </div>
      </div>

      {detailItem && (
        <ExportDetailModal
          item={detailItem}
          onClose={() => setDetailItem(null)}
          onDownload={() => download(detailItem)}
        />
      )}

      <ConfirmDialog
        open={!!confirm}
        tone="danger"
        icon={confirm?.type === "delete" ? <Trash2 className="h-5 w-5" /> : <Ban className="h-5 w-5" />}
        confirmIcon={confirm?.type === "delete" ? <Trash2 className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
        title={confirm?.type === "delete" ? t("Delete export") : t("Stop export")}
        message={
          confirm?.type === "delete"
            ? t("This permanently removes this export and its file from the Download Center.")
            : t("This stops the export while it's still running. Any progress is discarded.")
        }
        note={confirm ? confirm.item.requestId : undefined}
        confirmLabel={confirm?.type === "delete" ? t("Delete") : t("Stop export")}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm) (confirm.type === "delete" ? remove : stop)(confirm.item.id);
          setConfirm(null);
        }}
      />
    </AppShell>
  );
}

function DownloadRow({ item, onStop, onRemove, onDownload, onDetails }: { item: DownloadItem; onStop: () => void; onRemove: () => void; onDownload: () => void; onDetails: () => void }) {
  const t = useT();
  const s = STATUS_STYLE[item.status];
  const isActive = item.status === "running" || item.status === "queued";
  const iconBtn =
    "h-8 w-8 inline-flex items-center justify-center rounded-lg border border-border bg-card hover:bg-muted transition disabled:opacity-40 disabled:cursor-not-allowed";

  return (
    <tr className="border-t border-border hover:bg-muted/30 align-middle">
      <td className="px-4 py-3 whitespace-nowrap font-medium text-foreground">{item.requestId}</td>
      <td className="px-4 py-3 whitespace-nowrap text-foreground/90">{item.report}</td>
      <td className="px-4 py-3 whitespace-nowrap text-foreground/90">{item.requestedBy}</td>
      <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">{format(new Date(item.requestedAt), "dd MMM yyyy, hh:mm a")}</td>
      <td className="px-4 py-3 whitespace-nowrap">
        <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${s.text}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} /> {t(s.label)}
        </span>
      </td>
      <td className="px-4 py-3 whitespace-nowrap min-w-[90px]">
        {isActive ? (
          <div>
            <div className="text-[11px] text-muted-foreground mb-0.5">{item.progress < 0 ? "" : `${item.progress}%`}</div>
            <div className="h-1 w-16 rounded-full bg-muted overflow-hidden">
              {item.progress < 0 ? (
                <div className="h-full w-1/3 rounded-full bg-primary animate-pulse" />
              ) : (
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${item.progress}%` }} />
              )}
            </div>
          </div>
        ) : item.status === "completed" ? (
          <div>
            <div className="text-[11px] text-muted-foreground mb-0.5">100%</div>
            <div className="h-1 w-16 rounded-full bg-success" />
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-3 whitespace-nowrap text-foreground/90">
        {item.rows != null ? `${item.rows.toLocaleString()}${item.rowsPartial ? ` ${t("so far")}` : ""}` : "—"}
      </td>
      <td className="px-4 py-3 whitespace-nowrap text-foreground/90">{item.sizeBytes != null ? formatBytes(item.sizeBytes) : "—"}</td>
      <td className="px-4 py-3 whitespace-nowrap sticky right-0 z-10 bg-card shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.12)]">
        <div className="flex items-center gap-1.5">
          <Tooltip label={t("View details")} side="top">
            <button onClick={onDetails} className={iconBtn} aria-label={t("View details")}>
              <Info className="h-4 w-4 text-muted-foreground" />
            </button>
          </Tooltip>

          {item.status === "completed" && (
            <Tooltip label={t("Download file")} side="top">
              <button
                onClick={onDownload}
                className={iconBtn}
                aria-label={t("Download file")}
              >
                <Download className="h-4 w-4 text-foreground" />
              </button>
            </Tooltip>
          )}

          {isActive && (
            <Tooltip label={t("Stop download")} side="top">
              <button onClick={onStop} className={`${iconBtn} text-destructive`} aria-label={t("Stop download")}>
                <Ban className="h-4 w-4" />
              </button>
            </Tooltip>
          )}

          {item.status === "stopped" && (
            <Tooltip label={t("Download stopped")} side="top">
              <span className={`${iconBtn} cursor-default text-muted-foreground`} aria-label={t("Download stopped")}>
                <CircleSlash2 className="h-4 w-4" />
              </span>
            </Tooltip>
          )}

          {/* Delete is available on any finished job (completed / stopped / failed). */}
          {!isActive && (
            <Tooltip label={t("Delete")} side="top">
              <button onClick={onRemove} className={`${iconBtn} hover:text-destructive`} aria-label={t("Delete")}>
                <Trash2 className="h-4 w-4" />
              </button>
            </Tooltip>
          )}
        </div>
      </td>
    </tr>
  );
}
