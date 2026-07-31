/**
 * Snapshot details — what was rating, and why.
 *
 * A snapshot is the answer to "what was rating on the 14th", which makes this
 * the page an operator opens when a charge is disputed. It is organised around
 * the questions they arrive with rather than around the tables behind it:
 *
 * 1. **Summary** — is this the one that was live, and can I trust it?
 * 2. **Rules included** — what is actually in it, in engine order.
 * 3. **Compared with the previous** — what changed, which is usually the
 *    question behind "why did this start costing more?"
 * 4. **Validation & compile report** — was it clean, or forced past its errors?
 * 5. **Impact** — who does it touch, measured against rated traffic.
 * 6. **Export** — hand the whole thing to a regulator or a vendor.
 * 7. **Execution order** — the sequence the engine walks, stage by stage.
 *
 * Each section loads independently. Impact measures against rated CDRs and is by
 * far the slowest; folding it into one request would make the rule list — the
 * thing most visits are actually for — wait behind it.
 */
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileCode2,
  FileJson,
  FileSpreadsheet,
  GitCompare,
  Layers,
  ListOrdered,
  ShieldCheck,
  Target,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { Select } from "@/components/ui-kit/Select";
import { useT } from "@/lib/i18n";
import {
  snapshotExportUrl,
  useSnapshot,
  useSnapshotDiffWithPrevious,
  useSnapshotExecutionOrder,
  useSnapshotImpact,
  useSnapshotReport,
  useSnapshotRules,
} from "@/lib/rating/hooks";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { ExecutableRule } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/snapshots_/$snapshotId")({
  component: SnapshotDetailPage,
});

const CHANGE_TONE: Record<string, string> = {
  ADDED: "bg-success/10 text-success border-success/20",
  REMOVED: "bg-destructive/10 text-destructive border-destructive/20",
  CHANGED: "bg-warning/10 text-warning border-warning/20",
  UNCHANGED: "bg-muted text-muted-foreground border-border",
};

