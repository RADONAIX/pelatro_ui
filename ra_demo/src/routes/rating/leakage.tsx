import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { TrendingDown } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import { useLeakage, useRatingRuns } from "@/lib/rating/hooks";
import {
  fmtCount,
  fmtDate,
  fmtPct,
  money,
  titleCase,
} from "@/lib/rating/format";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/leakage")({
  component: RevenueLeakagePage,
});

const DIMENSIONS = [
  { value: "product", label: "Product" },
  { value: "service", label: "Service" },
  { value: "destination_zone", label: "Destination zone" },
  { value: "time_band", label: "Time band" },
  { value: "root_cause", label: "Root cause" },
];

function RevenueLeakagePage() {
  const t = useT();
  const [dimension, setDimension] = useState("product");
  const [runId, setRunId] = useState("");

  const { data: runs = [] } = useRatingRuns();
  const {
    data: rows = [],
    isLoading,
    error,
    refetch,
  } = useLeakage(dimension, { run_id: runId || undefined });

  const pretty = dimension === "root_cause";
  const totals = rows.reduce(
    (acc, r) => ({
      cdrs: acc.cdrs + r.cdrs,
      expected: acc.expected + r.expected,
      billed: acc.billed + r.billed,
      under: acc.under + r.undercharge,
      over: acc.over + r.overcharge,
    }),
    { cdrs: 0, expected: 0, billed: 0, under: 0, over: 0 },
  );

  return (
    <AppShell>
      <PageHeader
        title={t("Revenue Leakage")}
        description={t(
          "Where the money goes missing: every slice's expected revenue, billed revenue, and the exposure in both directions, worst first.",
        )}
        info={t(
          "Net leakage is shown last for a reason — undercharge is lost revenue, overcharge is customer harm, and netting them hides both.",
        )}
      />

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Select
          value={dimension}
          onChange={setDimension}
          options={DIMENSIONS.map((d) => ({
            value: d.value,
            label: t(d.label),
          }))}
          minWidth={180}
          ariaLabel={t("Break down by")}
        />
        <Select
          value={runId}
          onChange={setRunId}
          options={[
            { value: "", label: t("All runs") },
            ...runs.map((r) => ({
              value: r.id,
              label: `${r.id.slice(0, 8)} · ${fmtDate(r.created_at)}`,
            })),
          ]}
          minWidth={210}
          ariaLabel={t("Filter by run")}
        />
      </div>

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {!isLoading && rows.length === 0 && (
        <RatingEmpty
          icon={TrendingDown}
          title="No leakage found"
          description="Either nothing has been rated yet, or every charge in this dimension matched."
        />
      )}

      {rows.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                  <th className="px-5 py-2.5 font-medium">
                    {t(
                      DIMENSIONS.find((d) => d.value === dimension)?.label ??
                        "",
                    )}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("CDRs")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Match")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Expected")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Billed")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Undercharge")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Overcharge")}
                  </th>
                  <th className="px-5 py-2.5 font-medium text-right">
                    {t("Net leakage")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr key={row.key} className="hover:bg-muted/30 transition">
                    <td className="px-5 py-2.5 text-foreground">
                      {dimension === "product" ? (
                        <Link
                          to="/rating/records"
                          search={{ product_code: row.key }}
                          className="text-primary hover:underline"
                        >
                          {row.key}
                        </Link>
                      ) : pretty ? (
                        titleCase(row.key)
                      ) : (
                        row.key
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {fmtCount(row.cdrs)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">
                      {fmtPct(row.match_rate)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {money(row.expected)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {money(row.billed)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-warning-foreground">
                      {money(row.undercharge)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-destructive">
                      {money(row.overcharge)}
                    </td>
                    <td
                      className={`px-5 py-2.5 text-right tabular-nums font-semibold ${
                        row.net_variance > 0
                          ? "text-warning-foreground"
                          : row.net_variance < 0
                            ? "text-destructive"
                            : "text-muted-foreground"
                      }`}
                    >
                      {row.net_variance > 0 ? "+" : ""}
                      {money(row.net_variance)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-border font-semibold">
                  <td className="px-5 py-2.5">{t("Total")}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {fmtCount(totals.cdrs)}
                  </td>
                  <td className="px-3 py-2.5"></td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {money(totals.expected)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {money(totals.billed)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-warning-foreground">
                    {money(totals.under)}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-destructive">
                    {money(totals.over)}
                  </td>
                  <td className="px-5 py-2.5 text-right tabular-nums">
                    {money(totals.under - totals.over)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      )}
    </AppShell>
  );
}
