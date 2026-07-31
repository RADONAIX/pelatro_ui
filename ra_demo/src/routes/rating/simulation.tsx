import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import {
  CheckCircle2,
  FlaskConical,
  Loader2,
  Play,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useActiveSnapshot,
  useCanSimulate,
  useRatingEnums,
  useSimulate,
  useSnapshots,
} from "@/lib/rating/hooks";
import { RatingEmpty } from "@/components/rating/RatingState";
import { RuleStatusBadge } from "@/components/rating/RuleStatusBadge";

export const Route = createFileRoute("/rating/simulation")({
  component: SimulationPage,
});

const inputCls =
  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary";

function SimulationPage() {
  const t = useT();
  const allowed = useCanSimulate();
  const { data: enums } = useRatingEnums();
  const { data: snapshots = [] } = useSnapshots();
  const { data: active } = useActiveSnapshot();
  const run = useSimulate();

  const [form, setForm] = useState({
    service_type: "VOICE",
    duration_seconds: "125",
    usage_volume: "",
    calling_number: "447700900001",
    called_number: "447700900500",
    event_timestamp: "2026-03-10T09:00",
    actual_charge: "",
    snapshot_id: "",
  });
  const patch = (p: Partial<typeof form>) => setForm((f) => ({ ...f, ...p }));
  const result = run.data;

  const simulate = async () => {
    try {
      await run.mutateAsync({
        service_type: form.service_type,
        duration_seconds: form.duration_seconds
          ? Number(form.duration_seconds)
          : null,
        usage_volume: form.usage_volume ? Number(form.usage_volume) : null,
        calling_number: form.calling_number || null,
        called_number: form.called_number || null,
        msisdn: form.calling_number || null,
        event_timestamp: form.event_timestamp
          ? `${form.event_timestamp}:00`
          : null,
        actual_charge: form.actual_charge ? Number(form.actual_charge) : null,
        snapshot_id: form.snapshot_id || null,
      });
    } catch (err) {
      toast.error(t("Simulation failed"), { description: ratingError(err) });
    }
  };

  if (!allowed) {
    return (
      <AppShell>
        <PageHeader title={t("Rule Simulation")} />
        <RatingEmpty
          icon={FlaskConical}
          title="You don't have simulation rights"
          description="Ask an administrator for the analyst or RA manager role."
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title={t("Rule Simulation")}
        description={t(
          "Rate one CDR against a snapshot without writing anything. Shows which rules were considered, which won, and the full calculation.",
        )}
        info={t(
          "Simulation runs the real engine against the real compiled snapshot — never an approximation, so the number here is the number a run would produce.",
        )}
      />

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        {/* --- Input ------------------------------------------------------- */}
        <section className="bg-card border border-border rounded-xl p-5 h-fit">
          <h2 className="text-sm font-semibold text-foreground mb-4">
            {t("CDR input")}
          </h2>
          <div className="space-y-4">
            <Field label="Service type">
              <Select
                value={form.service_type}
                onChange={(v) => patch({ service_type: v })}
                options={(enums?.service_type ?? []).map((v) => ({
                  value: v,
                  label: v,
                }))}
                minWidth={0}
                className="w-full"
              />
            </Field>
            <Field label="Calling number">
              <input
                value={form.calling_number}
                onChange={(e) => patch({ calling_number: e.target.value })}
                className={inputCls}
              />
            </Field>
            <Field
              label="Called number"
              hint="Resolved to a destination zone by longest prefix."
            >
              <input
                value={form.called_number}
                onChange={(e) => patch({ called_number: e.target.value })}
                className={inputCls}
              />
            </Field>
            <Field label="Event time" hint="Decides which time band applies.">
              <input
                type="datetime-local"
                value={form.event_timestamp}
                onChange={(e) => patch({ event_timestamp: e.target.value })}
                className={inputCls}
              />
            </Field>
            {form.service_type === "DATA" ? (
              <Field label="Usage volume (bytes)">
                <input
                  type="number"
                  value={form.usage_volume}
                  onChange={(e) => patch({ usage_volume: e.target.value })}
                  className={inputCls}
                />
              </Field>
            ) : (
              <Field label="Duration (seconds)">
                <input
                  type="number"
                  value={form.duration_seconds}
                  onChange={(e) => patch({ duration_seconds: e.target.value })}
                  className={inputCls}
                />
              </Field>
            )}
            <Field
              label="Billed amount"
              hint="Optional. Supply it and the simulation compares as well as calculates."
            >
              <input
                type="number"
                step="any"
                value={form.actual_charge}
                onChange={(e) => patch({ actual_charge: e.target.value })}
                placeholder={t("Leave blank to only calculate")}
                className={inputCls}
              />
            </Field>
            <Field
              label="Snapshot"
              hint="Test a proposed snapshot before activating it."
            >
              <Select
                value={form.snapshot_id}
                onChange={(v) => patch({ snapshot_id: v })}
                options={[
                  {
                    value: "",
                    label: active
                      ? `${t("Active")} — v${active.version}`
                      : t("Active"),
                  },
                  ...snapshots.map((s) => ({
                    value: s.id,
                    label: `v${s.version} — ${s.name} (${s.status})`,
                  })),
                ]}
                minWidth={0}
                className="w-full"
              />
            </Field>

            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={simulate}
                disabled={run.isPending}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
              >
                {run.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {t("Simulate")}
              </button>
              <button
                onClick={() => run.reset()}
                className="rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
              >
                {t("Clear")}
              </button>
            </div>
          </div>
        </section>

        {/* --- Result ------------------------------------------------------ */}
        <div className="space-y-6">
          {!result && (
            <RatingEmpty
              icon={FlaskConical}
              title="No simulation yet"
              description="Enter a CDR and run it. Nothing is written — this is safe against any snapshot, including one that is not active."
            />
          )}

          {result && (
            <>
              <section className="bg-card border border-border rounded-xl p-5">
                <div className="flex flex-wrap items-baseline justify-between gap-3 mb-4">
                  <h2 className="text-sm font-semibold text-foreground">
                    {t("Expected charge")}
                  </h2>
                  <span className="text-xs text-muted-foreground">
                    {t("snapshot")} v{result.snapshot_version} ·{" "}
                    {result.candidate_count} {t("candidate rules considered")}
                  </span>
                </div>
                <div className="flex flex-wrap items-end gap-8">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                      {t("Expected")}
                    </div>
                    <div className="text-3xl font-semibold text-foreground tabular-nums">
                      {result.calculation.expected_charge.toFixed(2)}{" "}
                      <span className="text-base text-muted-foreground">
                        {result.calculation.currency}
                      </span>
                    </div>
                  </div>
                  {result.comparison && (
                    <>
                      <div>
                        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                          {t("Billed")}
                        </div>
                        <div className="text-3xl font-semibold text-muted-foreground tabular-nums">
                          {result.comparison.actual_charge.toFixed(2)}
                        </div>
                      </div>
                      <div className="pb-1">
                        <div className="flex items-center gap-2">
                          {result.comparison.status === "MATCHED" ? (
                            <CheckCircle2 className="h-4 w-4 text-success" />
                          ) : (
                            <XCircle className="h-4 w-4 text-destructive" />
                          )}
                          <span className="text-sm font-medium text-foreground">
                            {result.comparison.status}
                          </span>
                        </div>
                        <p className="text-[12px] text-muted-foreground mt-0.5">
                          {result.comparison.explanation}
                        </p>
                      </div>
                    </>
                  )}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5 pt-4 border-t border-border">
                  {[
                    [
                      t("Billable"),
                      `${result.calculation.billable_quantity} ${result.calculation.billable_unit ?? ""}`,
                    ],
                    [t("Base"), result.calculation.base_charge.toFixed(4)],
                    [t("Discount"), result.calculation.discount.toFixed(4)],
                    [t("Tax"), result.calculation.tax.toFixed(4)],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        {label}
                      </div>
                      <div className="text-sm font-medium text-foreground tabular-nums">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <section className="bg-card border border-border rounded-xl p-5">
                <h2 className="text-sm font-semibold text-foreground mb-3">
                  {t("Rating context")}
                </h2>
                <div className="font-mono text-[11px] text-muted-foreground break-all mb-3">
                  {result.context_key}
                </div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(result.enrichment).map(([key, value]) => (
                    <span
                      key={key}
                      className="text-[11px] rounded-md border border-border bg-muted/40 px-2 py-1 text-muted-foreground"
                    >
                      {key.replace(/_/g, " ")}
                      <span className="ml-1.5 font-medium text-foreground">
                        {value === null || value === undefined
                          ? "—"
                          : String(value)}
                      </span>
                    </span>
                  ))}
                </div>
              </section>

              <section className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="px-5 py-4 border-b border-border">
                  <h2 className="text-sm font-semibold text-foreground">
                    {t("Calculation trace")}
                  </h2>
                </div>
                <ol className="divide-y divide-border">
                  {result.trace.map((s) => (
                    <li
                      key={s.step}
                      className="px-5 py-2.5 flex items-start gap-3"
                    >
                      <span className="text-[11px] text-muted-foreground w-5 shrink-0 pt-0.5 tabular-nums">
                        {s.step}
                      </span>
                      <span className="text-[10px] uppercase tracking-wide text-primary w-28 shrink-0 pt-1">
                        {s.stage}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-foreground">{s.label}</div>
                        <div className="text-[12px] text-muted-foreground">
                          {s.detail}
                        </div>
                      </div>
                      {s.rule_key && (
                        <span className="font-mono text-[10px] text-muted-foreground shrink-0 pt-1">
                          {s.rule_key}
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              </section>

              <section className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="px-5 py-4 border-b border-border flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-foreground">
                    {t("Rules considered")}
                  </h2>
                  <InfoHint
                    text={t(
                      "Every rule whose dimensions matched this context, and why each won or lost. This is the answer to 'why didn't it pick mine?'.",
                    )}
                  />
                </div>
                <table className="w-full text-sm">
                  <tbody className="divide-y divide-border">
                    {result.candidates.map((c) => (
                      <tr
                        key={c.rule_id}
                        className={c.selected ? "bg-success/5" : ""}
                      >
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-2">
                            {c.selected ? (
                              <CheckCircle2 className="h-3.5 w-3.5 text-success shrink-0" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" />
                            )}
                            <span className="font-mono text-[12px] text-foreground">
                              {c.rule_key}
                            </span>
                            <span className="text-[11px] text-muted-foreground">
                              v{c.rule_version}
                            </span>
                          </div>
                          <div className="text-[11px] text-muted-foreground mt-0.5 ml-5.5">
                            {c.reason}
                          </div>
                        </td>
                        <td className="px-5 py-3 text-muted-foreground whitespace-nowrap">
                          {c.stage}
                        </td>
                        <td className="px-5 py-3 text-right text-muted-foreground whitespace-nowrap tabular-nums">
                          {t("spec")} {c.specificity} · {t("prio")} {c.priority}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <div>
      <div className="flex items-center gap-1 mb-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {t(label)}
        </label>
        {hint && <InfoHint text={t(hint)} />}
      </div>
      {children}
    </div>
  );
}

export { RuleStatusBadge };
