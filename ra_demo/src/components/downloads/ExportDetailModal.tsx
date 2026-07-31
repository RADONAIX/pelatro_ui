import { useEffect, useState } from "react";
import { format } from "date-fns";
import { Download, Copy, Check, AlertCircle, Loader2, ShieldCheck } from "lucide-react";
import { Modal } from "@/routes/roles";
import { exportsService, type ExportJobDetail } from "@/services";
import { formatBytes, type DownloadItem } from "@/lib/downloads";
import { useT } from "@/lib/i18n";

const fmtDate = (v?: string | null) => (v ? format(new Date(v), "dd MMM yyyy, hh:mm a") : "—");

/**
 * Detail modal for a Download Center job. Fetches GET /exports/{id} to surface
 * the fields the export worker records on completion — summary KPIs, SHA-256
 * checksum (integrity) and file format — alongside the job's metadata.
 */
export function ExportDetailModal({
  item,
  onClose,
  onDownload,
}: {
  item: DownloadItem;
  onClose: () => void;
  onDownload: () => void;
}) {
  const t = useT();
  const [detail, setDetail] = useState<ExportJobDetail | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    exportsService
      .get(item.id)
      .then((d) => alive && setDetail(d))
      .catch((e: any) => alive && setError(e?.response?.data?.error?.message ?? t("Could not load export details.")));
    return () => {
      alive = false;
    };
  }, [item.id, t]);

  const copyChecksum = async () => {
    if (!detail?.checksumSha256) return;
    try {
      await navigator.clipboard.writeText(detail.checksumSha256);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  const kpis = detail?.kpis && Object.keys(detail.kpis).length > 0 ? detail.kpis : null;
  const rowsLabel =
    detail?.processedRows != null && detail?.totalRows != null && detail.totalRows > 0
      ? `${detail.processedRows.toLocaleString()} / ${detail.totalRows.toLocaleString()}`
      : item.rows != null ? item.rows.toLocaleString() : "—";

  return (
    <Modal
      title={item.requestId}
      subtitle={item.report}
      icon={<ShieldCheck className="h-5 w-5" />}
      onClose={onClose}
    >
      <div className="space-y-5">
        {error ? (
          <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm font-medium text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" /> {error}
          </div>
        ) : !detail ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("Loading details…")}
          </div>
        ) : (
          <>
            {/* Metadata grid */}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <Row label={t("Status")} value={detail.status} />
              <Row label={t("Requested by")} value={detail.requestedBy ?? "—"} />
              <Row label={t("Requested at")} value={fmtDate(detail.createdAt)} />
              <Row label={t("Started")} value={fmtDate(detail.startedAt)} />
              <Row label={t("Completed")} value={fmtDate(detail.completedAt)} />
              <Row label={t("Expires")} value={fmtDate(detail.expiresAt)} />
              <Row label={t("Rows")} value={rowsLabel} />
              <Row label={t("File size")} value={detail.fileSizeBytes != null ? formatBytes(detail.fileSizeBytes) : "—"} />
              <Row label={t("Format")} value={detail.fileFormat ?? "—"} />
              <Row label={t("Progress")} value={`${detail.progressPct}%`} />
            </dl>

            {/* Applied filters */}
            {item.filters.length > 0 && (
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">{t("Filters")}</div>
                <div className="flex flex-wrap gap-1">
                  {item.filters.map((f, i) => (
                    <span key={i} className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{f}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Failure reason */}
            {detail.error && (
              <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm font-medium text-destructive">
                <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span className="min-w-0 break-words">{detail.error}</span>
              </div>
            )}

            {/* Download action (completed only) */}
            {detail.status === "Completed" && (
              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  onClick={onDownload}
                  className="inline-flex items-center gap-2 h-10 px-4 rounded-xl bg-primary text-primary-foreground text-sm font-semibold hover:bg-primary/90"
                >
                  <Download className="h-4 w-4" /> {t("Download file")}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="text-foreground/90 truncate">{value}</dd>
    </div>
  );
}
