import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  Database,
  Package,
  Plug,
  Workflow,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import { ratingApi } from "@/lib/rating/api";
import { usePipelineRuns, useSourceSystems } from "@/lib/rating/hooks";
import { fmtDateTime, statusTone } from "@/lib/rating/format";
import { RatingLoading } from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/monitoring")({
  component: MonitoringPage,
});

interface ServiceHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
  rating_db: boolean;
  identity_db: boolean;
  clickhouse_enabled: boolean;
  airflow_enabled: boolean;
}

interface SnapshotStats {
  total_snapshots: number;
  active_version: number | null;
  active_rule_count: number | null;
  active_checksum: string | null;
}

function useServiceHealth() {
  return useQuery({
    queryKey: ["rating", "service-health"],
    queryFn: async () => (await ratingApi.get<ServiceHealth>("/health")).data,
    refetchInterval: 30_000,
  });
}

function useSnapshotStats() {
  return useQuery({
    queryKey: ["rating", "snapshot-stats"],
    queryFn: async () =>
      (await ratingApi.get<SnapshotStats>("/rule-snapshots/stats")).data,
    staleTime: 30_000,
  });
}

function HealthDot({ ok }: { ok: boolean }) {
  return ok ? (
    <CheckCircle2 className="h-4 w-4 text-success" />
  ) : (
    <XCircle className="h-4 w-4 text-destructive" />
  );
}

function MonitoringPage() {
  const t = useT();
  const { data: health, isLoading } = useServiceHealth();
  const { data: snapshots } = useSnapshotStats();
  const { data: sources = [] } = useSourceSystems();
  const { data: pipelines = [] } = usePipelineRuns();

  const failedPipelines = pipelines.filter((p) => p.status === "FAILED");
  const recentPipelines = pipelines.slice(0, 8);

  return (
    <AppShell>
      <PageHeader
        title={t("System Monitoring")}
        description={t(
          "The rating service's own vitals: databases, the active rule snapshot, connector health and recent pipeline jobs — refreshed every 30 seconds.",
        )}
        info={t(
          "This watches the Rating Assurance service only; it is deliberately isolated from the main platform's monitoring.",
        )}
      />

      {isLoading && <RatingLoading label="Checking service health…" />}

      {health && (
        <div className="space-y-6">
          {/* --- Service vitals -------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <Activity className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">
                {health.service}
              </h2>
              <span
                className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${statusTone(health.status === "ok" ? "COMPLETED" : "FAILED")}`}
              >
                {health.status.toUpperCase()}
              </span>
              <span className="text-[11px] text-muted-foreground ml-auto">
                v{health.version} · {health.environment}
              </span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              {[
                {
                  label: t("Rating database"),
                  ok: health.rating_db,
                  icon: Database,
                  note: t("Schema 'rating' — reads and writes"),
                },
                {
                  label: t("Identity database"),
                  ok: health.identity_db,
                  icon: Database,
                  note: t("Read-only — verifies users, never writes"),
                },
                {
                  label: "ClickHouse",
                  ok: health.clickhouse_enabled,
                  icon: Database,
                  note: health.clickhouse_enabled
                    ? t("Bulk execution plane")
                    : t("Not configured — Postgres executes runs"),
                  neutral: !health.clickhouse_enabled,
                },
                {
                  label: "Airflow",
                  ok: health.airflow_enabled,
                  icon: Workflow,
                  note: health.airflow_enabled
                    ? t("Orchestrating pipelines")
                    : t("Not configured — in-process runner active"),
                  neutral: !health.airflow_enabled,
                },
              ].map((item) => (
                <div
                  key={item.label}
                  className="rounded-lg border border-border p-3 flex items-start gap-3"
                >
                  {item.neutral ? (
                    <item.icon className="h-4 w-4 text-muted-foreground mt-0.5" />
                  ) : (
                    <HealthDot ok={item.ok} />
                  )}
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-foreground">
                      {item.label}
                    </div>
                    <div className="text-[11px] text-muted-foreground leading-relaxed">
                      {item.note}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* --- Active snapshot ------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <Package className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">
                {t("Active rule snapshot")}
              </h2>
              <Link
                to="/rating/snapshots"
                className="ml-auto text-xs font-medium text-primary hover:underline"
              >
                {t("Manage")}
              </Link>
            </div>
            {snapshots?.active_version ? (
              <div className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
                <div>
                  <span className="text-lg font-semibold text-foreground">
                    v{snapshots.active_version}
                  </span>
                  <span className="text-sm text-muted-foreground ml-2">
                    {snapshots.active_rule_count} {t("rules")}
                  </span>
                </div>
                <code className="text-[11px] text-muted-foreground">
                  {snapshots.active_checksum?.slice(0, 16)}…
                </code>
                <span className="text-[12px] text-muted-foreground">
                  {snapshots.total_snapshots} {t("versions kept")}
                </span>
              </div>
            ) : (
              <p className="text-sm text-warning-foreground">
                {t("No active snapshot — rating runs cannot start.")}
              </p>
            )}
          </section>

          {/* --- Connector health ------------------------------------------ */}
          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center gap-2">
              <Plug className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">
                {t("Source system connectors")}
              </h2>
              <Link
                to="/rating/connectors"
                className="ml-auto text-xs font-medium text-primary hover:underline"
              >
                {t("Manage")}
              </Link>
            </div>
            {sources.length === 0 ? (
              <p className="px-5 py-6 text-sm text-muted-foreground">
                {t("No connectors configured yet.")}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {sources.map((sys) => (
                  <li
                    key={sys.id}
                    className="px-5 py-3 flex items-center gap-3"
                  >
                    <HealthDot ok={sys.health_status === "HEALTHY"} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-foreground">
                        {sys.name}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {sys.vendor} · {sys.source_type}
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${statusTone(sys.health_status === "HEALTHY" ? "COMPLETED" : "FAILED")}`}
                    >
                      {sys.health_status}
                    </span>
                    <span className="text-[11px] text-muted-foreground w-40 text-right">
                      {sys.last_import_at
                        ? fmtDateTime(sys.last_import_at)
                        : t("never imported")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* --- Pipeline jobs --------------------------------------------- */}
          <section className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center gap-2">
              <Workflow className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold text-foreground">
                {t("Recent pipeline jobs")}
              </h2>
              {failedPipelines.length > 0 && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md border bg-destructive/10 text-destructive border-destructive/20">
                  {failedPipelines.length} {t("failed")}
                </span>
              )}
              <Link
                to="/rating/pipeline"
                className="ml-auto text-xs font-medium text-primary hover:underline"
              >
                {t("Open monitor")}
              </Link>
            </div>
            {recentPipelines.length === 0 ? (
              <p className="px-5 py-6 text-sm text-muted-foreground">
                {t("No pipeline jobs have run yet.")}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {recentPipelines.map((run) => (
                  <li
                    key={run.id}
                    className="px-5 py-3 flex items-center gap-3"
                  >
                    <span
                      className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border shrink-0 ${statusTone(run.status)}`}
                    >
                      {run.status}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-foreground truncate">
                        {run.filename}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {run.source_system} · {run.total_records} {t("records")}
                        {run.error && (
                          <span className="text-destructive ml-2">
                            {run.error}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="text-[11px] text-muted-foreground w-40 text-right">
                      {fmtDateTime(run.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </AppShell>
  );
}
