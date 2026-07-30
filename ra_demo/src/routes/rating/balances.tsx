import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Package, PackageOpen, ReceiptText, Search, X } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import {
  useBalanceBuckets,
  useBalanceStats,
  useBucketLedger,
  type BalanceBucketRow,
} from "@/lib/rating/hooks";
import { fmtCount, fmtDate, fmtDateTime } from "@/lib/rating/format";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/balances")({
  component: BalancesPage,
});

const qty = (n: number) =>
  n.toLocaleString("en-GB", { maximumFractionDigits: 2 });

function BalancesPage() {
  const t = useT();
  const [owner, setOwner] = useState("");
  const [exhaustedOnly, setExhaustedOnly] = useState("");
  const [openBucket, setOpenBucket] = useState<BalanceBucketRow | undefined>();

  const { data: stats } = useBalanceStats();
  const {
    data: buckets = [],
    isLoading,
    error,
    refetch,
  } = useBalanceBuckets({
    owner: owner.trim() || undefined,
    exhausted: exhaustedOnly === "" ? undefined : exhaustedOnly === "exhausted",
  });
  const { data: ledger = [] } = useBucketLedger(openBucket?.id);

  return (
    <AppShell>
      <PageHeader
        title={t("Bundle Balances")}
        description={t(
          "Every subscriber's allowance, and the ledger behind it: what each call drew, what spilled over, and exactly when the bundle ran out.",
        )}
        info={t(
          "Consumption is order-dependent, so the run rates each owner's events in time order — the ledger below is that order, preserved.",
        )}
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatTile
          icon={Package}
          label={t("Buckets")}
          value={fmtCount(stats?.total_buckets ?? 0)}
        />
        <StatTile
          icon={PackageOpen}
          label={t("Exhausted")}
          value={fmtCount(stats?.exhausted_buckets ?? 0)}
        />
        <StatTile
          icon={ReceiptText}
          label={t("Ledger entries")}
          value={fmtCount(stats?.ledger_entries ?? 0)}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <input
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder={t("Subscriber, account or MSISDN")}
            className="h-9 w-64 rounded-lg border border-border bg-card pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <Select
          value={exhaustedOnly}
          onChange={setExhaustedOnly}
          options={[
            { value: "", label: t("All buckets") },
            { value: "exhausted", label: t("Exhausted only") },
            { value: "remaining", label: t("With allowance left") },
          ]}
          minWidth={190}
          ariaLabel={t("Filter by state")}
        />
      </div>

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {!isLoading && buckets.length === 0 && (
        <RatingEmpty
          icon={Package}
          title="No balance buckets"
          description="Buckets are allocated lazily, on a subscriber's first use of a bundle in a period — run a rating with a bundle rule active and they appear here."
        />
      )}

      {buckets.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                  <th className="px-5 py-2.5 font-medium">{t("Owner")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("Bundle")}</th>
                  <th className="px-3 py-2.5 font-medium">{t("Period")}</th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Allocated")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Consumed")}
                  </th>
                  <th className="px-3 py-2.5 font-medium text-right">
                    {t("Overflow")}
                  </th>
                  <th className="px-3 py-2.5 font-medium w-44">
                    {t("Remaining")}
                  </th>
                  <th className="px-5 py-2.5 font-medium text-right">
                    {t("Events")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {buckets.map((b) => {
                  const pct =
                    b.allocated > 0
                      ? Math.min(100, (b.consumed / b.allocated) * 100)
                      : 100;
                  return (
                    <tr
                      key={b.id}
                      onClick={() => setOpenBucket(b)}
                      className="cursor-pointer hover:bg-muted/30 transition"
                    >
                      <td className="px-5 py-2.5">
                        <div className="font-mono text-[12px] text-foreground">
                          {b.owner_key}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {b.shared ? t("Shared (account)") : t("Personal")}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="text-[12px] text-foreground">
                          {b.bundle_code}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {b.service_type} · {b.reset_period}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-[12px] text-muted-foreground whitespace-nowrap">
                        {fmtDate(b.period_start)} – {fmtDate(b.period_end)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {qty(b.allocated)} {b.quota_unit}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {qty(b.consumed)}
                      </td>
                      <td
                        className={`px-3 py-2.5 text-right tabular-nums ${b.overflow > 0 ? "text-warning-foreground font-medium" : "text-muted-foreground"}`}
                      >
                        {qty(b.overflow)}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                            <div
                              className={`h-full rounded-full ${pct >= 100 ? "bg-destructive" : pct >= 80 ? "bg-warning" : "bg-success"}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="text-[12px] tabular-nums text-muted-foreground w-14 text-right">
                            {qty(b.remaining)}
                          </span>
                        </div>
                      </td>
                      <td className="px-5 py-2.5 text-right tabular-nums text-muted-foreground">
                        {b.consumption_count}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* --- Consumption trace ---------------------------------------------- */}
      {openBucket && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-3xl my-8 rounded-xl border border-border bg-card shadow-2xl">
            <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
              <div>
                <h2 className="text-sm font-semibold text-foreground">
                  {openBucket.bundle_code} — {openBucket.owner_key}
                </h2>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  {qty(openBucket.consumed)} / {qty(openBucket.allocated)}{" "}
                  {openBucket.quota_unit} {t("consumed")} ·{" "}
                  {qty(openBucket.overflow)} {t("overflow")} ·{" "}
                  {fmtDate(openBucket.period_start)} –{" "}
                  {fmtDate(openBucket.period_end)}
                </p>
              </div>
              <button
                onClick={() => setOpenBucket(undefined)}
                aria-label={t("Close")}
                className="h-8 w-8 rounded-lg flex items-center justify-center text-muted-foreground hover:bg-muted transition"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="px-5 py-4">
              {ledger.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">
                  {t("No consumption recorded in this bucket yet.")}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                        <th className="py-2 pr-3 font-medium">{t("CDR")}</th>
                        <th className="py-2 px-3 font-medium">{t("Event")}</th>
                        <th className="py-2 px-3 font-medium text-right">
                          {t("Requested")}
                        </th>
                        <th className="py-2 px-3 font-medium text-right">
                          {t("Covered")}
                        </th>
                        <th className="py-2 px-3 font-medium text-right">
                          {t("Overflow")}
                        </th>
                        <th className="py-2 pl-3 font-medium text-right">
                          {t("Balance")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {ledger.map((e) => (
                        <tr key={e.id}>
                          <td className="py-2 pr-3">
                            <Link
                              to="/rating/records"
                              search={{ run_id: e.run_id }}
                              className="font-mono text-[12px] text-primary hover:underline"
                            >
                              {e.cdr_id}
                            </Link>
                          </td>
                          <td className="py-2 px-3 text-[12px] text-muted-foreground whitespace-nowrap">
                            {fmtDateTime(e.event_timestamp)}
                          </td>
                          <td className="py-2 px-3 text-right tabular-nums">
                            {qty(e.requested)}
                          </td>
                          <td className="py-2 px-3 text-right tabular-nums text-success">
                            {qty(e.consumed)}
                          </td>
                          <td
                            className={`py-2 px-3 text-right tabular-nums ${e.overflow > 0 ? "text-warning-foreground font-medium" : "text-muted-foreground"}`}
                          >
                            {qty(e.overflow)}
                          </td>
                          <td className="py-2 pl-3 text-right tabular-nums text-muted-foreground whitespace-nowrap">
                            {qty(e.balance_before)} → {qty(e.balance_after)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
