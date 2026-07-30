import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Check, Eye, ShieldCheck, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import { ratingApi, ratingError } from "@/lib/rating/api";
import { useCanEditRules, useRules } from "@/lib/rating/hooks";
import { fmtDate, statusTone } from "@/lib/rating/format";
import { RuleStatusBadge } from "@/components/rating/RuleStatusBadge";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/approvals")({
  component: ApprovalsPage,
});

// The two stations of the review ladder that sit in front of approval.
// DRAFT rules are the author's business; APPROVED rules are done — the inbox
// is only what someone else is being waited on for.
const QUEUE = [
  {
    status: "VALIDATED",
    heading: "Awaiting review",
    advance: "REVIEWED",
    advanceLabel: "Mark reviewed",
  },
  {
    status: "REVIEWED",
    heading: "Awaiting approval",
    advance: "APPROVED",
    advanceLabel: "Approve",
  },
] as const;

function ApprovalsPage() {
  const t = useT();
  const canEdit = useCanEditRules();
  const qc = useQueryClient();
  const [comment, setComment] = useState("");

  const validated = useRules({ status: "VALIDATED", limit: 100 });
  const reviewed = useRules({ status: "REVIEWED", limit: 100 });
  const byStatus = { VALIDATED: validated, REVIEWED: reviewed };

  const act = useMutation({
    mutationFn: async (vars: { ruleId: string; status: string }) => {
      const { data } = await ratingApi.post(`/rules/${vars.ruleId}/status`, {
        status: vars.status,
        comment: comment.trim() || undefined,
      });
      return data;
    },
    onSuccess: (_data, vars) => {
      toast.success(`${t("Moved to")} ${vars.status}`);
      qc.invalidateQueries({ queryKey: ["rating", "rules"] });
      qc.invalidateQueries({ queryKey: ["rating", "rule"] });
    },
    onError: (err) =>
      toast.error(t("Transition refused"), { description: ratingError(err) }),
  });

  const isLoading = validated.isLoading || reviewed.isLoading;
  const error = validated.error ?? reviewed.error;
  const total = (validated.data?.total ?? 0) + (reviewed.data?.total ?? 0);

  return (
    <AppShell>
      <PageHeader
        title={t("Approvals")}
        description={t(
          "Every rule change waiting on a second pair of eyes. Nothing reaches the rating engine without passing through here — approval is what separates a draft from money.",
        )}
        info={t(
          "The ladder is validate → review → approve. Returning a rule to draft keeps its history; nothing is deleted.",
        )}
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatTile
          icon={ShieldCheck}
          label={t("In the queue")}
          value={String(total)}
        />
        <StatTile
          icon={Eye}
          label={t("Awaiting review")}
          value={String(validated.data?.total ?? 0)}
        />
        <StatTile
          icon={Check}
          label={t("Awaiting approval")}
          value={String(reviewed.data?.total ?? 0)}
        />
      </div>

      {canEdit && (
        <div className="mb-6">
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={t("Optional comment recorded with every action…")}
            className="h-9 w-full max-w-xl rounded-lg border border-border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
      )}

      {isLoading && <RatingLoading />}
      {error && (
        <RatingError
          error={error}
          onRetry={() => {
            void validated.refetch();
            void reviewed.refetch();
          }}
        />
      )}

      {!isLoading && total === 0 && (
        <RatingEmpty
          icon={ShieldCheck}
          title="The queue is empty"
          description="No rules are waiting on review or approval. Validated drafts appear here automatically."
        />
      )}

      {QUEUE.map((station) => {
        const rows = byStatus[station.status].data?.items ?? [];
        if (rows.length === 0) return null;
        return (
          <section
            key={station.status}
            className="bg-card border border-border rounded-xl overflow-hidden mb-6"
          >
            <div className="px-5 py-4 border-b border-border flex items-center gap-3">
              <h2 className="text-sm font-semibold text-foreground">
                {t(station.heading)}
              </h2>
              <span
                className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${statusTone(station.status)}`}
              >
                {station.status}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                    <th className="px-5 py-2.5 font-medium">{t("Rule")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("Type")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("Service")}</th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("Priority")}
                    </th>
                    <th className="px-3 py-2.5 font-medium">
                      {t("Effective")}
                    </th>
                    <th className="px-3 py-2.5 font-medium">{t("Owner")}</th>
                    <th className="px-5 py-2.5 font-medium text-right">
                      {t("Actions")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((rule) => (
                    <tr key={rule.id} className="hover:bg-muted/30 transition">
                      <td className="px-5 py-2.5">
                        <Link
                          to="/rating/rules/$ruleId"
                          params={{ ruleId: rule.id }}
                          className="hover:underline"
                        >
                          <div className="font-mono text-[12px] text-primary">
                            {rule.rule_key} v{rule.version}
                          </div>
                          <div className="text-[12px] text-muted-foreground line-clamp-1">
                            {rule.name}
                          </div>
                        </Link>
                      </td>
                      <td className="px-3 py-2.5 text-[12px]">
                        {rule.rule_type}
                      </td>
                      <td className="px-3 py-2.5 text-[12px]">
                        {rule.service_type}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {rule.priority}
                      </td>
                      <td className="px-3 py-2.5 text-[12px] text-muted-foreground whitespace-nowrap">
                        {fmtDate(rule.effective_from)}
                      </td>
                      <td className="px-3 py-2.5 text-[12px] text-muted-foreground">
                        {rule.owner || "—"}
                      </td>
                      <td className="px-5 py-2.5">
                        {canEdit ? (
                          <div className="flex justify-end gap-2">
                            <button
                              onClick={() =>
                                act.mutate({
                                  ruleId: rule.id,
                                  status: station.advance,
                                })
                              }
                              disabled={act.isPending}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
                            >
                              <Check className="h-3.5 w-3.5" />
                              {t(station.advanceLabel)}
                            </button>
                            <button
                              onClick={() =>
                                act.mutate({ ruleId: rule.id, status: "DRAFT" })
                              }
                              disabled={act.isPending}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted transition disabled:opacity-50"
                            >
                              <Undo2 className="h-3.5 w-3.5" />
                              {t("Return to draft")}
                            </button>
                          </div>
                        ) : (
                          <div className="flex justify-end">
                            <RuleStatusBadge status={rule.status} />
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </AppShell>
  );
}
