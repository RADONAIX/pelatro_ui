/**
 * Bulk validate / approve / activate over an import.
 *
 * The last hop of the import workflow, and the reason an import produces a rule
 * set at all: without it, "approve the four hundred rules I just imported" is
 * four hundred clicks, and nobody does it four hundred times — they either give
 * one person blanket rights or stop reviewing.
 *
 * Three properties of the backend that this UI has to be honest about, because
 * hiding any of them turns a safety feature into a nuisance:
 *
 * **A dry run is the same call minus the write.** So the preview is rendered
 * with the same component as the result. A preview that lags what it previews is
 * worse than none.
 *
 * **Blockers are grouped by cause.** Thirty-two rules failing one check is one
 * fix; shown as thirty-two lines it reads as a broken batch, and an operator who
 * reaches that conclusion stops reading.
 *
 * **Activation is all-or-nothing.** Not a limitation to work around: a snapshot
 * is what rating resolves against, so activating most of a tariff publishes one
 * with a hole in it, and traffic that should have matched falls through to the
 * fallback. The button says so rather than letting someone discover it.
 */
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Rocket,
  ShieldCheck,
  Stamp,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useBulkAction,
  useBulkJob,
  useCanApproveRules,
  useSubmitBulkJob,
} from "@/lib/rating/hooks";
import type { BulkBlocker, BulkResponse } from "@/lib/rating/types";

/** Above this the backend refuses a synchronous call, so we queue a job. */
const JOB_THRESHOLD = 2_000;

type Operation = "validate" | "approve" | "activate";

const STEPS: {
  op: Operation;
  label: string;
  hint: string;
  icon: typeof ShieldCheck;
}[] = [
  {
    op: "validate",
    label: "Validate",
    hint: "Re-check every rule against the catalogue as it stands now.",
    icon: ShieldCheck,
  },
  {
    op: "approve",
    label: "Approve",
    hint: "Walk each rule to APPROVED, auditing every hop. All-or-nothing by default.",
    icon: Stamp,
  },
  {
    op: "activate",
    label: "Activate",
    hint: "Compile the set into a snapshot and make it live. Every rule must be approved first.",
    icon: Rocket,
  },
];

