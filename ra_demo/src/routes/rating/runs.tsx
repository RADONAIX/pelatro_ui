import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Layers,
  Loader2,
  PlayCircle,
  TrendingDown,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useActiveSnapshot,
  useCanEditRuns,
  useCdrBatches,
  useCdrProfiles,
  useIngestCdrBatch,
  useRatingRuns,
  useRunSummary,
  useStartRatingRun,
} from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/runs")({
  component: RatingRunsPage,
});

const money = (n: number) =>
  n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

function RatingRunsPage() {
  const t = useT();
  const canEdit = useCanEditRuns();
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: batches = [], isLoading, error, refetch } = useCdrBatches();
  const { data: profiles = [] } = useCdrProfiles();
  const { data: active } = useActiveSnapshot();
  const { data: runs = [] } = useRatingRuns();

  const ingest = useIngestCdrBatch();
  const startRun = useStartRatingRun();

  const [cdrType, setCdrType] = useState("MSC");
  const [sourceSystem, setSourceSystem] = useState("MSC_SWITCH_01");
  const [selectedRun, setSelectedRun] = useState<string | undefined>();
  const { data: summary } = useRunSummary(selectedRun ?? runs[0]?.id);

  const onPick = async (file: File | null) => {
    if (!file) return;
    try {
      const batch = await ingest.mutateAsync({
        file,
        cdr_type: cdrType,
        source_system: sourceSystem,
      });
      toast.success(t("CDR batch ingested"), {
        description: `${batch.loaded_records} ${t("loaded")}, ${batch.duplicate_records} ${t("duplicate")}, ${batch.rejected_records} ${t("rejected")}`,
      });
    } catch (err) {
      toast.error(t("Ingestion failed"), { description: ratingError(err) });
    }
    if (fileInput.current) fileInput.current.value = "";
  };

  const rate = async (batchId: string) => {
    try {
      const run = await startRun.mutateAsync({ batch_id: batchId });
      setSelectedRun(run.id);
      if (run.status === "FAILED") {
        toast.error(t("Rating run failed"), {
          description: run.error ?? undefined,
        });
      } else {
        toast.success(t("Rating complete"), {
          description: `${run.rated_cdrs} ${t("CDRs via")} ${run.distinct_contexts} ${t("context decisions")}`,
        });
      }
    } catch (err) {
      toast.error(t("Could not start the run"), {
        description: ratingError(err),
      });
    }
  };

  return (
    <AppShell>
      <PageHeader
        title={t("Rating Runs")}
        description={t(
          "Ingest a CDR batch, then rate it against the active snapshot. Every CDR gets its own result, but rule resolution runs once per distinct rating context.",
        )}
        info={t(
          "A run records which snapshot it used, so its results stay explainable even after newer rules are published.",
        )}
      />

      {!active && (
        <div className="rounded-xl border border-warning/30 bg-warning/15 p-4 mb-6 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5 text-warning-foreground" />
          <div>
            <div className="text-sm font-semibold text-foreground">
              {t("No snapshot is active")}
            </div>
            <p className="text-sm text-muted-foreground mt-0.5">
              {t(
                "Nothing can be rated until a snapshot is compiled and activated.",
              )}{" "}
              <Link
                to="/rating/snapshots"
                className="text-primary hover:underline"
              >
                {t("Go to snapshots")}
              </Link>
            </p>
          </div>
        </div>
      )}

      {summary && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
            <StatTile
              icon={CheckCircle2}
              label={t("Match rate")}
              value={`${summary.match_rate}%`}
            />
            <StatTile
              icon={TrendingDown}
              label={t("Revenue leakage")}
              value={money(summary.revenue_leakage)}
            />
            <StatTile
              icon={AlertTriangle}
              label={t("Customer overcharge")}
              value={money(summary.customer_overcharge)}
            />
            <StatTile
              icon={Layers}
              label={t("Context decisions")}
              value={String(summary.distinct_contexts)}
            />
          </div>

          <section className="bg-card border border-border rounded-xl p-5 mb-6">
            <div className="flex flex-wrap items-baseline justify-between gap-3 mb-4">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Expected vs billed")} · {t("snapshot")} v
                {summary.snapshot_version}
              </h2>
              <span className="text-xs text-muted-foreground">
                {summary.rated_cdrs} {t("CDRs rated")}
              </span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-5">
              {[
                [t("Expected revenue"), money(summary.expected_revenue)],
                [t("Billed revenue"), money(summary.billed_revenue)],
                [t("Undercharged"), money(summary.revenue_leakage)],
                [t("Overcharged"), money(summary.customer_overcharge)],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    {label}
                  </div>
                  <div className="text-lg font-semibold text-foreground tabular-nums">
                    {value}
                  </div>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              {summary.by_status.map((s) => (
                <Link
                  key={s.status}
                  to="/rating/exceptions"
                  className="text-[11px] rounded-md border border-border bg-muted/40 px-2 py-1 text-muted-foreground hover:bg-muted transition"
                >
                  {s.status}
                  <span className="ml-1.5 font-semibold text-foreground tabular-nums">
                    {s.count}
                  </span>
                </Link>
              ))}
            </div>
          </section>
        </>
      )}

      {canEdit && (
        <section className="bg-card border border-border rounded-xl p-5 mb-6">
          <h2 className="text-sm font-semibold text-foreground mb-1">
            {t("Ingest a CDR batch")}
          </h2>
          <p className="text-xs text-muted-foreground mb-4">
            {t(
              "Landed, normalized and enriched in one step. Duplicates and unparseable records are recorded, never dropped.",
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
              minWidth={230}
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
              accept=".csv,.tsv,.json"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={ingest.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
            >
              {ingest.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {t("Upload CDRs")}
            </button>
          </div>
        </section>
      )}

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {batches.length === 0 && !isLoading && (
        <RatingEmpty
          icon={Database}
          title="No CDR batches yet"
          description="Upload a CDR file to land, normalize and enrich it. Then rate it against the active snapshot."
        />
      )}

      {batches.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden mb-6">
          <div className="px-5 py-4 border-b border-border">
            <h2 className="text-sm font-semibold text-foreground">
              {t("CDR batches")}
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-left">
                  {[
                    t("File"),
                    t("Type"),
                    t("Loaded"),
                    t("Dup"),
                    t("Rejected"),
                    t("Contexts"),
                    t("Quality"),
                    "",
                  ].map((h, i) => (
                    <th
                      key={i}
                      className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground whitespace-nowrap"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {batches.map((b) => {
                  const quality = b.summary.quality ?? {};
                  const problems = Object.entries(quality).filter(
                    ([k]) => k !== "OK",
                  );
                  return (
                    <tr
                      key={b.id}
                      className="hover:bg-muted/30 transition-colors"
                    >
                      <td className="px-4 py-3">
                        <div className="text-foreground">{b.filename}</div>
                        <div className="text-[11px] text-muted-foreground">
                          {b.source_system} · {b.event_date ?? "—"}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {b.cdr_type}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {b.loaded_records}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {b.duplicate_records}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {b.rejected_records}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {b.summary.distinct_contexts ?? "—"}
                        {b.summary.cdrs_per_context && (
                          <div className="text-[10px]">
                            {b.summary.cdrs_per_context} {t("per decision")}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-[11px] text-muted-foreground">
                        {problems.length
                          ? problems.map(([k, v]) => `${k}: ${v}`).join(", ")
                          : t("clean")}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {canEdit && !!active && (
                          <button
                            onClick={() => rate(b.id)}
                            disabled={startRun.isPending}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition disabled:opacity-50"
                          >
                            {startRun.isPending ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <PlayCircle className="h-3.5 w-3.5" />
                            )}
                            {t("Rate")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {runs.length > 0 && (
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-border flex items-center gap-2">
            <Activity className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              {t("Runs")}
            </h2>
          </div>
          <table className="w-full text-sm">
            <tbody className="divide-y divide-border">
              {runs.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setSelectedRun(r.id)}
                  className={`cursor-pointer transition-colors ${
                    (selectedRun ?? runs[0]?.id) === r.id
                      ? "bg-primary/5"
                      : "hover:bg-muted/30"
                  }`}
                >
                  <td className="px-5 py-3">
                    <div className="text-foreground">
                      {t("Snapshot")} v{r.snapshot_version} · {r.status}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      {r.triggered_by_name ?? "—"} ·{" "}
                      {new Date(r.created_at).toLocaleString()}
                    </div>
                    {r.error && (
                      <div className="text-[11px] text-destructive mt-0.5">
                        {r.error}
                      </div>
                    )}
                  </td>
                  <td className="px-5 py-3 text-muted-foreground whitespace-nowrap">
                    {r.rated_cdrs} {t("CDRs")} / {r.distinct_contexts}{" "}
                    {t("contexts")}
                  </td>
                  <td className="px-5 py-3 text-right tabular-nums text-muted-foreground whitespace-nowrap">
                    {t("leak")} {money(r.undercharge_total)}
                  </td>
                  <td className="px-5 py-3 text-right text-muted-foreground whitespace-nowrap">
                    {r.stats?.duration_ms
                      ? `${Math.round(r.stats.duration_ms)}ms`
                      : ""}
                  </td>
                  <td className="px-5 py-3 text-right whitespace-nowrap">
                    <Link
                      to="/rating/runs/$runId"
                      params={{ runId: r.id }}
                      onClick={(e) => e.stopPropagation()}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      {t("Detail")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </AppShell>
  );
}
