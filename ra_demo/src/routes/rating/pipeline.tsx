import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Info,
  Loader2,
  MinusCircle,
  RotateCcw,
  Upload,
  Workflow,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Select } from "@/components/ui-kit/Select";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { useT } from "@/lib/i18n";
import type { PipelineRun } from "@/lib/rating/types";
import { ratingError } from "@/lib/rating/api";
import {
  useActiveSnapshot,
  useCanEditRuns,
  useCdrProfiles,
  usePipelineRun,
  usePipelineRuns,
  usePipelineStages,
  useRetryStage,
  useStartPipeline,
} from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { StageState } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/pipeline")({
  component: PipelinePage,
});

const money = (n: number | undefined) =>
  (n ?? 0).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const RUN_TONE: Record<string, string> = {
  QUEUED: "bg-muted text-muted-foreground border-border",
  RUNNING: "bg-[#F8C800]/15 text-[#9a7d00] border-[#F8C800]/40",
  COMPLETED: "bg-success/10 text-success border-success/20",
  COMPLETED_WITH_ISSUES:
    "bg-warning/15 text-warning-foreground border-warning/30",
  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
};

function StageIcon({ status }: { status: StageState["status"] }) {
  switch (status) {
    case "COMPLETED":
      return <CheckCircle2 className="h-4 w-4 text-success" />;
    case "RUNNING":
      return <Loader2 className="h-4 w-4 text-primary animate-spin" />;
    case "FAILED":
      return <XCircle className="h-4 w-4 text-destructive" />;
    case "SKIPPED":
      return <MinusCircle className="h-4 w-4 text-muted-foreground/50" />;
    default:
      return <CircleDashed className="h-4 w-4 text-muted-foreground/40" />;
  }
}