export function BulkLifecyclePanel({
  ruleSetId,
  ruleSetCode,
  ruleCount,
}: {
  ruleSetId: string;
  ruleSetCode?: string | null;
  ruleCount: number;
}) {
  const t = useT();
  const canApprove = useCanApproveRules();

  const [preview, setPreview] = useState<BulkResponse | null>(null);
  const [result, setResult] = useState<BulkResponse | null>(null);
  const [running, setRunning] = useState<Operation | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [comment, setComment] = useState("");

  const validate = useBulkAction("validate");
  const approve = useBulkAction("approve");
  const activate = useBulkAction("activate");
  const submitJob = useSubmitBulkJob();
  const { data: job } = useBulkJob(jobId);

  const mutationFor = (op: Operation) =>
    op === "validate" ? validate : op === "approve" ? approve : activate;

  // Large sets go to the job endpoint. The threshold is the backend's, mirrored
  // here only so the user gets a progress bar instead of a refusal.
  const asJob = ruleCount > JOB_THRESHOLD;

  const run = async (op: Operation, dryRun: boolean) => {
    setRunning(op);
    if (!dryRun) {
      setPreview(null);
      setResult(null);
    }
    try {
      if (asJob && !dryRun) {
        const queued = await submitJob.mutateAsync({
          rule_set_id: ruleSetId,
          operation: op,
          comment,
        });
        setJobId(queued.bulk_run_id);
        toast.success(t("Queued"), {
          description: t("This set is large, so it runs as a job."),
        });
        return;
      }
      const data = await mutationFor(op).mutateAsync({
        rule_set_id: ruleSetId,
        comment,
        dry_run: dryRun,
      });
      if (dryRun) {
        setPreview(data);
      } else {
        setResult(data);
        setPreview(null);
        toast.success(
          data.snapshot
            ? `${t("Snapshot")} v${data.snapshot.version} ${t("is live")}`
            : `${data.applied} ${t("rules")} ${op === "approve" ? t("approved") : t("validated")}`,
        );
      }
    } catch (err) {
      toast.error(t("That didn't run"), { description: ratingError(err) });
    } finally {
      setRunning(null);
    }
  };

  const shown = result ?? preview;

  return (
    <section className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-border">
        <h2 className="text-sm font-semibold text-foreground">
          {t("Take the whole import through its lifecycle")}
        </h2>
        <p className="text-xs text-muted-foreground mt-1">
          {ruleSetCode ? (
            <>
              <span className="font-mono">{ruleSetCode}</span> ·{" "}
            </>
          ) : null}
          {ruleCount} {t("rules")} ·{" "}
          {t(
            "validate, approve and activate them as one unit rather than one at a time.",
          )}
        </p>
      </div>

      <div className="p-5 space-y-4">
        {!canApprove && (
          <div className="rounded-lg border border-warning/20 bg-warning/10 p-3 flex items-start gap-2.5">
            <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
            <p className="text-[13px] text-muted-foreground">
              {t(
                "Your role can import and validate, but approving or activating rules that price live traffic needs rule-approval rights.",
              )}
            </p>
          </div>
        )}

        <input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={t("Why (goes on every rule's audit trail)")}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {STEPS.map(({ op, label, hint, icon: Icon }) => {
            const needsApprover = op !== "validate";
            const disabled =
              running !== null || (needsApprover && !canApprove) || !!jobId;
            return (
              <div
                key={op}
                className="rounded-lg border border-border p-3 flex flex-col gap-2"
              >
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-primary" />
                  <span className="text-sm font-medium text-foreground">
                    {t(label)}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed flex-1">
                  {t(hint)}
                </p>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => void run(op, true)}
                    disabled={disabled}
                    className="flex-1 rounded-md border border-border px-2 py-1.5 text-xs font-medium hover:bg-muted transition disabled:opacity-40"
                  >
                    {t("Preview")}
                  </button>
                  <button
                    onClick={() => void run(op, false)}
                    disabled={disabled}
                    className="flex-1 rounded-md bg-primary px-2 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-40 inline-flex items-center justify-center gap-1.5"
                  >
                    {running === op && (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    )}
                    {t("Run")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {/* A queued run reports its own progress; the row outlives this tab. */}
        {job && (
          <div className="rounded-lg border border-border p-4 space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm font-medium text-foreground">
                {t("Job")} · {job.operation}
              </span>
              <span className="text-xs text-muted-foreground">
                {job.status}
                {job.stale && ` · ${t("no heartbeat — the run may have died")}`}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${job.percent ?? 0}%` }}
              />
            </div>
            <p className="text-[11px] text-muted-foreground">
              {job.processed}/{job.total} {t("processed")} · {job.applied}{" "}
              {t("applied")}
            </p>
            {job.error && (
              <p className="text-[12px] text-destructive">{job.error}</p>
            )}
            {job.blocked.length > 0 && <Blockers blocked={job.blocked} />}
          </div>
        )}

        {shown && <Outcome result={shown} isPreview={shown === preview} />}
      </div>
    </section>
  );
}

function Outcome({
  result,
  isPreview,
}: {
  result: BulkResponse;
  isPreview: boolean;
}) {
  const t = useT();
  return (
    <div className="rounded-lg border border-border p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        {isPreview ? (
          <span className="text-[11px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded border bg-muted text-muted-foreground">
            {t("preview — nothing written")}
          </span>
        ) : (
          <CheckCircle2 className="h-4 w-4 text-success" />
        )}
        <span className="text-sm font-medium text-foreground capitalize">
          {result.operation}
        </span>
        <span className="text-xs text-muted-foreground">
          {result.applied}/{result.total}{" "}
          {isPreview ? t("would apply") : t("applied")}
        </span>
      </div>

      {result.snapshot && (
        <div className="rounded-md bg-success/10 border border-success/20 px-3 py-2">
          <p className="text-[13px] text-foreground">
            {t("Snapshot")}{" "}
            <span className="font-semibold">v{result.snapshot.version}</span> ·{" "}
            {result.snapshot.rule_count} {t("rules")} · {result.snapshot.status}
            {result.snapshot.forced && (
              <span className="ml-2 text-warning">
                {t("(compiled past blocking issues)")}
              </span>
            )}
          </p>
          <p className="text-[11px] text-muted-foreground mt-0.5 font-mono">
            {result.snapshot.checksum.slice(0, 16)}
          </p>
        </div>
      )}

      {Object.keys(result.counts).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(result.counts).map(([k, v]) => (
            <span
              key={k}
              className="text-[11px] px-2 py-0.5 rounded border border-border bg-muted/50 text-muted-foreground"
            >
              {k.toLowerCase().replace(/_/g, " ")} · {v}
            </span>
          ))}
        </div>
      )}

      {result.blocked.length > 0 && <Blockers blocked={result.blocked} />}

      {result.requires_approver_role && !result.caller_can_approve && (
        <p className="text-[12px] text-warning flex items-start gap-1.5">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          {t(
            "This selection includes rules that are already live, so running it needs rule-approval rights.",
          )}
        </p>
      )}
    </div>
  );
}

/** Grouped by cause, with examples — never one line per rule. */
function Blockers({ blocked }: { blocked: BulkBlocker[] }) {
  const t = useT();
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("What is holding it up")}
      </p>
      {blocked.map((b, i) => (
        <div key={`${b.code}-${i}`} className="flex items-start gap-2">
          <XCircle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
          <div className="min-w-0">
            <p className="text-[13px] text-foreground">
              <span className="tabular-nums font-semibold">×{b.count}</span>{" "}
              {b.reason || b.outcome}
            </p>
            {b.examples.length > 0 && (
              <p className="text-[11px] text-muted-foreground font-mono truncate">
                {b.examples.join(", ")}
                {b.count > b.examples.length && " …"}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