function SnapshotDetailPage() {
  const t = useT();
  const { snapshotId } = Route.useParams();

  const { data: snapshot, isLoading, error, refetch } = useSnapshot(snapshotId);
  const [stage, setStage] = useState("");
  const { data: rules = [] } = useSnapshotRules(snapshotId, {
    execution_stage: stage,
  });
  const { data: report } = useSnapshotReport(snapshotId);
  const { data: diff } = useSnapshotDiffWithPrevious(snapshotId);
  const { data: order = [] } = useSnapshotExecutionOrder(snapshotId);
  const { data: impact } = useSnapshotImpact(snapshotId);

  if (isLoading)
    return (
      <AppShell>
        <RatingLoading />
      </AppShell>
    );
  if (error)
    return (
      <AppShell>
        <RatingError error={error} onRetry={() => void refetch()} />
      </AppShell>
    );
  if (!snapshot)
    return (
      <AppShell>
        <RatingEmpty
          icon={Layers}
          title="That snapshot doesn't exist"
          description="It may have been removed, or the link is wrong."
        />
      </AppShell>
    );

  const stageOptions = [
    { value: "", label: t("Every stage") },
    ...order.map((g) => ({
      value: g.stage,
      label: `${g.stage} (${g.rule_count})`,
    })),
  ];

  return (
    <AppShell>
      <PageHeader
        title={`${t("Snapshot")} v${snapshot.version}`}
        description={snapshot.name}
        actions={
          <Link
            to="/rating/snapshots"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
          >
            <ArrowLeft className="h-4 w-4" /> {t("All snapshots")}
          </Link>
        }
      />

      {/* --- 1. Summary ---------------------------------------------------- */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile
          icon={Layers}
          label={t("Rules")}
          value={String(snapshot.rule_count)}
        />
        <StatTile
          icon={Target}
          label={t("Products")}
          value={String(snapshot.product_count)}
        />
        <StatTile
          icon={ShieldCheck}
          label={t("Status")}
          value={snapshot.status}
        />
        <StatTile
          icon={CheckCircle2}
          label={t("Compiled by")}
          value={snapshot.compiled_by_name ?? "—"}
        />
      </div>

      <Section title={t("Summary")} className="mt-6">
        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-3 p-5">
          <Field label={t("Effective")}>
            {snapshot.effective_from}
            {snapshot.effective_to ? ` → ${snapshot.effective_to}` : ""}
          </Field>
          <Field label={t("Compiled at")}>
            {new Date(snapshot.created_at).toLocaleString()}
          </Field>
          <Field label={t("Activated")}>
            {snapshot.activated_at
              ? new Date(snapshot.activated_at).toLocaleString()
              : "—"}
          </Field>
          <Field label={t("Superseded")}>
            {snapshot.superseded_at
              ? new Date(snapshot.superseded_at).toLocaleString()
              : "—"}
          </Field>
          {/* The checksum is shown in full, not truncated: it is the thing you
              quote to prove two systems were running the same rules. */}
          <Field label={t("Checksum")} className="sm:col-span-2 lg:col-span-1">
            <span className="font-mono text-[11px] break-all">
              {snapshot.checksum}
            </span>
          </Field>
          {snapshot.description && (
            <Field label={t("Description")} className="sm:col-span-2">
              {snapshot.description}
            </Field>
          )}
        </dl>
      </Section>

      {/* --- 4. Validation & compile report --------------------------------- */}
      {report && (
        <Section title={t("Validation & compile report")} className="mt-6">
          <div className="p-5 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              {report.safe_to_activate ? (
                <span className="inline-flex items-center gap-1.5 text-sm text-success">
                  <CheckCircle2 className="h-4 w-4" />
                  {t("Compiled clean")}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-sm text-destructive">
                  <XCircle className="h-4 w-4" />
                  {report.forced
                    ? t("Forced past blocking issues")
                    : t("Has blocking issues")}
                </span>
              )}
              <span className="text-xs text-muted-foreground">
                {report.error_count} {t("errors")} · {report.warning_count}{" "}
                {t("warnings")}
              </span>
            </div>

            {report.forced && (
              <p className="text-[12px] text-warning flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                {t(
                  "Somebody compiled this past its own validation errors. The override is recorded here on purpose — a forced snapshot is the first thing to check when rating looks wrong.",
                )}
              </p>
            )}

            {/* Grouped by cause. Thirty-two rules failing one check is one fix. */}
            {report.grouped_issues.map((g, i) => (
              <div
                key={`${g.code}-${i}`}
                className="rounded-lg border border-border p-3"
              >
                <p className="text-[13px] text-foreground">
                  <span className="tabular-nums font-semibold">×{g.count}</span>{" "}
                  <span
                    className={
                      g.severity === "ERROR"
                        ? "text-destructive"
                        : "text-warning"
                    }
                  >
                    {g.code}
                  </span>{" "}
                  — {g.message}
                </p>
                {g.hint && (
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {g.hint}
                  </p>
                )}
                {g.examples && g.examples.length > 0 && (
                  <p className="text-[11px] font-mono text-muted-foreground mt-1 break-all">
                    {g.examples.join(", ")}
                  </p>
                )}
              </div>
            ))}
            {report.grouped_issues.length === 0 && (
              <p className="text-sm text-muted-foreground">
                {t("No validation issues were recorded for this compile.")}
              </p>
            )}
          </div>
        </Section>
      )}

      {/* --- 3. Compared with the previous snapshot ------------------------- */}
      <Section
        title={t("Compared with the previous snapshot")}
        icon={GitCompare}
        className="mt-6"
      >
        {!diff ? (
          <p className="p-5 text-sm text-muted-foreground">{t("Loading…")}</p>
        ) : diff.from_snapshot === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">
            {t("This is the first snapshot — there is nothing before it.")}
          </p>
        ) : (
          <>
            <div className="px-5 py-4 flex items-center gap-2 flex-wrap text-sm">
              <span className="text-muted-foreground">
                v{diff.from_snapshot} → v{diff.to_snapshot}
              </span>
              <Pill tone="ADDED">
                {diff.added} {t("added")}
              </Pill>
              <Pill tone="REMOVED">
                {diff.removed} {t("removed")}
              </Pill>
              <Pill tone="CHANGED">
                {diff.changed} {t("changed")}
              </Pill>
              <Pill tone="UNCHANGED">
                {diff.unchanged} {t("unchanged")}
              </Pill>
              {diff.identical && (
                <span className="text-xs text-muted-foreground">
                  {t("— the two are identical")}
                </span>
              )}
            </div>
            {diff.entries.length > 0 && (
              <div className="overflow-x-auto border-t border-border max-h-96">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-muted/90 backdrop-blur">
                    <tr className="border-b border-border text-left">
                      <Th>{t("Rule")}</Th>
                      <Th>{t("Change")}</Th>
                      <Th>{t("Version")}</Th>
                      <Th>{t("What differs")}</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {diff.entries.map((e) => (
                      <tr key={`${e.rule_key}-${e.change}`}>
                        <td className="px-4 py-2.5 font-mono text-[12px] text-foreground align-top">
                          {e.rule_key}
                        </td>
                        <td className="px-4 py-2.5 align-top">
                          <Pill tone={e.change}>{e.change}</Pill>
                        </td>
                        <td className="px-4 py-2.5 align-top tabular-nums text-muted-foreground">
                          {e.from_version ?? "—"} → {e.to_version ?? "—"}
                        </td>
                        <td className="px-4 py-2.5 align-top text-[12px] text-muted-foreground">
                          {e.details.length ? e.details.join("; ") : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Section>

      {/* --- 5. Impact ------------------------------------------------------ */}
      {impact && (
        <Section title={t("Impact analysis")} icon={Target} className="mt-6">
          <div className="p-5 space-y-4">
            {impact.note && (
              <p className="text-[12px] text-muted-foreground">{impact.note}</p>
            )}

            {impact.traffic && (
              <div className="rounded-lg border border-border p-4">
                {impact.traffic.has_traffic ? (
                  <>
                    <p className="text-sm text-foreground">
                      {t("Measured over")} {impact.traffic.window_days}{" "}
                      {t("days")} ({impact.traffic.from_date} →{" "}
                      {impact.traffic.to_date})
                    </p>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-3">
                      <Metric
                        label={t("Rated events")}
                        value={impact.traffic.rated_events.toLocaleString()}
                      />
                      <Metric
                        label={t("Affected")}
                        value={impact.traffic.affected_events.toLocaleString()}
                      />
                      <Metric
                        label={t("Subscribers")}
                        value={impact.traffic.distinct_subscribers.toLocaleString()}
                      />
                      <Metric
                        label={t("Charge affected")}
                        value={`${impact.traffic.affected_charge} ${impact.traffic.currency}`}
                      />
                    </div>
                  </>
                ) : (
                  // Not the same as zero impact, and saying so matters: an
                  // operator who reads "0 affected" concludes the change is
                  // safe, when in fact nothing was measured.
                  <p className="text-sm text-muted-foreground">
                    {t(
                      "There was no rated traffic in the window, so impact could not be measured. This is not the same as no impact.",
                    )}
                  </p>
                )}
              </div>
            )}

            {impact.reach.length > 0 && (
              <div className="space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("What this snapshot reaches")}
                </p>
                {impact.reach.map((r) => (
                  <div
                    key={r.dimension}
                    className="rounded-lg border border-border p-3"
                  >
                    <p className="text-[13px] text-foreground">{r.label}</p>
                    <p className="text-[12px] text-muted-foreground mt-0.5 break-words">
                      {r.values.length ? r.values.join(", ") : t("none named")}
                    </p>
                    {r.wildcard_rules > 0 && (
                      // Counted apart, because a rule matching every zone is a
                      // categorically different risk from one naming forty.
                      <p className="text-[11px] text-warning mt-1">
                        {r.wildcard_rules}{" "}
                        {t("rule(s) match every value on this dimension")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}

      {/* --- 2. Rules included ---------------------------------------------- */}
      <Section
        title={t("Rules in this snapshot")}
        className="mt-6"
        aside={
          <div className="flex items-center gap-2">
            <Select
              value={stage}
              onChange={setStage}
              options={stageOptions}
              size="sm"
              minWidth={200}
            />
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              {rules.length} {t("shown")}
            </span>
          </div>
        }
      >
        {rules.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">
            {t("No compiled rules match this filter.")}
          </p>
        ) : (
          <div className="overflow-x-auto max-h-[36rem]">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/90 backdrop-blur">
                <tr className="border-b border-border text-left">
                  <Th>{t("Rule")}</Th>
                  <Th>{t("Stage")}</Th>
                  <Th className="text-right">{t("Priority")}</Th>
                  <Th className="text-right">{t("Specificity")}</Th>
                  <Th>{t("Matches")}</Th>
                  <Th>{t("Does")}</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rules.map((r) => (
                  <RuleRow key={r.id} rule={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* --- 7. Execution order --------------------------------------------- */}
      <Section title={t("Execution order")} icon={ListOrdered} className="mt-6">
        <div className="p-5 space-y-3">
          <p className="text-[12px] text-muted-foreground">
            {t(
              "The sequence the engine walks. A stage with no rules is shown on purpose — an empty ROUNDING stage is usually the explanation for a fraction-of-a-penny discrepancy.",
            )}
          </p>
          {order.map((g) => (
            <div
              key={g.stage}
              className={`rounded-lg border p-3 ${
                g.rule_count === 0
                  ? "border-dashed border-border opacity-70"
                  : "border-border"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium text-foreground">
                  <span className="tabular-nums text-muted-foreground mr-2">
                    {g.stage_order}
                  </span>
                  {g.stage}
                </span>
                <span className="text-xs text-muted-foreground">
                  {g.rule_count} {t("rules")}
                </span>
              </div>
              {g.rules.length > 0 && (
                <p className="text-[11px] font-mono text-muted-foreground mt-1.5 break-all">
                  {g.rules
                    .map((r) => String(r.rule_key ?? ""))
                    .filter(Boolean)
                    .join(" → ")}
                </p>
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* --- 6. Export ------------------------------------------------------ */}
      <Section title={t("Export")} className="mt-6">
        <div className="p-5">
          <p className="text-[12px] text-muted-foreground mb-3">
            {t(
              "The compiled form — the rules as the engine walks them, not as they were authored. That is what answers “what was rating on the 14th”.",
            )}
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            <ExportLink id={snapshotId} format="csv" icon={FileSpreadsheet} />
            <ExportLink id={snapshotId} format="json" icon={FileJson} />
            <ExportLink id={snapshotId} format="xml" icon={FileCode2} />
          </div>
        </div>
      </Section>
    </AppShell>
  );
}

// --- Pieces -----------------------------------------------------------------

function RuleRow({ rule }: { rule: ExecutableRule }) {
  const t = useT();
  const dims = Object.entries(rule.dimension_sets ?? {}).filter(
    ([, v]) => Array.isArray(v) && v.length > 0,
  );
  return (
    <tr className="hover:bg-muted/30 transition-colors">
      <td className="px-4 py-2.5 align-top">
        <div className="text-foreground">{rule.rule_name}</div>
        <div className="font-mono text-[11px] text-muted-foreground">
          {rule.rule_key} v{rule.rule_version}
        </div>
      </td>
      <td className="px-4 py-2.5 align-top">
        <div className="text-muted-foreground">{rule.execution_stage}</div>
        <div className="text-[11px] text-muted-foreground">
          {rule.rule_type}
        </div>
      </td>
      <td className="px-4 py-2.5 align-top text-right tabular-nums text-muted-foreground">
        {rule.priority}
      </td>
      <td className="px-4 py-2.5 align-top text-right tabular-nums text-muted-foreground">
        {rule.specificity}
      </td>
      <td className="px-4 py-2.5 align-top text-[12px] text-muted-foreground max-w-xs">
        {dims.length === 0 && (rule.predicates?.length ?? 0) === 0 ? (
          // Worth calling out rather than leaving blank: a rule with no
          // conditions matches everything, which is the usual cause of two
          // rules colliding at the same priority.
          <span className="text-warning">{t("everything")}</span>
        ) : (
          <>
            {dims.map(([k, v]) => (
              <div key={k}>
                <span className="text-foreground">{k}</span>:{" "}
                {(v as string[]).join(", ")}
              </div>
            ))}
            {(rule.predicates?.length ?? 0) > 0 && (
              <div>
                {rule.predicates.length} {t("predicate(s)")}
              </div>
            )}
          </>
        )}
      </td>
      <td className="px-4 py-2.5 align-top text-[12px] text-muted-foreground max-w-xs">
        {(rule.actions ?? []).map((a, i) => {
          const action = a as Record<string, unknown>;
          return (
            <div key={i}>
              <span className="text-foreground">
                {String(action.action_type ?? action.type ?? "?")}
              </span>
              {action.rate != null && ` ${String(action.rate)}`}
              {action.unit != null && ` / ${String(action.unit)}`}
            </div>
          );
        })}
      </td>
    </tr>
  );
}

function ExportLink({
  id,
  format,
  icon: Icon,
}: {
  id: string;
  format: "csv" | "json" | "xml";
  icon: typeof Download;
}) {
  return (
    <a
      href={snapshotExportUrl(id, format)}
      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
    >
      <Icon className="h-4 w-4" /> {format.toUpperCase()}
    </a>
  );
}

function Section({
  title,
  icon: Icon,
  aside,
  className = "",
  children,
}: {
  title: string;
  icon?: typeof Layers;
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`bg-card border border-border rounded-xl overflow-hidden ${className}`}
    >
      <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-foreground inline-flex items-center gap-2">
          {Icon && <Icon className="h-4 w-4 text-muted-foreground" />}
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={className}>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm text-foreground mt-0.5">{children}</dd>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold text-foreground tabular-nums">
        {value}
      </div>
    </div>
  );
}

function Pill({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span
      className={`text-[11px] font-medium px-2 py-0.5 rounded border ${
        CHANGE_TONE[tone] ?? CHANGE_TONE.UNCHANGED
      }`}
    >
      {children}
    </span>
  );
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground ${className}`}
    >
      {children}
    </th>
  );
}
