import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { AlertTriangle, ChevronRight, Users } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import { useExceptionMeta, useExceptions } from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/exceptions")({
  component: ExceptionsPage,
});

const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: "bg-destructive text-destructive-foreground border-destructive",
  HIGH: "bg-destructive/10 text-destructive border-destructive/20",
  MEDIUM: "bg-warning/15 text-warning-foreground border-warning/30",
  LOW: "bg-info/10 text-info border-info/20",
};

const money = (n: number) =>
  n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

function ExceptionsPage() {
  const t = useT();
  const { data: meta } = useExceptionMeta();

  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");

  const {
    data: rows = [],
    isLoading,
    error,
    refetch,
  } = useExceptions({ status, severity });

  const totalImpact = rows.reduce(
    (sum, r) => sum + Math.abs(r.revenue_impact),
    0,
  );
  const affectedCdrs = rows.reduce((sum, r) => sum + r.cdr_count, 0);

  return (
    <AppShell>
      <PageHeader
        title={t("Exceptions")}
        description={t(
          "CDRs that failed the same way for the same reason are grouped into one investigation. One broken rule mispricing 40,000 calls is one problem, not 40,000 tickets.",
        )}
        info={t(
          "Undercharge and overcharge are never netted — one is revenue leakage, the other is customer harm.",
        )}
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatTile
          icon={AlertTriangle}
          label={t("Exception groups")}
          value={String(rows.length)}
        />
        <StatTile
          icon={Users}
          label={t("CDRs affected")}
          value={String(affectedCdrs)}
        />
        <StatTile
          icon={AlertTriangle}
          label={t("Revenue at risk")}
          value={money(totalImpact)}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Select
          value={status}
          onChange={setStatus}
          options={[
            { value: "", label: t("All statuses") },
            ...Object.keys(meta?.transitions ?? {}).map((s) => ({
              value: s,
              label: s,
            })),
          ]}
          minWidth={180}
          ariaLabel={t("Filter by status")}
        />
        <Select
          value={severity}
          onChange={setSeverity}
          options={[
            { value: "", label: t("All severities") },
            ...["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => ({
              value: s,
              label: s,
            })),
          ]}
          minWidth={160}
          ariaLabel={t("Filter by severity")}
        />
      </div>

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {rows.length === 0 && !isLoading && (
        <RatingEmpty
          icon={AlertTriangle}
          title="No exceptions"
          description="Either nothing has been rated yet, or every CDR matched its expected charge."
        />
      )}

      {rows.length > 0 && (
        <div className="space-y-2">
          {rows.map((e) => (
            <Link
              key={e.id}
              to="/rating/exceptions/$exceptionId"
              params={{ exceptionId: e.id }}
              className="w-full text-left bg-card border border-border rounded-xl p-4 hover:bg-muted/30 transition flex items-start gap-4"
            >
              <span
                className={`text-[10px] font-semibold px-2 py-1 rounded-md border shrink-0 ${
                  SEVERITY_TONE[e.severity] ?? SEVERITY_TONE.LOW
                }`}
              >
                {e.severity}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-foreground">
                  {e.title}
                </div>
                <div className="text-[12px] text-muted-foreground mt-0.5">
                  {e.assurance_status} ·{" "}
                  {e.root_cause.replace(/_/g, " ").toLowerCase()}
                  {e.rule_key && ` · ${e.rule_key}`}
                </div>
                <div className="text-[12px] text-muted-foreground mt-1 line-clamp-1">
                  {e.probable_cause}
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-semibold text-foreground tabular-nums">
                  {money(e.revenue_impact)}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {e.cdr_count} {t("CDRs")} · {e.subscriber_count} {t("subs")}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {e.status}
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 mt-1" />
            </Link>
          ))}
        </div>
      )}
    </AppShell>
  );
}
