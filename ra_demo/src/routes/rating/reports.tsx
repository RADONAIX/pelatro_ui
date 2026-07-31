import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useSort, sortRows, SortHeader } from "@/components/ui-kit/Sortable";
import {
  ReportFilters,
  TablePagination,
  detectColumns,
  emptyFilterState,
  applyFilters,
  hasActiveFilters,
  type ColumnMeta,
  type FilterState,
} from "@/components/reports/ReportFilters";
import { useT } from "@/lib/i18n";
import { isReconReportKey } from "@/lib/assurance/reconciliation-api";
import { ReconReportView } from "@/components/reports/ReconReportView";
import { ratingApi, ratingError } from "@/lib/rating/api";
import {
  RATING_REPORTS,
  resolveRatingReportKey,
} from "@/lib/rating/reportsCatalog";

// The rating module's Reports screen. Deliberately the same shape as the
// Mediation Assurance one — sidebar picks the report, this page renders its
// table with the same filters, sorting, pagination and CSV export — so the two
// scopes read as one product. The data comes from the rating service instead.

export const Route = createFileRoute("/rating/reports")({
  validateSearch: (search: Record<string, unknown>): { report?: string } => ({
    report: typeof search.report === "string" ? search.report : undefined,
  }),
  component: RatingReportsPage,
});

interface ReportData {
  code: string;
  name: string;
  columns: string[];
  rows: unknown[][];
  count: number;
  total: number;
}

interface ReportSchema {
  columns: string[];
  metas: ColumnMeta[];
}
const EMPTY_SCHEMA: ReportSchema = { columns: [], metas: [] };

/**
 * Under the Rating scope the sidebar's report catalog points at THIS route, so
 * a generated reconciliation report opens here rather than at /reports. Without
 * this branch its key fell through resolveRatingReportKey to the default rating
 * report, which is why the page showed "Daily Reconciliation Summary — no data"
 * instead of the reconciliation.
 *
 * Branching in a component that calls no other hooks keeps either page's hooks
 * out of a conditional.
 */
function RatingReportsPage() {
  const { report } = Route.useSearch();
  if (isReconReportKey(report)) return <ReconRatingReportPage reportKey={report!} />;
  return <RatingCatalogReportsPage />;
}

function ReconRatingReportPage({ reportKey }: { reportKey: string }) {
  const t = useT();
  return (
    <AppShell>
      <PageHeader
        title={t("Reports")}
        description={t(
          "Generated reconciliation — the comparison keys and metrics this rule selected, with the status of every record.",
        )}
        info={t("Generated automatically from a Reconciliation rule in Controls.")}
      />
      <ReconReportView reportKey={reportKey} />
    </AppShell>
  );
}

