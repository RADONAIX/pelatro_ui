import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Download, RefreshCw, Info } from "lucide-react";
import {
  reportService,
  exportsService,
  type ReportDetail,
  type ReportDetailRequest,
  type CreateExportPayload,
} from "@/services";
import { REPORTS, resolveReportKey } from "@/lib/reportsCatalog";
import {
  ReportFilters,
  TablePagination,
  detectColumns,
  emptyFilterState,
  type ColumnMeta,
  type FilterState,
} from "@/components/reports/ReportFilters";
import { toast } from "sonner";
import { useDownloads } from "@/lib/downloads";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";

// Debounce before a filter change hits the server, so typing in the search box
// (or rapidly toggling categories) collapses into a single query.
const FILTER_DEBOUNCE_MS = 350;

// The report's filter schema (column names + the metas that drive the filter
// controls), captured from the UNFILTERED load so the category dropdowns keep
// all their options as the user narrows the table.
interface ReportSchema {
  columns: string[];
  metas: ColumnMeta[];
}
const EMPTY_SCHEMA: ReportSchema = { columns: [], metas: [] };

function isCancel(e: unknown): boolean {
  const err = e as { code?: string; name?: string } | null;
  return err?.code === "ERR_CANCELED" || err?.name === "CanceledError";
}

export const Route = createFileRoute("/reports")({
  // The selected report is driven by a `?report=<key>` search param so the
  // sidebar report catalog can deep-link straight to a report's table.
  validateSearch: (search: Record<string, unknown>): { report?: string } => ({
    report: typeof search.report === "string" ? search.report : undefined,
  }),
  component: ReportsPage,
});

