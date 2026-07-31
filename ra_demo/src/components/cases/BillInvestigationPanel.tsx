import { useState, type ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, BadgeCheck, Calculator, Check, ChevronRight, CircleDollarSign,
  Layers, Loader2, Percent, RefreshCw, ScanSearch, ShieldAlert, Sparkles, Tags, Users, Wrench, X,
} from "lucide-react";
import { toast } from "sonner";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import {
  AFFECTED_RECORDS, AFFECTED_SAMPLE_VALUE, AFFECTED_TOTAL, ANALYSIS_STEPS, COMPARISON_ROWS,
  FLOW_STAGES, INVESTIGATION_SUMMARY, MOCK_BILL, RATING_ANALYSIS, RECOMMENDED_ACTIONS,
  ROOT_CAUSE_STAGE, ghs,
  type FlowStage, type StageStatus,
} from "@/lib/billInvestigation";

// DEMO-ONLY presentation of the mock bill analysis in lib/billInvestigation.
// Everything rendered here is fixed data; the progress animation exists to make
// the multi-stage nature of the check legible, not to time real work.

const TONE: Record<StageStatus, { badge: string; ring: string; icon: ReactNode; label: string }> = {
  pass: {
    badge: "bg-success/10 text-success border-success/20",
    ring: "border-border",
    icon: <Check className="h-3.5 w-3.5" />,
    label: "Pass",
  },
  warning: {
    badge: "bg-warning/15 text-warning-foreground border-warning/30",
    ring: "border-warning/40",
    icon: <AlertTriangle className="h-3.5 w-3.5" />,
    label: "Warning",
  },
  fail: {
    badge: "bg-destructive/10 text-destructive border-destructive/30",
    // The failing stage is the answer to the customer's question — give it the
    // only strong border on the row so it reads first.
    ring: "border-destructive/50 ring-1 ring-destructive/20",
    icon: <X className="h-3.5 w-3.5" />,
    label: "Fail",
  },
};

/** Stepped progress shown while the mock analysis "runs". */
export function AnalysisProgress({ step }: Readonly<{ step: number }>) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <ScanSearch className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium text-foreground">Analyzing bill</span>
        <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
          {Math.min(step + 1, ANALYSIS_STEPS.length)} / {ANALYSIS_STEPS.length}
        </span>
      </div>
      <ol className="space-y-2">
        {ANALYSIS_STEPS.map((s, i) => {
          const state = i < step ? "done" : i === step ? "active" : "pending";
          return (
            <li key={s.key} className="flex items-center gap-2.5">
              <span
                className={`h-5 w-5 shrink-0 rounded-full flex items-center justify-center ${
                  state === "done"
                    ? "bg-success/15 text-success"
                    : state === "active"
                      ? "bg-primary/15 text-primary"
                      : "bg-muted text-muted-foreground/50"
                }`}
              >
                {state === "done" ? <Check className="h-3 w-3" />
                  : state === "active" ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
              </span>
              <span className={`text-xs ${state === "pending" ? "text-muted-foreground/60" : "text-foreground/85"}`}>
                {s.label}
              </span>
              {state === "active" && (
                <span className="text-[11px] text-muted-foreground truncate">— {s.detail}</span>
              )}
            </li>
          );
        })}
      </ol>
      <div className="mt-3 h-1 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all duration-500"
          style={{ width: `${((step + 1) / ANALYSIS_STEPS.length) * 100}%` }}
        />
      </div>
    </div>
  );
}