function PipelinePage() {
  const t = useT();
  const canEdit = useCanEditRuns();
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: stageSpecs = [] } = usePipelineStages();
  const { data: profiles = [] } = useCdrProfiles();
  const { data: active } = useActiveSnapshot();
  const { data: runs = [], isLoading, error, refetch } = usePipelineRuns();

  const [selectedId, setSelectedId] = useState<string | undefined>();
  const currentId = selectedId ?? runs[0]?.id;
  const { data: run } = usePipelineRun(currentId);

  const start = useStartPipeline();
  const retry = useRetryStage(currentId);

  const [cdrType, setCdrType] = useState("MSC");
  const [sourceSystem, setSourceSystem] = useState("MSC_SWITCH_01");

  const onPick = async (file: File | null) => {
    if (!file) return;
    try {
      const created = await start.mutateAsync({
        file,
        cdr_type: cdrType,
        source_system: sourceSystem,
      });
      setSelectedId(created.id);
      toast.success(t("Pipeline started"), {
        description: t(
          "The stages run in the background — this view follows them.",
        ),
      });
    } catch (err) {
      toast.error(t("Could not start the pipeline"), {
        description: ratingError(err),
      });
    }
    if (fileInput.current) fileInput.current.value = "";
  };

  const describe = (key: string) => stageSpecs.find((s) => s.key === key);

  return (
    <AppShell>
      <PageHeader
        title={t("Pipelines & Job Monitor")}
        description={t(
          "Ingestion → normalization → enrichment → rule selection → rating → assurance. The upload returns immediately; the stages run behind it.",
        )}
        info={t(
          "Each stage commits its own transaction, so a failure part-way can be fixed and re-run from that stage without re-uploading the file.",
        )}
      />

      {!active && (
        <div className="rounded-xl border border-warning/30 bg-warning/15 p-4 mb-6 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5 text-warning-foreground" />
          <div className="text-sm">
            <div className="font-semibold text-foreground">
              {t("No snapshot is active")}
            </div>
            <p className="text-muted-foreground mt-0.5">
              {t(
                "Ingestion and enrichment will run, but rating will fail at the selection stage.",
              )}{" "}
              <Link
                to="/rating/snapshots"
                className="text-primary hover:underline"
              >
                {t("Compile and activate one")}
              </Link>
            </p>
          </div>
        </div>
      )}

      <PipelineStatusSummary runs={runs} />

      {canEdit && (
        <section className="bg-card border border-border rounded-xl p-5 mb-6">
          <div className="flex items-center gap-2 mb-1">
            <Workflow className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold text-foreground">
              {t("Run the pipeline")}
            </h2>
          </div>
          <p className="text-xs text-muted-foreground mb-4">
            {t(
              "Upload a CDR file. Everything after that is automatic and observable below.",
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={cdrType}
              onChange={setCdrType}
              options={profiles.map((p) => ({
                value: p.code,
                label: `${p.code} — ${p.label}`,
              }))}
              minWidth={240}
              ariaLabel={t("CDR type")}
            />
            <input
              value={sourceSystem}
              onChange={(e) => setSourceSystem(e.target.value.toUpperCase())}
              placeholder={t("Source system")}
              className="h-9 w-56 rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.tsv,.json,.xml"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={start.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
            >
              {start.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {t("Upload and run")}
            </button>
          </div>
        </section>
      )}

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {runs.length === 0 && !isLoading && (
        <RatingEmpty
          icon={Workflow}
          title="No pipeline runs yet"
          description="Upload a CDR file to land, normalize, enrich, rate and assure it in one pass."
        />
      )}

      {/* --- The stage rail ------------------------------------------------- */}
      {run && (
        <section className="bg-card border border-border rounded-xl overflow-hidden mb-6">
          <div className="px-5 py-4 border-b border-border flex flex-wrap items-center gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-foreground truncate">
                {run.filename}
              </div>
              <div className="text-[11px] text-muted-foreground">
                {run.source_system} · {run.cdr_type} · {run.total_records}{" "}
                {t("records")}
                {run.snapshot_version
                  ? ` · ${t("snapshot")} v${run.snapshot_version}`
                  : ""}
              </div>
            </div>
            <span
              className={`ml-auto text-[11px] font-medium px-2 py-0.5 rounded-md border ${
                RUN_TONE[run.status] ?? RUN_TONE.QUEUED
              }`}
            >
              {run.status.replace(/_/g, " ")}
            </span>
          </div>

          {run.error && (
            <div className="px-5 py-3 bg-destructive/5 border-b border-border text-sm text-destructive">
              {run.error}
            </div>
          )}

          <ol className="divide-y divide-border">
            {run.stages.map((stage, i) => {
              const spec = describe(stage.key);
              return (
                <li
                  key={stage.key}
                  className="px-5 py-3 flex items-start gap-3"
                >
                  <div className="flex flex-col items-center pt-0.5">
                    <StageIcon status={stage.status} />
                    {i < run.stages.length - 1 && (
                      <span className="w-px flex-1 min-h-6 bg-border mt-1" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-medium text-foreground">
                        {stage.label}
                      </span>
                      {stage.duration_ms !== null && (
                        <span className="text-[11px] text-muted-foreground tabular-nums">
                          {Math.round(stage.duration_ms)}ms
                        </span>
                      )}
                      {stage.records_failed > 0 && (
                        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded border bg-warning/15 text-warning-foreground border-warning/30">
                          {stage.records_failed} {t("not processed")}
                        </span>
                      )}
                    </div>
                    {spec && (
                      <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed max-w-3xl">
                        {spec.description}
                      </p>
                    )}
                    {stage.detail && (
                      <div className="text-[12px] text-foreground mt-1">
                        {stage.detail}
                      </div>
                    )}
                    {stage.error && (
                      <div className="text-[12px] text-destructive mt-1">
                        {stage.error}
                      </div>
                    )}
                    {stage.status !== "PENDING" && spec && (
                      <div className="flex gap-4 mt-1 text-[11px] text-muted-foreground tabular-nums">
                        <span>
                          {spec.input_label}: {stage.records_in}
                        </span>
                        <span>
                          {spec.output_label}: {stage.records_out}
                        </span>
                      </div>
                    )}
                  </div>
                  {canEdit &&
                    (stage.status === "FAILED" || stage.status === "SKIPPED") &&
                    run.status !== "RUNNING" && (
                      <Tooltip
                        label={t(
                          "Re-run this stage and everything after it, without re-uploading.",
                        )}
                        side="left"
                      >
                        <button
                          onClick={() =>
                            retry.mutateAsync(stage.key).then(
                              () =>
                                toast.success(
                                  `${t("Restarting from")} ${stage.label}`,
                                ),
                              (e) =>
                                toast.error(t("Could not restart"), {
                                  description: ratingError(e),
                                }),
                            )
                          }
                          disabled={retry.isPending}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition disabled:opacity-50 shrink-0"
                        >
                          <RotateCcw className="h-3.5 w-3.5" /> {t("Retry")}
                        </button>
                      </Tooltip>
                    )}
                </li>
              );
            })}
          </ol>

          {run.summary?.match_rate !== undefined && (
            <div className="px-5 py-4 border-t border-border bg-muted/20 grid grid-cols-2 sm:grid-cols-5 gap-4">
              {[
                [t("Match rate"), `${run.summary.match_rate}%`],
                [t("Expected"), money(run.summary.expected_revenue)],
                [t("Billed"), money(run.summary.billed_revenue)],
                [t("Leakage"), money(run.summary.revenue_leakage)],
                [t("Overcharge"), money(run.summary.customer_overcharge)],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    {label}
                  </div>
                  <div className="text-sm font-semibold text-foreground tabular-nums">
                    {value}
                  </div>
                </div>
              ))}
            </div>
          )}

          {run.exception_count > 0 && (
            <div className="px-5 py-3 border-t border-border">
              <Link
                to="/rating/exceptions"
                className="text-xs font-medium text-primary hover:underline inline-flex items-center gap-1"
              >
                {run.exception_count} {t("exception groups to investigate")}
                <ChevronRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          )}
        </section>
      )}

      {/* --- Run history ---------------------------------------------------- */}
      {runs.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <h2 className="text-sm font-semibold text-foreground">
              {t("Recent runs")}
            </h2>
          </div>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border">
              {runs.map((r) => {
                const done = r.stages.filter(
                  (s) => s.status === "COMPLETED",
                ).length;
                return (
                  <tr
                    key={r.id}
                    onClick={() => setSelectedId(r.id)}
                    className={`cursor-pointer transition-colors ${
                      currentId === r.id ? "bg-primary/5" : "hover:bg-muted/30"
                    }`}
                  >
                    <td className="px-5 py-3">
                      <div className="text-foreground">{r.filename}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {r.source_system} · {r.triggered_by_name ?? "—"} ·{" "}
                        {new Date(r.created_at).toLocaleString()}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground whitespace-nowrap">
                      {done}/{r.stages.length} {t("stages")}
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums text-muted-foreground whitespace-nowrap">
                      {r.summary?.revenue_leakage !== undefined
                        ? `${t("leak")} ${money(r.summary.revenue_leakage)}`
                        : "—"}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <span
                        className={`text-[11px] font-medium px-2 py-0.5 rounded-md border ${
                          RUN_TONE[r.status] ?? RUN_TONE.QUEUED
                        }`}
                      >
                        {r.status.replace(/_/g, " ")}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </AppShell>
  );
}

// Status counts and per-source breakdown, laid out exactly as the Mediation
// Assurance job monitor so the two scopes read the same way.
function PipelineStatusSummary({ runs }: { runs: PipelineRun[] }) {
  const t = useT();

  const completed = runs.filter(
    (r) => r.status === "COMPLETED" || r.status === "COMPLETED_WITH_ISSUES",
  ).length;
  const inProgress = runs.filter(
    (r) => r.status === "RUNNING" || r.status === "QUEUED",
  ).length;
  const failed = runs.filter((r) => r.status === "FAILED").length;

  // Upstream-source observations, counted as "how many runs were affected" —
  // these are data problems in the file, not pipeline failures.
  const withRejects = runs.filter((r) =>
    (r.stages ?? []).some((st) => st.records_failed > 0),
  ).length;
  const emptyRuns = runs.filter((r) => (r.total_records ?? 0) === 0).length;
  const withExceptions = runs.filter(
    (r) => (r.exception_count ?? 0) > 0,
  ).length;

  const cards = [
    {
      icon: CheckCircle2,
      label: t("Completed"),
      value: completed,
      hint: t("Ran end-to-end"),
      border: "border-l-success",
      text: "text-success",
    },
    {
      icon: Loader2,
      label: t("In progress"),
      value: inProgress,
      hint: t("Currently running"),
      border: "border-l-[#F8C800]",
      text: "text-[#F8C800]",
    },
    {
      icon: XCircle,
      label: t("Failed"),
      value: failed,
      hint: t("Needs investigation"),
      border: "border-l-[#EF4444]",
      text: "text-[#EF4444]",
    },
  ];

  // Per source system, split by status — the equivalent of the mediation
  // stream breakdown.
  const bySource = Object.values(
    runs.reduce(
      (acc, r) => {
        const name = `${r.source_system} ${r.cdr_type}`;
        const stat =
          acc[name] ??
          (acc[name] = {
            name,
            total: 0,
            completed: 0,
            inProgress: 0,
            failed: 0,
          });
        stat.total++;
        if (r.status === "FAILED") stat.failed++;
        else if (r.status === "RUNNING" || r.status === "QUEUED")
          stat.inProgress++;
        else stat.completed++;
        return acc;
      },
      {} as Record<
        string,
        {
          name: string;
          total: number;
          completed: number;
          inProgress: number;
          failed: number;
        }
      >,
    ),
  ).sort((a, b) => b.total - a.total);
  const sourceMax = bySource.length ? bySource[0].total : 1;

  return (
    <div className="space-y-4 mb-6">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <div
              key={c.label}
              className={`bg-card border border-border ${c.border} border-l-4 rounded-xl p-5 shadow-sm`}
            >
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium text-muted-foreground">
                  {c.label}
                </div>
                <Icon className={`h-4 w-4 ${c.text}`} />
              </div>
              <div
                className={`mt-2 text-3xl font-semibold tracking-tight ${c.text}`}
              >
                {c.value.toLocaleString()}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{c.hint}</div>
            </div>
          );
        })}
      </div>

      <div className="bg-card border border-border rounded-xl p-4 shadow-sm">
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground">
            {t("Source data quality observed across")}{" "}
            {runs.length.toLocaleString()} {t("runs")}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <span>
            <span className="font-semibold text-foreground">
              {withRejects.toLocaleString()}
            </span>{" "}
            {t("runs with rejected records")}
          </span>
          <span>
            <span className="font-semibold text-foreground">
              {emptyRuns.toLocaleString()}
            </span>{" "}
            {t("with no records")}
          </span>
          <span>
            <span className="font-semibold text-foreground">
              {withExceptions.toLocaleString()}
            </span>{" "}
            {t("with rating exceptions")}
          </span>
        </div>
        <div className="mt-2 text-[11px] text-muted-foreground/80">
          {t("These are upstream-source observations, not pipeline failures.")}
        </div>
      </div>

      {bySource.length > 0 && (
        <>
          <h2 className="text-base font-semibold text-foreground pt-2">
            {t("Run Status")}
          </h2>
          <div className="bg-card border border-border rounded-xl p-5 shadow-sm">
            <div className="text-sm font-semibold text-foreground">
              {t("Source breakdown")}
            </div>
            <div className="text-3xl font-semibold text-foreground tracking-tight mt-2">
              {runs.length.toLocaleString()}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {t("total runs")}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 text-xs">
              <StatusLegend
                color="bg-success"
                value={completed}
                label={t("Completed")}
              />
              <StatusLegend
                color="bg-[#F8C800]"
                value={inProgress}
                label={t("In progress")}
              />
              <StatusLegend
                color="bg-[#EF4444]"
                value={failed}
                label={t("Failed")}
              />
            </div>

            <div className="mt-4 pt-4 border-t border-border space-y-3">
              {bySource.map((s) => (
                <div key={s.name}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-foreground">{s.name}</span>
                    <span className="text-sm font-semibold text-foreground tabular-nums">
                      {s.total.toLocaleString()}
                    </span>
                  </div>
                  <div className="h-3.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className="flex h-full rounded-full overflow-hidden"
                      style={{ width: `${(s.total / sourceMax) * 100}%` }}
                    >
                      {[
                        { value: s.completed, color: "bg-success" },
                        { value: s.inProgress, color: "bg-[#F8C800]" },
                        { value: s.failed, color: "bg-[#EF4444]" },
                      ]
                        .filter((seg) => seg.value > 0)
                        .map((seg, i) => (
                          <div
                            key={i}
                            className={seg.color}
                            style={{
                              width: `${(seg.value / s.total) * 100}%`,
                            }}
                          />
                        ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatusLegend({
  color,
  value,
  label,
}: {
  color: string;
  value: number;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
      <span className="font-semibold text-foreground tabular-nums">
        {value.toLocaleString()}
      </span>
      <span className="text-muted-foreground">{label}</span>
    </span>
  );
}