function ReportsPage() {
  const t = useT();
  const navigate = useNavigate();
  const downloads = useDownloads();
  const { report } = Route.useSearch();
  const selected = resolveReportKey(report);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  // Filter schema from the unfiltered load — drives the filter controls and the
  // index→column-name mapping used to build the server request.
  const [schema, setSchema] = useState<ReportSchema>(EMPTY_SCHEMA);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [filters, setFilters] = useState<FilterState>(emptyFilterState);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // Timestamp of the most recent successful fetch, shown in the Live badge.
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Translate the index-based on-screen FilterState into the backend's
  // name-based request. The server applies these over the FULL dataset and
  // returns the last N matching rows — so the preview reflects every match, not
  // just what happened to be in an on-screen sample.
  const body = useMemo<ReportDetailRequest>(() => {
    const categories: Record<string, string[]> = {};
    for (const m of schema.metas) {
      if (m.kind === "category") {
        const vals = filters.values[m.index];
        if (vals?.length && schema.columns[m.index]) categories[schema.columns[m.index]] = vals;
      }
    }
    const dateColumn = filters.dateCol != null ? schema.columns[filters.dateCol] : undefined;
    return {
      dateFrom: filters.from ? filters.from.slice(0, 10) : undefined, // "YYYY-MM-DD"
      dateTo: filters.to ? filters.to.slice(0, 10) : undefined,
      filters: { search: filters.search.trim() || undefined, categories, dateColumn },
    };
  }, [filters, schema]);

  const filtered = useMemo(
    () =>
      !!(
        body.dateFrom ||
        body.dateTo ||
        body.filters.search ||
        Object.keys(body.filters.categories).length
      ),
    [body],
  );

  // The fetch effect keys off this STRING, not the `body` object: capturing the
  // filter schema after an unfiltered load rebuilds `body` into a new (but
  // value-identical) object, and depending on the object reference would refetch
  // in a loop. The live body is read from a ref so the effect need not depend on
  // it. requestKey only changes when the *content* of the request changes.
  const requestKey = useMemo(() => JSON.stringify(body), [body]);
  const bodyRef = useRef(body);
  bodyRef.current = body;

  // Reset everything when the report changes: clear the table + schema so the
  // first fetch for the new report is unfiltered and re-derives the filter
  // options. (Runs before the fetch effect below sees the new `selected`.)
  useEffect(() => {
    setFilters(emptyFilterState);
    setSchema(EMPTY_SCHEMA);
    setDetail(null);
  }, [selected]);

  // The main fetch: (re)load the report whenever the report or the filters
  // change. Filter changes are debounced; every request is abortable so a slow
  // earlier response can't overwrite a newer one. The unfiltered load also
  // captures the filter schema (stable options for the dropdowns).
  useEffect(() => {
    if (!selected) return;
    const ctrl = new AbortController();
    setLoading(true);
    const timer = setTimeout(
      () => {
        reportService
          .detail(selected, bodyRef.current, ctrl.signal)
          .then((d) => {
            setDetail(d);
            setLastUpdated(new Date());
            setLoading(false);
            if (!filtered) setSchema({ columns: d.columns, metas: detectColumns(d.columns, d.rows) });
          })
          .catch((e) => {
            if (!isCancel(e)) setLoading(false);
          });
      },
      filtered ? FILTER_DEBOUNCE_MS : 0,
    );
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [selected, requestKey, filtered]);

  // No background polling: the open report re-queries only on a filter change or
  // the manual Refresh button, so we never hit the DB on a timer.

  // Manual refresh: re-query the open report (current filters) without clearing
  // the current table.
  const onReload = () => {
    if (!selected) return;
    setLoading(true);
    reportService
      .detail(selected, body)
      .then((d) => {
        setDetail(d);
        setLastUpdated(new Date());
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  // The server has already filtered; the on-screen table only sorts + paginates.
  const rows = detail?.rows ?? [];
  const metas = schema.metas;

  // Column sorting — sort by the clicked column's index over the returned rows.
  const { sortKey, sortDir, onSort } = useSort();
  const sortedRows = useMemo(
    () => sortRows(rows as unknown[][], sortKey, sortDir, (row, key) => row[Number(key)]),
    [rows, sortKey, sortDir],
  );

  // Back to page 1 on a real change (report/filters/page size) — not on a
  // silent data refresh, so the user keeps their page.
  useEffect(() => {
    setPage(1);
  }, [selected, requestKey, pageSize]);

  const pageCount = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageRows = sortedRows.slice((safePage - 1) * pageSize, safePage * pageSize);

  const buildExportPayload = (key: string): CreateExportPayload => ({ reportKey: key, ...body });

  const onExport = async (key: string) => {
    setExporting(true);
    try {
      await exportsService.create(buildExportPayload(key));
      downloads.refresh(); // surface the new job in the Download Center at once
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
    }
    setExporting(false);
  };

  const sel = REPORTS.find((r) => r.key === selected);
  // Prefer the backend's columns; fall back to the catalog's known schema so
  // the header row is shown even when a report returns zero rows.
  const columns = (detail && detail.columns.length > 0 ? detail.columns : sel?.columns) ?? [];
  const showTable = !!sel?.available;
  // Nothing to export when the (filtered) report has no findings.
  const noData = (detail?.count ?? sortedRows.length) === 0;

  return (
    <AppShell>
      <PageHeader
        title={t("Reports")}
        description={t("Revenue Assurance reports — pick a report from the sidebar to drill down and export.")}
        info={t("Browse certified Revenue Assurance reports and export their findings.")}
      />
      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-foreground">{sel?.title ? t(sel.title) : t("Select a report")}</span>
                {sel?.description && <InfoHint text={t(sel.description)} />}
              </div>
              {showTable && lastUpdated && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-muted-foreground">
                  {t("Updated")} {lastUpdated.toLocaleTimeString()}
                </span>
              )}
            </div>
            {detail && (
              <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                {detail.count != null && (
                  <span>
                    {detail.count.toLocaleString()} {filtered ? t("matching findings") : t("findings")}
                  </span>
                )}
                <span>· {t("showing")} {rows.length.toLocaleString()}</span>
                {loading && (
                  <span className="inline-flex items-center gap-1 text-muted-foreground/80">
                    <RefreshCw className="h-3 w-3 animate-spin" />
                    {t("updating…")}
                  </span>
                )}
              </div>
            )}
          </div>
          {showTable && (
            <div className="flex items-center gap-2">
              <Tooltip label={t("Reload this report")} side="bottom">
                <button
                  onClick={onReload}
                  disabled={loading}
                  className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-sm hover:bg-muted transition disabled:opacity-60"
                >
                  <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> {t("Refresh")}
                </button>
              </Tooltip>
              {sel?.available && (
                <Tooltip label={noData ? t("No data to export") : t("Download the report as CSV")} side="bottom">
                  <button
                    onClick={() => onExport(sel.key)}
                    disabled={exporting || noData}
                    className="inline-flex items-center gap-2 h-9 px-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Download className="h-4 w-4" /> {exporting ? t("Exporting…") : t("Export CSV")}
                  </button>
                </Tooltip>
              )}
            </div>
          )}
        </div>

        {!showTable ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            {t("This report is not available yet.")}
          </div>
        ) : (
          <>
            {/* Filters stay visible regardless of whether the report has data. */}
            <ReportFilters
              metas={metas}
              state={filters}
              onChange={setFilters}
              shown={rows.length}
              total={detail?.count ?? rows.length}
            />

            {detail?.note ? (
              /* Server says this report can't be previewed interactively (e.g.
                 too large). Show the note instead of a blank table; the Export
                 CSV button above stays available as the fallback. */
              <div className="m-4 flex items-start gap-2 rounded-xl border border-info/30 bg-info/5 px-4 py-3 text-sm text-foreground/80">
                <Info className="h-4 w-4 shrink-0 text-info mt-0.5" />
                <span>{detail.note}</span>
              </div>
            ) : (
            <>
            <div className="overflow-auto max-h-[60vh]">
              <table className="w-full text-xs">
                <thead className="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground sticky top-0 z-10">
                  <tr>{columns.map((c, ci) => (
                    <SortHeader
                      key={c}
                      label={t(c)}
                      colKey={String(ci)}
                      activeKey={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                      thClassName="text-left font-medium px-3 py-2 whitespace-nowrap"
                    />
                  ))}</tr>
                </thead>
                <tbody>
                  {pageRows.length > 0 ? (
                    pageRows.map((row, i) => (
                      <tr key={i} className="border-t border-border hover:bg-muted/30">
                        {(row as unknown[]).map((v, j) => (
                          <td key={j} className="px-3 py-2 whitespace-nowrap text-foreground/90">
                            {v == null || v === "" ? "—" : String(v)}
                          </td>
                        ))}
                      </tr>
                    ))
                  ) : (
                    <tr className="border-t border-border">
                      <td colSpan={Math.max(1, columns.length)} className="px-5 py-12 text-center text-sm text-muted-foreground">
                        {loading
                          ? t("Loading…")
                          : filtered
                            ? t("No rows match the current filters.")
                            : t("No data available for this report yet.")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination is always present, even with no rows. */}
            <TablePagination
              page={safePage}
              pageSize={pageSize}
              total={sortedRows.length}
              onPage={setPage}
              onPageSize={setPageSize}
            />
            </>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