function StageCard({ stage, onDrillDown }: Readonly<{ stage: FlowStage; onDrillDown?: () => void }>) {
  const tone = TONE[stage.status];
  return (
    <div className={`rounded-xl border bg-card p-3.5 flex flex-col ${tone.ring}`}>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{stage.name}</span>
        <span className={`ml-auto inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-md border ${tone.badge}`}>
          {tone.icon} {tone.label}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground leading-relaxed">{stage.summary}</p>

      <div className="mt-2.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {stage.recordsProcessed}
      </div>

      {stage.checks && (
        <ul className="mt-1.5 space-y-1">
          {stage.checks.map((check) => (
            <li key={check.label} className="flex items-start gap-1.5 text-[11px]">
              <span className={`mt-0.5 shrink-0 ${check.ok ? "text-success" : "text-destructive"}`}>
                {check.ok ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
              </span>
              <span className="text-muted-foreground flex-1 min-w-0">
                {check.label}
                <span className={`ml-1 ${check.ok ? "text-foreground/75" : "text-destructive font-medium"}`}>
                  {check.value}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Figures stack rather than sit in columns: the labels here name both
          sides of the reconciliation, and three of those across a quarter-width
          card is unreadable. */}
      {stage.figures && (
        <div className="mt-2.5 space-y-2 rounded-lg border border-destructive/25 bg-destructive/5 p-2.5">
          {stage.figures.map((f) => (
            <div key={f.label} className="min-w-0">
              <div className="text-[10px] text-muted-foreground leading-snug">{f.label}</div>
              <div className={`text-sm font-semibold tabular-nums ${
                f.tone === "bad" ? "text-destructive" : f.tone === "good" ? "text-success" : "text-foreground"
              }`}>
                {f.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {stage.rootCause && (
        <div className="mt-2.5 rounded-lg border border-border bg-muted/30 p-2.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Root cause</div>
          <p className="mt-0.5 text-[11px] text-foreground/85 leading-relaxed">{stage.rootCause}</p>
        </div>
      )}

      {stage.findings && (
        <ul className="mt-2.5 space-y-1.5">
          {stage.findings.map((finding) => (
            <li key={finding} className="flex gap-1.5 text-[11px] text-foreground/80 leading-relaxed">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current opacity-50" />
              <span>{finding}</span>
            </li>
          ))}
        </ul>
      )}

      {stage.drillDown && onDrillDown && (
        <button
          onClick={onDrillDown}
          className="mt-3 w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-1.5 text-[11px] font-medium text-primary hover:bg-primary/10 transition-colors"
        >
          <ScanSearch className="h-3 w-3" /> View Rating Analysis
          <ChevronRight className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

const ACTION_ICONS: Record<string, ReactNode> = {
  refund: <CircleDollarSign className="h-4 w-4" />,
  tariff: <Wrench className="h-4 w-4" />,
  rerate: <RefreshCw className="h-4 w-4" />,
  similar: <Users className="h-4 w-4" />,
  escalate: <ShieldAlert className="h-4 w-4" />,
};

/** The full result set, rendered once the mock analysis has finished. */
export function BillInvestigationPanel({
  onAction, onOpenRatingAnalysis,
}: Readonly<{
  onAction?: (key: string) => void;
  /** Opens the rating drill-down. Owned by the dialog, which has the height to
   *  anchor a drawer over the whole investigation surface. */
  onOpenRatingAnalysis?: () => void;
}>) {
  const [taken, setTaken] = useState<Set<string>>(new Set());

  const runAction = (key: string, done: string) => {
    setTaken((prev) => new Set(prev).add(key));
    toast.success(done);
    onAction?.(key);
  };

  return (
    <div className="space-y-3">
      {/* ---- Verdict ---- */}
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
        <div className="flex items-start gap-2.5">
          <span className="h-8 w-8 shrink-0 rounded-lg bg-destructive/10 text-destructive flex items-center justify-center">
            <BadgeCheck className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-foreground">{INVESTIGATION_SUMMARY.status}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-border bg-background text-muted-foreground">
                {INVESTIGATION_SUMMARY.confidence}% confidence
              </span>
            </div>
            <p className="mt-1 text-xs text-foreground/80 leading-relaxed">
              {INVESTIGATION_SUMMARY.narrative}
            </p>
          </div>
        </div>

        <dl className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3 border-t border-destructive/20 pt-3">
          <Metric label="Root cause" value={INVESTIGATION_SUMMARY.rootCause} tone="bad" />
          <Metric label="Revenue impact" value={ghs(INVESTIGATION_SUMMARY.revenueImpact)} tone="bad" />
          <Metric label="Recommended refund" value={ghs(INVESTIGATION_SUMMARY.recommendedRefund)} />
          <Metric label="Subscriber" value={`${MOCK_BILL.customer} · ${MOCK_BILL.msisdn}`} />
        </dl>
      </div>

      {/* ---- Assurance chain ---- */}
      <Panel
        title="Investigation Flow"
        icon={<ArrowRight className="h-4 w-4" />}
        hint={`Failure isolated to ${ROOT_CAUSE_STAGE}`}
      >
        {/* On a narrow screen the stages stack; the arrows only make sense in a
            row, so they are hidden there rather than rotated. */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] gap-2 items-stretch">
          {FLOW_STAGES.map((stage, i) => (
            <div key={stage.key} className="contents">
              <StageCard stage={stage} onDrillDown={onOpenRatingAnalysis} />
              {i < FLOW_STAGES.length - 1 && (
                <div className="hidden xl:flex items-center justify-center text-muted-foreground/40">
                  <ChevronRight className="h-4 w-4" />
                </div>
              )}
            </div>
          ))}
        </div>
      </Panel>

      {/* ---- Expected vs actual ---- */}
      <Panel title="Expected vs Actual" icon={<CircleDollarSign className="h-4 w-4" />}
             hint={`${MOCK_BILL.plan} · ${MOCK_BILL.cycle}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                {["Line item", "Expected", "Actual", "Difference"].map((h) => (
                  <th key={h} className="text-left font-medium px-2.5 py-2 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {COMPARISON_ROWS.map((row) => (
                <tr key={row.label} className={`border-t border-border ${row.mismatch ? "bg-destructive/5" : ""}`}>
                  <td className="px-2.5 py-2">
                    <div className="text-foreground/85">{row.label}</div>
                    {row.note && <div className="text-[10px] text-muted-foreground">{row.note}</div>}
                  </td>
                  <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{row.expected}</td>
                  <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${row.mismatch ? "text-destructive font-medium" : "text-foreground/80"}`}>
                    {row.actual}
                  </td>
                  <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${row.mismatch ? "text-destructive font-medium" : "text-muted-foreground"}`}>
                    {row.difference}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* ---- Affected records ---- */}
      <Panel title="Affected Records" icon={<ScanSearch className="h-4 w-4" />}
             hint={`${AFFECTED_RECORDS.length} of ${AFFECTED_TOTAL} calls · ${ghs(AFFECTED_SAMPLE_VALUE)} of ${ghs(INVESTIGATION_SUMMARY.revenueImpact)}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                {["CDR ID", "Date", "Duration", "Expected", "Actual", "Difference", "Reason"].map((h) => (
                  <th key={h} className="text-left font-medium px-2.5 py-2 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {AFFECTED_RECORDS.map((r) => (
                <tr key={r.cdrId} className="border-t border-border">
                  <td className="px-2.5 py-2 font-mono text-foreground/80 whitespace-nowrap">{r.cdrId}</td>
                  <td className="px-2.5 py-2 text-muted-foreground whitespace-nowrap">{r.date}</td>
                  <td className="px-2.5 py-2 tabular-nums text-muted-foreground whitespace-nowrap">{r.duration}</td>
                  <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{r.expected}</td>
                  <td className="px-2.5 py-2 tabular-nums text-destructive whitespace-nowrap">{r.actual}</td>
                  <td className="px-2.5 py-2 tabular-nums text-destructive font-medium whitespace-nowrap">{r.difference}</td>
                  <td className="px-2.5 py-2 text-muted-foreground">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* ---- Actions ---- */}
      <Panel title="Recommended Actions" icon={<Sparkles className="h-4 w-4" />}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {RECOMMENDED_ACTIONS.map((action) => {
            const done = taken.has(action.key);
            return (
              <div
                key={action.key}
                className={`rounded-lg border p-3 flex flex-col gap-2 ${
                  action.primary && !done ? "border-primary/40 bg-primary/5" : "border-border bg-background"
                }`}
              >
                <div className="flex items-start gap-2">
                  <span className={`h-7 w-7 shrink-0 rounded-lg flex items-center justify-center ${
                    done ? "bg-success/15 text-success" : "bg-primary/10 text-primary"
                  }`}>
                    {done ? <Check className="h-4 w-4" /> : ACTION_ICONS[action.key]}
                  </span>
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-foreground">{action.title}</div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed mt-0.5">{action.detail}</p>
                  </div>
                </div>
                <button
                  onClick={() => runAction(action.key, action.done)}
                  disabled={done}
                  className={`mt-auto self-start rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${
                    done
                      ? "text-success cursor-default"
                      : action.primary
                        ? "bg-primary text-primary-foreground hover:opacity-90"
                        : "border border-border hover:bg-muted"
                  }`}
                >
                  {done ? "Done" : action.title}
                </button>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rating drill-down
// ---------------------------------------------------------------------------
// Content of the drawer opened from the Rating stage. The drawer chrome —
// position, scrim, slide animation — belongs to CaseInvestigation, which owns
// the surface this is anchored to; this component only fills it.

const TH = "text-left font-medium px-2.5 py-2 whitespace-nowrap";

/** Label / actual / expected table, shared by the tariff and discount sections. */
function CompareTable({
  rows, columns,
}: Readonly<{
  rows: readonly { label: string; actual: string; expected: string; mismatch: boolean }[];
  columns: [string, string, string];
}>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr>{columns.map((h) => <th key={h} className={TH}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className={`border-t border-border ${row.mismatch ? "bg-destructive/5" : ""}`}>
              <td className="px-2.5 py-2 text-foreground/85">{row.label}</td>
              <td className={`px-2.5 py-2 whitespace-nowrap ${row.mismatch ? "text-destructive font-medium" : "text-foreground/80"}`}>
                {row.actual}
              </td>
              <td className="px-2.5 py-2 whitespace-nowrap text-foreground/80">{row.expected}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Detailed rating reconciliation behind the Rating stage's drill-down. */
export function RatingAnalysis({ onClose }: Readonly<{ onClose: () => void }>) {
  const a = RATING_ANALYSIS;
  return (
    <>
      <div className="shrink-0 flex items-start gap-3 px-5 py-4 border-b border-border">
        <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Calculator className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-foreground">Rating Analysis</h3>
          <p className="text-[11px] text-muted-foreground truncate">{a.subtitle}</p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close rating analysis"
          className="h-8 w-8 shrink-0 rounded-lg hover:bg-muted flex items-center justify-center text-muted-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {/* ---- The two sides, side by side ---- */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {a.sides.map((side) => (
            <div
              key={side.title}
              className={`rounded-xl border p-3.5 ${
                side.tone === "bad" ? "border-destructive/40 bg-destructive/5" : "border-success/40 bg-success/5"
              }`}
            >
              <div className="text-xs font-medium text-foreground">{side.title}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">{side.engine}</div>
              <div className={`mt-2 text-xl font-semibold tabular-nums ${
                side.tone === "bad" ? "text-destructive" : "text-success"
              }`}>
                {ghs(side.amount)}
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">{side.tariff}</div>
              <dl className="mt-2.5 space-y-1 border-t border-border/60 pt-2.5">
                {side.lines.map((line) => (
                  <div key={line.label} className="flex items-baseline justify-between gap-2 text-[11px]">
                    <dt className="text-muted-foreground">{line.label}</dt>
                    <dd className="tabular-nums text-foreground/85">{line.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>

        <p className="text-[11px] text-muted-foreground leading-relaxed">{a.scopeNote}</p>

        {/* ---- Tariff ---- */}
        <Panel title="Tariff version comparison" icon={<Tags className="h-4 w-4" />}>
          <CompareTable rows={a.tariff} columns={["Attribute", "Network (v11)", "RA expected (v12)"]} />
        </Panel>

        {/* ---- Allowances ---- */}
        <Panel title="Bundle & allowance comparison" icon={<Layers className="h-4 w-4" />}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  {["Allowance", "Entitlement", "Consumed", "Applied by network", "Applied by RA"].map((h) => (
                    <th key={h} className={TH}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {a.allowances.map((row) => (
                  <tr key={row.label} className={`border-t border-border ${row.mismatch ? "bg-destructive/5" : ""}`}>
                    <td className="px-2.5 py-2 text-foreground/85">{row.label}</td>
                    <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{row.entitlement}</td>
                    <td className="px-2.5 py-2 tabular-nums text-muted-foreground whitespace-nowrap">{row.consumed}</td>
                    <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${row.mismatch ? "text-destructive font-medium" : "text-foreground/80"}`}>
                      {row.networkApplied}
                    </td>
                    <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{row.raApplied}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* ---- Voice / Data / SMS ---- */}
        <Panel title="Voice / Data / SMS breakdown" icon={<CircleDollarSign className="h-4 w-4" />}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  {["Service", "Usage", "Network rated", "RA recalculated", "Variance"].map((h) => (
                    <th key={h} className={TH}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {a.charges.map((row) => (
                  <tr
                    key={row.service}
                    className={`border-t border-border ${row.total ? "bg-muted/40 font-medium" : row.mismatch ? "bg-destructive/5" : ""}`}
                  >
                    <td className="px-2.5 py-2 text-foreground/85 whitespace-nowrap">{row.service}</td>
                    <td className="px-2.5 py-2 text-muted-foreground">{row.usage}</td>
                    <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${row.mismatch ? "text-destructive" : "text-foreground/80"}`}>
                      {row.actual}
                    </td>
                    <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{row.expected}</td>
                    <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${row.mismatch ? "text-destructive font-medium" : "text-muted-foreground"}`}>
                      {row.variance}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* ---- Discounts ---- */}
        <Panel title="Applied discounts & credits" icon={<Percent className="h-4 w-4" />}>
          <CompareTable rows={a.discounts} columns={["Discount / credit", "Network (actual)", "RA expected"]} />
        </Panel>

        {/* ---- Variance ---- */}
        <Panel title="Variance calculation" icon={<Calculator className="h-4 w-4" />} hint={a.varianceTotal}>
          <ol className="space-y-1.5">
            {a.variance.map((step) => (
              <li
                key={step.label}
                className={`flex items-baseline justify-between gap-3 rounded-lg px-2.5 py-2 ${
                  step.kind === "credit" ? "bg-destructive/5" : "bg-muted/40"
                }`}
              >
                <div className="min-w-0">
                  <div className={`text-xs ${step.kind === "credit" ? "text-foreground/85" : "font-medium text-foreground"}`}>
                    {step.label}
                  </div>
                  {step.note && <div className="text-[10px] text-muted-foreground">{step.note}</div>}
                </div>
                <span className={`text-xs font-semibold tabular-nums whitespace-nowrap ${
                  step.kind === "credit" ? "text-destructive" : "text-foreground"
                }`}>
                  {step.value}
                </span>
              </li>
            ))}
            <li className="flex items-baseline justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-2">
              <span className="text-xs font-medium text-foreground">Variance (overcharge)</span>
              <span className="text-sm font-semibold tabular-nums text-destructive">{a.varianceTotal}</span>
            </li>
          </ol>
        </Panel>

        {/* ---- Root cause ---- */}
        <Panel title="Root cause summary" icon={<AlertTriangle className="h-4 w-4" />}>
          <p className="text-xs font-medium text-foreground leading-relaxed">{a.rootCause.headline}</p>
          <ul className="mt-2 space-y-1.5">
            {a.rootCause.points.map((point) => (
              <li key={point} className="flex gap-2 text-[11px] text-foreground/80 leading-relaxed">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-destructive" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}

function Panel({
  title, icon, hint, children,
}: Readonly<{ title: string; icon: ReactNode; hint?: string; children: ReactNode }>) {
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border">
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-sm font-medium text-foreground">{title}</span>
        {hint && <span className="ml-auto text-[11px] text-muted-foreground truncate">{hint}</span>}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

function Metric({ label, value, tone }: Readonly<{ label: string; value: string; tone?: "bad" }>) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] text-muted-foreground">{label}</dt>
      <dd className={`text-xs font-semibold truncate ${tone === "bad" ? "text-destructive" : "text-foreground"}`} title={value}>
        {value}
      </dd>
    </div>
  );
}

/** Small header badge reused by the investigation modal. */
export function RootCauseBadge() {
  return <StatusBadge value="critical" />;
}
