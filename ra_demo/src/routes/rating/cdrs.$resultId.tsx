import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  FileSearch,
  Package,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import { useEnrichedCdr, useRatingResult } from "@/lib/rating/hooks";
import {
  fmtDate,
  fmtDateTime,
  money,
  statusTone,
  titleCase,
} from "@/lib/rating/format";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/cdrs/$resultId")({
  component: CdrInvestigationPage,
});

const TABS = [
  "CDR Details",
  "Enrichment",
  "Candidate Rules",
  "Calculation Trace",
] as const;
type Tab = (typeof TABS)[number];

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm font-medium text-foreground mt-0.5 break-words">
        {value ?? "—"}
      </dd>
    </div>
  );
}

function CdrInvestigationPage() {
  const { resultId } = Route.useParams();
  const t = useT();
  const [tab, setTab] = useState<Tab>("Calculation Trace");

  const { data: result, isLoading, error, refetch } = useRatingResult(resultId);
  const { data: cdr } = useEnrichedCdr(result?.cdr_enriched_id);

  return (
    <AppShell>
      <PageHeader
        title={`${t("CDR Investigation")} — ${result?.cdr_id ?? ""}`}
        description={t(
          "Why this CDR was charged what it was charged: the raw record, its enrichment, every rule that was considered, and the calculation step by step.",
        )}
        actions={
          <Link
            to="/rating/records"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
          >
            <ArrowLeft className="h-4 w-4" /> {t("Records")}
          </Link>
        }
      />

      {isLoading && <RatingLoading label="Loading result…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {result && (
        <div className="space-y-6">
          {/* --- Verdict strip --------------------------------------------- */}
          <div className="bg-card border border-border rounded-xl p-5 flex flex-wrap items-center gap-x-8 gap-y-3">
            <span
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${statusTone(result.status)}`}
            >
              {result.status}
            </span>
            {[
              [
                t("Expected"),
                money(result.expected_final_charge, result.currency),
              ],
              [t("Billed"), money(result.actual_charge, result.currency)],
              [
                t("Variance"),
                `${result.variance > 0 ? "+" : ""}${money(result.variance, result.currency)}`,
              ],
              [t("Root cause"), titleCase(result.root_cause)],
              [t("Event date"), fmtDate(result.event_date)],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {label}
                </div>
                <div className="text-sm font-semibold text-foreground tabular-nums">
                  {value}
                </div>
              </div>
            ))}
            {result.bundle_code && (
              <div className="inline-flex items-center gap-2 text-[12px] text-muted-foreground">
                <Package className="h-3.5 w-3.5" />
                {result.bundle_code}: {result.bundle_consumed} {t("covered")}
                {result.bundle_overflow > 0 &&
                  `, ${result.bundle_overflow} ${t("over")}`}
              </div>
            )}
            <Link
              to="/rating/runs/$runId"
              params={{ runId: result.run_id }}
              className="ml-auto text-xs font-medium text-primary hover:underline"
            >
              {t("Run")} {result.run_id.slice(0, 8)}
            </Link>
          </div>

          {/* --- Tabs ------------------------------------------------------ */}
          <div className="flex gap-1 border-b border-border overflow-x-auto">
            {TABS.map((name) => (
              <button
                key={name}
                onClick={() => setTab(name)}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition ${
                  tab === name
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(name)}
              </button>
            ))}
          </div>

          {tab === "CDR Details" && (
            <section className="bg-card border border-border rounded-xl p-5">
              <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-4">
                <Field label={t("CDR ID")} value={result.cdr_id} />
                <Field
                  label={t("Event time")}
                  value={fmtDateTime(cdr?.event_timestamp)}
                />
                <Field label={t("MSISDN")} value={result.msisdn} />
                <Field label={t("Subscriber")} value={result.subscriber_id} />
                <Field
                  label={t("Calling number")}
                  value={cdr?.calling_number}
                />
                <Field label={t("Called number")} value={cdr?.called_number} />
                <Field label={t("Service")} value={result.service_type} />
                <Field
                  label={t("Duration (s)")}
                  value={cdr?.duration_seconds ?? "—"}
                />
                <Field label={t("Usage volume")} value={cdr?.usage_volume} />
                <Field
                  label={t("Billed charge")}
                  value={money(result.actual_charge, result.currency)}
                />
                <Field label={t("Currency")} value={result.currency} />
                <Field label={t("Engine")} value={result.engine_version} />
              </dl>
            </section>
          )}

          {tab === "Enrichment" && (
            <section className="bg-card border border-border rounded-xl p-5">
              <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-4">
                <Field label={t("Product")} value={result.product_code} />
                <Field label={t("Account type")} value={cdr?.account_type} />
                <Field
                  label={t("Destination zone")}
                  value={result.destination_zone}
                />
                <Field
                  label={t("On-net")}
                  value={
                    cdr?.on_net === null || cdr?.on_net === undefined
                      ? "—"
                      : cdr.on_net
                        ? t("Yes")
                        : t("No")
                  }
                />
                <Field label={t("Time band")} value={result.time_band} />
                <Field
                  label={t("Roaming")}
                  value={
                    cdr?.roaming === null || cdr?.roaming === undefined
                      ? "—"
                      : cdr.roaming
                        ? t("Yes")
                        : t("No")
                  }
                />
                <Field
                  label={t("Quality")}
                  value={cdr ? titleCase(cdr.quality_status) : "—"}
                />
                <Field
                  label={t("Context hash")}
                  value={
                    <span className="font-mono text-[12px]">
                      {result.context_hash?.slice(0, 12) ?? "—"}
                    </span>
                  }
                />
              </dl>
              {result.context_key && (
                <div className="mt-5 pt-4 border-t border-border">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
                    {t("Rating context")}
                  </div>
                  <code className="text-[12px] text-foreground break-all">
                    {result.context_key}
                  </code>
                  <p className="text-[12px] text-muted-foreground mt-2 leading-relaxed">
                    {t(
                      "Every CDR with this context gets the same rule decision — rules are resolved once per context, not once per CDR.",
                    )}
                  </p>
                </div>
              )}
            </section>
          )}

          {tab === "Candidate Rules" && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Every rule that was considered")}
                </h2>
                <p className="text-[12px] text-muted-foreground mt-0.5">
                  {t(
                    "Winners are selected per execution stage by specificity, then priority. Losers stay listed with the reason they lost.",
                  )}
                </p>
              </div>
              {result.candidates.length === 0 ? (
                <p className="px-5 py-6 text-sm text-muted-foreground">
                  {t("No candidate detail was recorded for this run.")}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                        <th className="px-5 py-2.5 font-medium w-10"></th>
                        <th className="px-3 py-2.5 font-medium">{t("Rule")}</th>
                        <th className="px-3 py-2.5 font-medium">
                          {t("Stage")}
                        </th>
                        <th className="px-3 py-2.5 font-medium text-right">
                          {t("Specificity")}
                        </th>
                        <th className="px-3 py-2.5 font-medium text-right">
                          {t("Priority")}
                        </th>
                        <th className="px-5 py-2.5 font-medium">
                          {t("Outcome")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {result.candidates.map((c) => (
                        <tr
                          key={`${c.rule_id}-${c.stage}`}
                          className={c.selected ? "bg-success/5" : undefined}
                        >
                          <td className="px-5 py-2.5">
                            {c.selected ? (
                              <CheckCircle2 className="h-4 w-4 text-success" />
                            ) : (
                              <XCircle className="h-4 w-4 text-muted-foreground/40" />
                            )}
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="font-mono text-[12px] text-foreground">
                              {c.rule_key} v{c.rule_version}
                            </div>
                            <div className="text-[12px] text-muted-foreground">
                              {c.rule_name}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 text-[12px]">{c.stage}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">
                            {c.specificity}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums">
                            {c.priority}
                          </td>
                          <td className="px-5 py-2.5 text-[12px] text-muted-foreground">
                            {c.reason}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {tab === "Calculation Trace" && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("The charge, step by step")}
                </h2>
              </div>
              {result.trace.length === 0 ? (
                <p className="px-5 py-6 text-sm text-muted-foreground">
                  {t("No trace recorded.")}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                        <th className="px-5 py-2.5 font-medium w-10">#</th>
                        <th className="px-3 py-2.5 font-medium">
                          {t("Stage")}
                        </th>
                        <th className="px-3 py-2.5 font-medium">
                          {t("Rule / action")}
                        </th>
                        <th className="px-3 py-2.5 font-medium">
                          {t("Detail")}
                        </th>
                        <th className="px-5 py-2.5 font-medium text-right">
                          {t("Value")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {result.trace.map((s) => (
                        <tr key={s.step}>
                          <td className="px-5 py-2.5 text-muted-foreground tabular-nums">
                            {s.step}
                          </td>
                          <td className="px-3 py-2.5">
                            <span className="text-[11px] font-semibold text-primary">
                              {s.stage}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="text-[13px] text-foreground">
                              {s.label}
                            </div>
                            {s.rule_key && (
                              <div className="font-mono text-[11px] text-muted-foreground">
                                {s.rule_key}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2.5 text-[12px] text-muted-foreground leading-relaxed">
                            {s.detail}
                          </td>
                          <td className="px-5 py-2.5 text-right tabular-nums font-medium">
                            {s.value ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="px-5 py-3 border-t border-border flex items-center justify-between">
                <span className="text-[12px] text-muted-foreground inline-flex items-center gap-2">
                  <FileSearch className="h-3.5 w-3.5" />
                  {t("Engine")} {result.engine_version}
                </span>
                <span className="text-sm font-semibold text-foreground tabular-nums">
                  {t("Expected final charge")}:{" "}
                  {money(result.expected_final_charge, result.currency)}
                </span>
              </div>
            </section>
          )}
        </div>
      )}
    </AppShell>
  );
}
