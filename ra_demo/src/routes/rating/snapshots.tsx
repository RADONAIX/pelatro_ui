import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Package,
  Play,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatTile } from "@/components/ui-kit/StatTile";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useActivateSnapshot,
  useActiveSnapshot,
  useCanEditSnapshots,
  useCompileSnapshot,
  useRollbackSnapshot,
  useSnapshotDiff,
  useSnapshots,
  useValidateRuleSet,
} from "@/lib/rating/hooks";
import { Select } from "@/components/ui-kit/Select";
import type { SnapshotSummary } from "@/lib/rating/types";
import {
  RatingEmpty,
  RatingError,
  RatingLoading,
} from "@/components/rating/RatingState";
import type { RuleSetValidationReport } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/snapshots")({
  component: SnapshotsPage,
});

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-success/10 text-success border-success/20",
  PUBLISHED: "bg-info/10 text-info border-info/20",
  SUPERSEDED: "bg-muted text-muted-foreground border-border",
  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
};

function SnapshotsPage() {
  const t = useT();
  const canEdit = useCanEditSnapshots();
  const { data: snapshots = [], isLoading, error, refetch } = useSnapshots();
  const { data: active } = useActiveSnapshot();

  const validate = useValidateRuleSet();
  const compile = useCompileSnapshot();
  const activate = useActivateSnapshot();
  const rollback = useRollbackSnapshot();

  const [report, setReport] = useState<RuleSetValidationReport | null>(null);
  const [name, setName] = useState("");

  const runValidation = async () => {
    try {
      const r = await validate.mutateAsync({});
      setReport(r);
      if (r.error_count)
        toast.error(`${r.error_count} ${t("blocking issues")}`);
      else
        toast.success(t("Rule set is compilable"), {
          description: `${r.rule_count} ${t("rules")} · ${r.warning_count} ${t("warnings")}`,
        });
    } catch (err) {
      toast.error(t("Validation failed"), { description: ratingError(err) });
    }
  };

  const runCompile = async () => {
    try {
      const snap = await compile.mutateAsync({
        name:
          name.trim() || `Snapshot ${new Date().toISOString().slice(0, 10)}`,
      });
      toast.success(`${t("Compiled snapshot")} v${snap.version}`, {
        description: `${snap.rule_count} ${t("executable rules")}`,
      });
      setName("");
    } catch (err) {
      toast.error(t("Compilation refused"), { description: ratingError(err) });
    }
  };

  const issueGroups: [string, RuleSetValidationReport["structural"]][] = report
    ? [
        [t("Structural"), report.structural],
        [t("Conflicts"), report.conflicts],
        [t("Coverage gaps"), report.coverage],
      ]
    : [];

  return (
    <AppShell>
      <PageHeader
        title={t("Rule Snapshots")}
        description={t(
          "Approved rules are compiled into an immutable, checksummed snapshot. The engine rates against exactly one active snapshot, so any historical charge can be re-explained against the rules that produced it.",
        )}
        info={t(
          "Compiling flattens each rule into match dimensions and residual predicates. That is what lets rule selection resolve once per rating context instead of once per CDR.",
        )}
        actions={
          canEdit && (
            <div className="flex items-center gap-2">
              <button
                onClick={runValidation}
                disabled={validate.isPending}
                className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
              >
                <ShieldCheck className="h-4 w-4" /> {t("Validate rule set")}
              </button>
              <button
                onClick={() =>
                  rollback.mutateAsync().then(
                    (s) =>
                      toast.success(`${t("Rolled back to")} v${s.version}`),
                    (e) =>
                      toast.error(t("Rollback refused"), {
                        description: ratingError(e),
                      }),
                  )
                }
                disabled={rollback.isPending}
                className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
              >
                <RotateCcw className="h-4 w-4" /> {t("Rollback")}
              </button>
            </div>
          )
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <StatTile
          icon={Package}
          label={t("Active snapshot")}
          value={active ? `v${active.version}` : "—"}
        />
        <StatTile
          icon={CheckCircle2}
          label={t("Rules in the engine")}
          value={String(active?.rule_count ?? 0)}
        />
        <StatTile
          icon={Package}
          label={t("Snapshots")}
          value={String(snapshots.length)}
        />
      </div>

      {canEdit && (
        <section className="bg-card border border-border rounded-xl p-5 mb-6">
          <h2 className="text-sm font-semibold text-foreground mb-1">
            {t("Compile a snapshot")}
          </h2>
          <p className="text-xs text-muted-foreground mb-4">
            {t(
              "Compiles every approved rule. Blocking conflicts stop the compile; coverage gaps are reported as warnings and do not.",
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("Snapshot name (optional)")}
              className="h-9 flex-1 min-w-[240px] rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <button
              onClick={runCompile}
              disabled={compile.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
            >
              <Package className="h-4 w-4" /> {t("Compile")}
            </button>
          </div>
        </section>
      )}

      {report && (
        <section className="bg-card border border-border rounded-xl overflow-hidden mb-6">
          <div className="px-5 py-4 border-b border-border flex flex-wrap items-center gap-3">
            <span className="text-sm font-semibold text-foreground">
              {t("Rule set validation")}
            </span>
            <span className="text-xs text-muted-foreground">
              {report.rule_count} {t("rules")}
            </span>
            {report.error_count > 0 ? (
              <span className="text-[11px] font-medium px-2 py-0.5 rounded-md border bg-destructive/10 text-destructive border-destructive/20">
                {report.error_count} {t("blocking")}
              </span>
            ) : (
              <span className="text-[11px] font-medium px-2 py-0.5 rounded-md border bg-success/10 text-success border-success/20">
                {t("compilable")}
              </span>
            )}
            {report.warning_count > 0 && (
              <span className="text-[11px] font-medium px-2 py-0.5 rounded-md border bg-warning/15 text-warning-foreground border-warning/30">
                {report.warning_count} {t("warnings")}
              </span>
            )}
          </div>
          <div className="divide-y divide-border max-h-96 overflow-y-auto">
            {issueGroups.map(([label, issues]) =>
              issues.length === 0 ? null : (
                <div key={label} className="px-5 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                    {label} ({issues.length})
                  </div>
                  <ul className="space-y-1.5">
                    {issues.slice(0, 25).map((issue, i) => (
                      <li
                        key={`${issue.code}-${i}`}
                        className="flex items-start gap-2"
                      >
                        {issue.severity === "ERROR" ? (
                          <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-destructive" />
                        ) : (
                          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-warning-foreground" />
                        )}
                        <div className="min-w-0">
                          <div className="text-[13px] text-foreground">
                            {issue.message}
                          </div>
                          {issue.hint && (
                            <div className="text-[11px] text-muted-foreground">
                              {issue.hint}
                            </div>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            )}
          </div>
        </section>
      )}

      {isLoading && <RatingLoading />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {snapshots.length === 0 && !isLoading && (
        <RatingEmpty
          icon={Package}
          title="No snapshots yet"
          description="Approve some rules, then compile them into a snapshot. Nothing can be rated until one is active."
        />
      )}

      {snapshots.length > 0 && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/40 text-left">
                  {[
                    t("Version"),
                    t("Name"),
                    t("Status"),
                    t("Rules"),
                    t("Effective"),
                    t("Checksum"),
                    t("Compiled by"),
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
                {snapshots.map((s) => (
                  <tr
                    key={s.id}
                    className="hover:bg-muted/30 transition-colors"
                  >
                    <td className="px-4 py-3 font-medium">
                      <Link
                        to="/rating/snapshots/$snapshotId"
                        params={{ snapshotId: s.id }}
                        className="text-foreground hover:text-primary transition-colors"
                      >
                        v{s.version}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        to="/rating/snapshots/$snapshotId"
                        params={{ snapshotId: s.id }}
                        className="text-muted-foreground hover:text-primary transition-colors"
                      >
                        {s.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-md border ${
                          STATUS_TONE[s.status] ?? STATUS_TONE.SUPERSEDED
                        }`}
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
                        {s.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                      {s.rule_count}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {s.effective_from}
                      {s.effective_to ? ` → ${s.effective_to}` : ""}
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] text-muted-foreground">
                      {s.checksum.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {s.compiled_by_name ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {canEdit &&
                        s.status !== "ACTIVE" &&
                        s.status !== "FAILED" && (
                          <button
                            onClick={() =>
                              activate.mutateAsync(s.id).then(
                                () =>
                                  toast.success(
                                    `v${s.version} ${t("is now active")}`,
                                  ),
                                (e) =>
                                  toast.error(t("Activation refused"), {
                                    description: ratingError(e),
                                  }),
                              )
                            }
                            disabled={activate.isPending}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted transition disabled:opacity-50"
                          >
                            <Play className="h-3.5 w-3.5" /> {t("Activate")}
                          </button>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {snapshots.length >= 2 && <SnapshotComparePanel snapshots={snapshots} />}
    </AppShell>
  );
}

const CHANGE_TONE: Record<string, string> = {
  ADDED: "bg-success/10 text-success border-success/20",
  REMOVED: "bg-destructive/10 text-destructive border-destructive/20",
  CHANGED: "bg-warning/15 text-warning-foreground border-warning/30",
};

function SnapshotComparePanel({ snapshots }: { snapshots: SnapshotSummary[] }) {
  const t = useT();
  // Newest against the previous one by default — the comparison someone
  // actually wants right before hitting Activate.
  const [fromId, setFromId] = useState(snapshots[1]?.id ?? "");
  const [toId, setToId] = useState(snapshots[0]?.id ?? "");
  const { data: diff, isFetching } = useSnapshotDiff(
    fromId || undefined,
    toId || undefined,
  );

  const options = snapshots.map((s) => ({
    value: s.id,
    label: `v${s.version} · ${s.status}`,
  }));
  const visible = (diff?.entries ?? []).filter((e) => e.change !== "UNCHANGED");

  return (
    <section className="bg-card border border-border rounded-xl p-5 mt-6">
      <h2 className="text-sm font-semibold text-foreground mb-1">
        {t("Compare snapshots")}
      </h2>
      <p className="text-[12px] text-muted-foreground mb-4">
        {t(
          "What changes if this version goes live — rule by rule, before it touches money.",
        )}
      </p>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Select
          value={fromId}
          onChange={setFromId}
          options={options}
          minWidth={170}
          ariaLabel={t("Compare from")}
        />
        <span className="text-sm text-muted-foreground">{t("vs")}</span>
        <Select
          value={toId}
          onChange={setToId}
          options={options}
          minWidth={170}
          ariaLabel={t("Compare to")}
        />
      </div>

      {fromId === toId && (
        <p className="text-sm text-muted-foreground">
          {t("Pick two different versions.")}
        </p>
      )}
      {isFetching && <p className="text-sm text-muted-foreground">…</p>}

      {diff && fromId !== toId && (
        <>
          <div className="flex flex-wrap gap-2 mb-4">
            {[
              [t("Added"), diff.added, CHANGE_TONE.ADDED],
              [t("Removed"), diff.removed, CHANGE_TONE.REMOVED],
              [t("Changed"), diff.changed, CHANGE_TONE.CHANGED],
              [
                t("Unchanged"),
                diff.unchanged,
                "bg-muted text-muted-foreground border-border",
              ],
            ].map(([label, count, tone]) => (
              <span
                key={String(label)}
                className={`text-[11px] font-semibold px-2.5 py-1 rounded-md border ${tone}`}
              >
                {label}
                <span className="ml-1.5 tabular-nums">{count}</span>
              </span>
            ))}
          </div>

          {diff.identical ? (
            <p className="text-sm text-muted-foreground">
              {t("The two versions are identical.")}
            </p>
          ) : (
            <ul className="divide-y divide-border border border-border rounded-lg overflow-hidden">
              {visible.map((entry) => (
                <li
                  key={entry.rule_key}
                  className="px-4 py-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1"
                >
                  <span
                    className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${CHANGE_TONE[entry.change] ?? ""}`}
                  >
                    {entry.change}
                  </span>
                  <span className="font-mono text-[12px] text-foreground">
                    {entry.rule_key}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {entry.from_version !== null && `v${entry.from_version}`}
                    {entry.from_version !== null &&
                      entry.to_version !== null &&
                      " → "}
                    {entry.to_version !== null && `v${entry.to_version}`}
                  </span>
                  {entry.details.length > 0 && (
                    <span className="text-[12px] text-muted-foreground basis-full">
                      {entry.details.join("; ")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
