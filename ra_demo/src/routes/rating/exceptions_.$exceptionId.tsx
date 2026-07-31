import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Lightbulb,
  MessageSquare,
  Send,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useAddExceptionComment,
  useCanEditExceptions,
  useException,
  useExceptionMeta,
  useExceptionResults,
  useTransitionException,
} from "@/lib/rating/hooks";
import {
  fmtCount,
  fmtDate,
  fmtDateTime,
  money,
  statusTone,
  titleCase,
} from "@/lib/rating/format";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/exceptions_/$exceptionId")({
  component: ExceptionCasePage,
});

function ExceptionCasePage() {
  const { exceptionId } = Route.useParams();
  const t = useT();
  const canEdit = useCanEditExceptions();
  const [note, setNote] = useState("");

  const { data: detail, isLoading, error, refetch } = useException(exceptionId);
  const { data: meta } = useExceptionMeta();
  const { data: affected = [] } = useExceptionResults(exceptionId);
  const transition = useTransitionException(exceptionId);
  const addComment = useAddExceptionComment(exceptionId);

  const allowed = detail ? (meta?.transitions?.[detail.status] ?? []) : [];

  const move = async (next: string) => {
    try {
      await transition.mutateAsync({ status: next });
      toast.success(`${t("Moved to")} ${next}`);
    } catch (err) {
      toast.error(t("Transition refused"), { description: ratingError(err) });
    }
  };

  const submitNote = async () => {
    const body = note.trim();
    if (!body) return;
    try {
      await addComment.mutateAsync({ body });
      setNote("");
    } catch (err) {
      toast.error(t("Could not add the note"), {
        description: ratingError(err),
      });
    }
  };

  return (
    <AppShell>
      <PageHeader
        title={detail?.title ?? t("Exception Case")}
        description={t(
          "One investigation for every CDR that failed the same way for the same reason — with the money at stake, the likely cause, and the trail of what was done about it.",
        )}
        actions={
          <Link
            to="/rating/exceptions"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
          >
            <ArrowLeft className="h-4 w-4" /> {t("All exceptions")}
          </Link>
        }
      />

      {isLoading && <RatingLoading label="Loading case…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {detail && (
        <div className="space-y-6">
          {/* --- Case strip ------------------------------------------------ */}
          <div className="bg-card border border-border rounded-xl p-5 flex flex-wrap items-center gap-x-8 gap-y-3">
            <span
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${statusTone(detail.status)}`}
            >
              {detail.status}
            </span>
            <span
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${statusTone(detail.severity)}`}
            >
              {detail.severity}
            </span>
            {[
              [t("Type"), detail.assurance_status],
              [t("Root cause"), titleCase(detail.root_cause)],
              [t("Product"), detail.product_code ?? "—"],
              [t("Rule"), detail.rule_key ?? "—"],
              [
                t("Window"),
                `${fmtDate(detail.first_event_date)} – ${fmtDate(detail.last_event_date)}`,
              ],
              [t("Assigned to"), detail.assigned_to_name ?? t("Unassigned")],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {label}
                </div>
                <div className="text-sm font-medium text-foreground">
                  {value}
                </div>
              </div>
            ))}
            <Link
              to="/rating/runs/$runId"
              params={{ runId: detail.run_id }}
              className="ml-auto text-xs font-medium text-primary hover:underline"
            >
              {t("Run")} {detail.run_id.slice(0, 8)}
            </Link>
          </div>

          {/* --- Impact ---------------------------------------------------- */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <StatTile
              icon={AlertTriangle}
              label={t("Revenue impact")}
              value={money(detail.revenue_impact)}
            />
            <StatTile
              icon={BarChart3}
              label={t("Expected / billed")}
              value={`${money(detail.expected_total)} / ${money(detail.actual_total)}`}
            />
            <StatTile
              icon={Users}
              label={t("CDRs affected")}
              value={fmtCount(detail.cdr_count)}
            />
            <StatTile
              icon={Users}
              label={t("Subscribers")}
              value={fmtCount(detail.subscriber_count)}
            />
          </div>

          {/* --- Cause + workflow ------------------------------------------ */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <section className="bg-card border border-border rounded-xl p-5 xl:col-span-2">
              <div className="flex items-center gap-2 mb-2">
                <Lightbulb className="h-4 w-4 text-primary" />
                <span className="text-sm font-semibold text-foreground">
                  {t("Probable cause")}
                </span>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {detail.probable_cause}
              </p>
              <div className="flex items-start gap-2 mt-3 pt-3 border-t border-border">
                <ArrowRight className="h-4 w-4 shrink-0 mt-0.5 text-muted-foreground" />
                <p className="text-sm text-foreground leading-relaxed">
                  {detail.recommended_action}
                </p>
              </div>
              {detail.resolution && (
                <div className="mt-3 pt-3 border-t border-border">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
                    {t("Resolution")}
                  </div>
                  <p className="text-sm text-foreground leading-relaxed">
                    {detail.resolution}
                  </p>
                </div>
              )}
            </section>

            <section className="bg-card border border-border rounded-xl p-5">
              <h2 className="text-sm font-semibold text-foreground mb-3">
                {t("Lifecycle")}
              </h2>
              {canEdit && allowed.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {allowed.map((next) => (
                    <button
                      key={next}
                      onClick={() => move(next)}
                      disabled={transition.isPending}
                      className="inline-flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
                    >
                      {titleCase(next)}
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {canEdit
                    ? t("This case is closed — no further transitions.")
                    : t("You have read-only access to exception cases.")}
                </p>
              )}
            </section>
          </div>

          {/* --- Affected CDRs --------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Affected CDRs")}
              </h2>
              <p className="text-[12px] text-muted-foreground mt-0.5">
                {t(
                  "Worst variance first. Open any row for the full calculation trace.",
                )}
              </p>
            </div>
            {affected.length === 0 ? (
              <p className="px-5 py-6 text-sm text-muted-foreground">
                {t("No result rows found for this group.")}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                      <th className="px-5 py-2.5 font-medium">{t("CDR")}</th>
                      <th className="px-3 py-2.5 font-medium">{t("Date")}</th>
                      <th className="px-3 py-2.5 font-medium">{t("MSISDN")}</th>
                      <th className="px-3 py-2.5 font-medium text-right">
                        {t("Expected")}
                      </th>
                      <th className="px-3 py-2.5 font-medium text-right">
                        {t("Billed")}
                      </th>
                      <th className="px-5 py-2.5 font-medium text-right">
                        {t("Variance")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {affected.map((row) => (
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
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {money(row.expected_final_charge, row.currency)}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {money(row.actual_charge, row.currency)}
                        </td>
                        <td
                          className={`px-5 py-2.5 text-right tabular-nums font-medium ${
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* --- Timeline -------------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <MessageSquare className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">
                {t("Investigation timeline")}
              </h2>
            </div>

            {detail.comments.length === 0 ? (
              <p className="text-sm text-muted-foreground mb-3">
                {t("Nothing recorded yet.")}
              </p>
            ) : (
              <ul className="space-y-3 mb-4">
                {detail.comments.map((c) => (
                  <li key={c.id} className="flex items-start gap-3">
                    <span
                      className={`mt-1 h-2 w-2 rounded-full shrink-0 ${
                        c.kind === "COMMENT"
                          ? "bg-primary"
                          : "bg-muted-foreground/50"
                      }`}
                    />
                    <div className="min-w-0">
                      <p className="text-sm text-foreground leading-relaxed">
                        {c.body}
                      </p>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {c.author_name ?? t("System")} ·{" "}
                        {fmtDateTime(c.created_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {canEdit && (
              <div className="flex gap-2">
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submitNote();
                  }}
                  placeholder={t("Add an investigation note…")}
                  className="h-9 flex-1 rounded-lg border border-border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
                />
                <button
                  onClick={() => void submitNote()}
                  disabled={addComment.isPending || !note.trim()}
                  className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
                >
                  <Send className="h-4 w-4" /> {t("Add")}
                </button>
              </div>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
