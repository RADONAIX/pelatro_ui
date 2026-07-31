import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { FileSearch, Search } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import {
  useExceptionMeta,
  useRatingResults,
  useRatingRuns,
} from "@/lib/rating/hooks";
import {
  fmtCount,
  fmtDate,
  money,
  statusTone,
  titleCase,
} from "@/lib/rating/format";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

// Deep links land here pre-filtered: a run detail's "Records" button passes
// run_id, an exception's affected-CDR list can pass status + product.
interface RecordsSearch {
  run_id?: string;
  status?: string;
  product_code?: string;
}

export const Route = createFileRoute("/rating/records")({
  validateSearch: (search: Record<string, unknown>): RecordsSearch => ({
    run_id: typeof search.run_id === "string" ? search.run_id : undefined,
    status: typeof search.status === "string" ? search.status : undefined,
    product_code:
      typeof search.product_code === "string" ? search.product_code : undefined,
  }),
  component: RecordsPage,
});

const PAGE_SIZE = 50;

const STATUSES = [
  "MATCHED",
  "UNDERCHARGED",
  "OVERCHARGED",
  "UNRATED",
  "ZERO_CHARGE",
  "NO_MATCHING_RULE",
  "MULTIPLE_RULE_MATCH",
  "PRODUCT_NOT_FOUND",
];

function RecordsPage() {
  const t = useT();
  const initial = Route.useSearch();

  const [runId, setRunId] = useState(initial.run_id ?? "");
  const [status, setStatus] = useState(initial.status ?? "");
  const [rootCause, setRootCause] = useState("");
  const [needle, setNeedle] = useState("");
  const [page, setPage] = useState(0);

  const { data: runs = [] } = useRatingRuns();
  const { data: meta } = useExceptionMeta();

  // One box searches both identifiers — an analyst pastes whatever they have.
  const looksLikeMsisdn = /^\+?\d{6,}$/.test(needle.trim());
  const {
    data: rows = [],
    isLoading,
    error,
    refetch,
  } = useRatingResults({
    run_id: runId || undefined,
    status: status || undefined,
    root_cause: rootCause || undefined,
    product_code: initial.product_code,
    msisdn: looksLikeMsisdn ? needle.trim() : undefined,
    cdr_id: needle.trim() && !looksLikeMsisdn ? needle.trim() : undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const resetPage =
    <T,>(setter: (v: T) => void) =>
    (v: T) => {
      setter(v);
      setPage(0);
    };

  return (
    <AppShell>
      <PageHeader
        title={t("Reconciliation Records")}
        description={t(
          "One row per rated CDR: what the rules say it should cost, what billing actually charged, and the variance between them.",
        )}
        info={t(
          "Sorted by absolute variance, so the most expensive disagreements are always on the first page.",
        )}
      />

      {/* --- Filters --------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <input
            value={needle}
            onChange={(e) => {
              setNeedle(e.target.value);
              setPage(0);
            }}
            placeholder={t("CDR ID or MSISDN")}
            className="h-9 w-56 rounded-lg border border-border bg-card pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <Select
          value={runId}
          onChange={resetPage(setRunId)}
          options={[
            { value: "", label: t("All runs") },
            ...runs.map((r) => ({
              value: r.id,
              label: `${r.id.slice(0, 8)} · ${fmtDate(r.created_at)}`,
            })),
          ]}
          minWidth={200}
          ariaLabel={t("Filter by run")}
        />
        <Select
          value={status}
          onChange={resetPage(setStatus)}
          options={[
            { value: "", label: t("All statuses") },
            ...STATUSES.map((s) => ({ value: s, label: s })),
          ]}
          minWidth={190}
          ariaLabel={t("Filter by status")}
        />
        <Select
          value={rootCause}
          onChange={resetPage(setRootCause)}
          options={[
            { value: "", label: t("All root causes") },
            ...(meta?.root_causes ?? []).map((s) => ({
              value: s,
              label: titleCase(s),
            })),
          ]}
          minWidth={190}
          ariaLabel={t("Filter by root cause")}
        />
      </div>

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {!isLoading && rows.length === 0 && page === 0 && (
        <RatingEmpty
          icon={FileSearch}
          title="No records match"
          description="Loosen the filters, or run a rating first — records appear as soon as CDRs are rated."
        />
      )}

      {rows.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-card z-10">
                <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                  <th className="px-5 py-2.5 font-medium">{t("CDR")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("Date")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("MSISDN")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("Service")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("Product")}</th>
                  <th className="px-3 py-2.5 font-medium">
                    {t("Destination")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Expected")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Billed")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Variance")}
                  </th>
                  <th className="px-5 py-2.5 font-medium">{t("Status")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-muted/30 transition">
                    <td className="px-5 py-2.5">
                      <Link
                        to="/rating/cdrs/$resultId"
                        params={{ resultId: row.id }}
                        className="font-mono text-[12px] text-primary hover:underline"
                      >
                        {row.cdr_id}
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-[12px] text-muted-foreground whitespace-nowrap">
                      {fmtDate(row.event_date)}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-[12px]">
                      {row.msisdn ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-[12px]">
                      {row.service_type}
                    </td>
                    <td className="px-3 py-2.5 text-[12px]">
                      {row.product_code ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-[12px] text-muted-foreground">
                      {row.destination_zone ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {money(row.expected_final_charge, row.currency)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {money(row.actual_charge, row.currency)}
                    </td>
                    <td
                      className={`px-3 py-2.5 text-right tabular-nums font-medium ${
                        row.variance > 0
                          ? "text-warning-foreground"
                          : row.variance < 0
                            ? "text-destructive"
                            : "text-muted-foreground"
                      }`}
                    >
                      {row.variance > 0 ? "+" : ""}
                      {money(row.variance, row.currency)}
                    </td>
                    <td className="px-5 py-2.5">
                      <span
                        className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border whitespace-nowrap ${statusTone(row.status)}`}
                      >
                        {row.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between px-5 py-3 border-t border-border">
            <span className="text-[12px] text-muted-foreground">
              {t("Page")} {page + 1} · {fmtCount(rows.length)} {t("rows")}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition disabled:opacity-40"
              >
                {t("Previous")}
              </button>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={rows.length < PAGE_SIZE}
                className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition disabled:opacity-40"
              >
                {t("Next")}
              </button>
            </div>
          </div>
        </section>
      )}
    </AppShell>
  );
}