function RatingCatalogReportsPage() {
  const t = useT();
  const { report } = Route.useSearch();
  const selected = resolveRatingReportKey(report);
  const sel = RATING_REPORTS.find((r) => r.key === selected);

  const [filters, setFilters] = useState<FilterState>(emptyFilterState);
  const [schema, setSchema] = useState<ReportSchema>(EMPTY_SCHEMA);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [exporting, setExporting] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const { data, isFetching, refetch } = useQuery({
    queryKey: ["rating", "report-data", selected],
    queryFn: async () =>
      (await ratingApi.get<ReportData>(`/reports/${selected}/data`)).data,
    enabled: !!sel?.available,
  });

  // Capture the filter schema from the loaded rows, so the category dropdowns
  // keep every option as the user narrows the table.
  useEffect(() => {
    setFilters(emptyFilterState);
    setSchema(EMPTY_SCHEMA);
    setPage(1);
  }, [selected]);

  useEffect(() => {
    if (data) {
      setLastUpdated(new Date());
      setSchema((prev) =>
        prev.columns.length
          ? prev
          : {
              columns: data.columns,
              metas: detectColumns(data.columns, data.rows),
            },
      );
    }
  }, [data]);

  // Rating reports return the whole result set, so filtering happens on-screen
  // rather than as another round trip.
  const rows = useMemo(
    () => applyFilters((data?.rows ?? []) as unknown[][], filters),
    [data, filters],
  );
  const filtered = hasActiveFilters(filters);

  const { sortKey, sortDir, onSort } = useSort();
  const sortedRows = useMemo(
    () => sortRows(rows, sortKey, sortDir, (row, key) => row[Number(key)]),
    [rows, sortKey, sortDir],
  );

  useEffect(() => {
    setPage(1);
  }, [filters, pageSize]);

  const pageCount = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageRows = sortedRows.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize,
  );

  const columns =
    (data && data.columns.length > 0 ? data.columns : sel?.columns) ?? [];
  const showTable = !!sel?.available;
  const noData = sortedRows.length === 0;

  const onExport = async () => {
    if (!sel) return;
    setExporting(true);
    try {
      const { data: created } = await ratingApi.post(
        `/reports/${sel.key}/generate`,
        { format: "csv" },
      );
      const { data: blob } = await ratingApi.get(
        `/reports/generated/${created.id}/download`,
        { responseType: "blob" },
      );
      const url = URL.createObjectURL(blob as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = created.filename;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("Export ready"), {
        description: `${created.row_count.toLocaleString()} ${t("rows")}`,
      });
    } catch (e) {
      toast.error(t("Export failed"), { description: ratingError(e) });
    }
    setExporting(false);
  };

  return (
    <AppShell>
      <PageHeader
        title={t("Reports")}
        description={t(
          "Rating Assurance reports — pick a report from the sidebar to drill down and export.",
        )}
        info={t(
          "Browse certified Rating Assurance reports and export their findings.",
        )}
      />
      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-foreground">
                  {sel?.title ? t(sel.title) : t("Select a report")}
                </span>
                {sel?.description && <InfoHint text={t(sel.description)} />}
              </div>
              {showTable && lastUpdated && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-muted-foreground">
                  {t("Updated")} {lastUpdated.toLocaleTimeString()}
                </span>
              )}
            </div>
            {data && (
              <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                <span>
                  {data.total.toLocaleString()}{" "}
                  {filtered ? t("matching findings") : t("findings")}
                </span>
                <span>
                  · {t("showing")} {sortedRows.length.toLocaleString()}
                </span>
                {isFetching && (
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
                  onClick={() => void refetch()}
                  disabled={isFetching}
                  className="inline-flex items-center gap-2 h-9 px-3 rounded-lg border border-border bg-card text-sm hover:bg-muted transition disabled:opacity-60"
                >
                  <RefreshCw
                    className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
                  />{" "}
                  {t("Refresh")}
                </button>
              </Tooltip>
              <Tooltip
                label={
                  noData
                    ? t("No data to export")
                    : t("Download the report as CSV")
                }
                side="bottom"
              >
                <button
                  onClick={() => void onExport()}
                  disabled={exporting || noData}
                  className="inline-flex items-center gap-2 h-9 px-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Download className="h-4 w-4" />{" "}
                  {exporting ? t("Exporting…") : t("Export CSV")}
                </button>
              </Tooltip>
            </div>
          )}
        </div>

        {!showTable ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            {t("This report is not available yet.")}
          </div>
        ) : (
          <>
            <ReportFilters
              metas={schema.metas}
              state={filters}
              onChange={setFilters}
              shown={sortedRows.length}
              total={data?.total ?? 0}
            />

            <div className="overflow-auto max-h-[60vh]">
              <table className="w-full text-xs">
                <thead className="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground sticky top-0 z-10">
                  <tr>
                    {columns.map((c, ci) => (
                      <SortHeader
                        key={c}
                        label={t(c)}
                        colKey={String(ci)}
                        activeKey={sortKey}
                        dir={sortDir}
                        onSort={onSort}
                        thClassName="text-left font-medium px-3 py-2 whitespace-nowrap"
                      />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pageRows.length > 0 ? (
                    pageRows.map((row, i) => (
                      <tr
                        key={i}
                        className="border-t border-border hover:bg-muted/30"
                      >
                        {(row as unknown[]).map((v, j) => (
                          <td
                            key={j}
                            className="px-3 py-2 whitespace-nowrap text-foreground/90"
                          >
                            {v == null || v === "" ? "—" : String(v)}
                          </td>
                        ))}
                      </tr>
                    ))
                  ) : (
                    <tr className="border-t border-border">
                      <td
                        colSpan={Math.max(1, columns.length)}
                        className="px-5 py-12 text-center text-sm text-muted-foreground"
                      >
                        {isFetching
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

            <TablePagination
              page={safePage}
              pageSize={pageSize}
              total={sortedRows.length}
              onPage={setPage}
              onPageSize={setPageSize}
            />
          </>
        )}
      </div>
    </AppShell>
  );
}
