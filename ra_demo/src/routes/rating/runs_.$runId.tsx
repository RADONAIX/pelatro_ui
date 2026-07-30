import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock,
  FileSearch,
  Layers,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import {
  usePipelineRuns,
  useRatingRun,
  useRunContexts,
  useRunSummary,
} from "@/lib/rating/hooks";
import {
  fmtCount,
  fmtDateTime,
  fmtPct,
  money,
  statusTone,
  titleCase,
} from "@/lib/rating/format";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";
import type { StageState } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/runs_/$runId")({
  component: RunDetailPage,
});

const TABS = ["Overview", "Pipeline", "Statistics", "Contexts"] as const;
type Tab = (typeof TABS)[number];

function RunDetailPage() {
  const { runId } = Route.useParams();
  const t = useT();
  const [tab, setTab] = useState<Tab>("Overview");

  const { data: run, isLoading, error, refetch } = useRatingRun(runId);
  const { data: summary } = useRunSummary(runId);
  const { data: contexts = [] } = useRunContexts(
    tab === "Contexts" ? runId : undefined,
  );
  // The upload workflow (if this run came through it) carries the per-stage
  // trace; a run started directly from a batch simply has no pipeline tab data.
  const { data: pipelineRuns = [] } = usePipelineRuns();
  const pipeline = pipelineRuns.find((p) => p.rating_run_id === runId);

  const duration =
    run?.started_at && run?.finished_at
      ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1)}s`
      : "—";

  return (
    <AppShell>
      <PageHeader
        title={`${t("Rating Run")} ${runId.slice(0, 8)}`}
        description={t(
          "Everything this run did: the pipeline it ran through, what it found, and the contexts it resolved rules for.",
        )}
        actions={
          <Link
            to="/rating/runs"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
          >
            <ArrowLeft className="h-4 w-4" /> {t("All runs")}
          </Link>
        }
      />

      {isLoading && <RatingLoading label="Loading run…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {run && (
        <div className="space-y-6">
          {/* --- Header strip --------------------------------------------- */}
          <div className="bg-card border border-border rounded-xl p-5 flex flex-wrap items-center gap-x-8 gap-y-3">
            <span
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${statusTone(run.status)}`}
            >
              {run.status}
            </span>
            {[
              [t("Type"), run.run_type],
              [t("Snapshot"), `v${run.snapshot_version}`],
              [t("Started"), fmtDateTime(run.started_at)],
              [t("Duration"), duration],
              [t("Triggered by"), run.triggered_by_name ?? "—"],
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
              to="/rating/records"
              search={{ run_id: runId }}
              className="ml-auto inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
            >
              <FileSearch className="h-4 w-4" /> {t("Records")}
            </Link>
          </div>

          {run.error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-5 py-4 flex items-start gap-3">
              <XCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
              <p className="text-sm text-destructive leading-relaxed">
                {run.error}
              </p>
            </div>
          )}

          {/* --- Tabs ------------------------------------------------------ */}
          <div className="flex gap-1 border-b border-border">
            {TABS.map((name) => (
              <button
                key={name}
                onClick={() => setTab(name)}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                  tab === name
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(name)}
              </button>
            ))}
          </div>

          {tab === "Overview" && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                <StatTile
                  icon={BarChart3}
                  label={t("Total CDRs")}
                  value={fmtCount(run.total_cdrs)}
                />
                <StatTile
                  icon={CheckCircle2}
                  label={`${t("Matched")} · ${fmtPct(summary?.match_rate ?? null)}`}
                  value={fmtCount(run.matched_count)}
                />
                <StatTile
                  icon={Layers}
                  label={t("Distinct contexts")}
                  value={fmtCount(run.distinct_contexts)}
                />
                <StatTile
                  icon={AlertTriangle}
                  label={t("Exception groups")}
                  value={fmtCount(run.exception_count)}
                />
              </div>

              <section className="bg-card border border-border rounded-xl p-5">
                <h2 className="text-sm font-semibold text-foreground mb-4">
                  {t("Revenue summary")}
                </h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  {[
                    [t("Expected"), money(run.expected_revenue), ""],
                    [t("Billed"), money(run.billed_revenue), ""],
                    [
                      t("Undercharge"),
                      money(run.undercharge_total),
                      "text-warning-foreground",
                    ],
                    [
                      t("Overcharge"),
                      money(run.overcharge_total),
                      "text-destructive",
                    ],
                  ].map(([label, value, cls]) => (
                    <div key={label}>
                      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        {label}
                      </div>
                      <div
                        className={`text-lg font-semibold tabular-nums ${cls || "text-foreground"}`}
                      >
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}

          {tab === "Pipeline" &&
            (pipeline ? (
              <section className="bg-card border border-border rounded-xl p-5">
                <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
                  <h2 className="text-sm font-semibold text-foreground">
                    {t("Processing pipeline")} — {pipeline.filename}
                  </h2>
                  <Link
                    to="/rating/pipeline"
                    className="text-xs font-medium text-primary hover:underline"
                  >
                    {t("Open in CDR Pipeline")}
                  </Link>
                </div>
                <ol className="space-y-2">
                  {pipeline.stages.map((stage) => (
                    <StageRow key={stage.key} stage={stage} />
                  ))}
                </ol>
              </section>
            ) : (
              <p className="text-sm text-muted-foreground px-1 py-4">
                {t(
                  "This run was started directly from a CDR batch, so there is no upload pipeline trace. Runs launched from a file upload show each stage here.",
                )}
              </p>
            ))}

          {tab === "Statistics" && summary && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Results by status")}
                </h2>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                    <th className="px-5 py-2.5 font-medium">{t("Status")}</th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("CDRs")}
                    </th>
                    <th className="px-3 py-2.5 font-medium text-right">
                      {t("Share")}
                    </th>
                    <th className="px-5 py-2.5 font-medium text-right">
                      {t("Variance")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {summary.by_status.map((row) => (
                    <tr key={row.status}>
                      <td className="px-5 py-2.5">
                        <span
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${statusTone(row.status)}`}
                        >
                          {row.status}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {fmtCount(row.count)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">
                        {fmtPct(
                          (row.count / Math.max(1, run.rated_cdrs)) * 100,
                        )}
                      </td>
                      <td className="px-5 py-2.5 text-right tabular-nums">
                        {money(row.variance)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {tab === "Contexts" && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <h2 className="text-sm font-semibold text-foreground">
                  {t("Rating contexts")}
                </h2>
                <p className="text-[12px] text-muted-foreground mt-0.5">
                  {t(
                    "Rules are resolved once per context, not once per CDR — this is why a million CDRs need only a handful of rule decisions.",
                  )}
                </p>
              </div>
              {contexts.length === 0 ? (
                <p className="px-5 py-6 text-sm text-muted-foreground">
                  {t("No contexts recorded for this run.")}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground border-b border-border">
                        <th className="px-5 py-2.5 font-medium">
                          {t("Context")}
                        </th>
                        <th className="px-3 py-2.5 font-medium text-right">
                          {t("CDRs")}
                        </th>
                        <th className="px-3 py-2.5 font-medium text-right">
                          {t("Candidates")}
                        </th>
                        <th className="px-5 py-2.5 font-medium">
                          {t("Selected rules")}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {contexts.map((ctx) => {
                        const c = ctx as {
                          context_key?: string;
                          context_hash?: string;
                          cdr_count?: number;
                          candidate_count?: number;
                          selected_rules?: Record<string, unknown>;
                        };
                        return (
                          <tr key={c.context_hash}>
                            <td className="px-5 py-2.5 font-mono text-[11px] text-foreground max-w-md truncate">
                              {c.context_key ?? c.context_hash}
                            </td>
                            <td className="px-3 py-2.5 text-right tabular-nums">
                              {fmtCount(c.cdr_count ?? 0)}
                            </td>
                            <td className="px-3 py-2.5 text-right tabular-nums">
                              {fmtCount(c.candidate_count ?? 0)}
                            </td>
                            <td className="px-5 py-2.5 text-[12px] text-muted-foreground">
                              {Object.keys(c.selected_rules ?? {}).length}{" "}
                              {t("stages")}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </AppShell>
  );
}

function StageRow({ stage }: { stage: StageState }) {
  const t = useT();
  const icon =
    stage.status === "COMPLETED" ? (
      <CheckCircle2 className="h-4 w-4 text-success" />
    ) : stage.status === "FAILED" ? (
      <XCircle className="h-4 w-4 text-destructive" />
    ) : stage.status === "RUNNING" ? (
      <CircleDot className="h-4 w-4 text-info animate-pulse" />
    ) : (
      <Clock className="h-4 w-4 text-muted-foreground/50" />
    );

  return (
    <li className="flex items-center gap-3 rounded-lg border border-border px-4 py-3">
      {icon}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-foreground">
          {titleCase(stage.label || stage.key)}
        </div>
        {(stage.detail || stage.error) && (
          <div
            className={`text-[12px] mt-0.5 line-clamp-1 ${stage.error ? "text-destructive" : "text-muted-foreground"}`}
          >
            {stage.error ?? stage.detail}
          </div>
        )}
      </div>
      <div className="text-right shrink-0 text-[12px] text-muted-foreground tabular-nums">
        <div>
          {fmtCount(stage.records_in)}{" "}
          <ChevronRight className="inline h-3 w-3" />{" "}
          {fmtCount(stage.records_out)}
          {stage.records_failed > 0 && (
            <span className="text-destructive ml-1.5">
              −{fmtCount(stage.records_failed)}
            </span>
          )}
        </div>
        <div>
          {stage.duration_ms !== null
            ? `${(stage.duration_ms / 1000).toFixed(1)}s`
            : "—"}
        </div>
      </div>
    </li>
  );
}
