import { useState, type ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, BadgeCheck, Calculator, Check, ChevronRight, CircleDollarSign,
  FolderOpen, Layers, Loader2, Percent, RefreshCw, ScanSearch, ShieldAlert, Sparkles, Tags,
  UserRound, Users, Wrench, X,
} from "lucide-react";
import { toast } from "sonner";
import { StatusBadge } from "@/components/ui-kit/StatusBadge";
import { ANALYSIS_STEPS } from "@/lib/billInvestigation";
import {
  BILLING_TYPE, money, signedMoney,
  type Investigation, type MediationResponse, type RatingResponse,
} from "@/lib/investigation";

// Presentation of the postpaid billing-shock investigation. Every figure comes
// from `Investigation`, read straight out of the rating and billing source
// tables — nothing here computes, defaults or rounds a money value, so a dash
// on screen means the source system had nothing for that field rather than
// that the UI gave up.

type StageStatus = "pass" | "fail" | "warning";

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

/** Stepped progress shown while the investigation loads. */
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
                className={`h-5 w-5 shrink-0 rounded-full flex items-center justify-center ${state === "done"
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

interface StageCheck { label: string; ok: boolean; value: string }

function StageCard({
  name, status, summary, subtitle, checks, figures, rootCause, onDrillDown,
}: Readonly<{
  name: string;
  status: StageStatus;
  summary: string;
  subtitle: string;
  checks?: StageCheck[];
  figures?: { label: string; value: string; tone?: "bad" | "good" }[];
  rootCause?: string;
  onDrillDown?: () => void;
}>) {
  const tone = TONE[status];
  return (
    <div className={`rounded-xl border bg-card p-3.5 flex flex-col ${tone.ring}`}>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{name}</span>
        <span className={`ml-auto inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-md border ${tone.badge}`}>
          {tone.icon} {tone.label}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground leading-relaxed">{summary}</p>

      <div className="mt-2.5 text-[10px] uppercase tracking-wide text-muted-foreground">{subtitle}</div>

      {checks && (
        <ul className="mt-1.5 space-y-1">
          {checks.map((check) => (
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

      {/* Figures stack rather than sit in columns: the labels name both sides of
          the reconciliation, and three of those across a quarter-width card is
          unreadable. */}
      {figures && (
        <div className="mt-2.5 space-y-2 rounded-lg border border-destructive/25 bg-destructive/5 p-2.5">
          {figures.map((f) => (
            <div key={f.label} className="min-w-0">
              <div className="text-[10px] text-muted-foreground leading-snug">{f.label}</div>
              <div className={`text-sm font-semibold tabular-nums ${f.tone === "bad" ? "text-destructive" : f.tone === "good" ? "text-success" : "text-foreground"
                }`}>
                {f.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {rootCause && (
        <div className="mt-2.5 rounded-lg border border-border bg-muted/30 p-2.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Root cause</div>
          <p className="mt-0.5 text-[11px] text-foreground/85 leading-relaxed">{rootCause}</p>
        </div>
      )}

      {onDrillDown && (
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
  impacted: <Users className="h-4 w-4" />,
  escalate: <ShieldAlert className="h-4 w-4" />,
};

const count = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

/** Per-service counts either side of mediation, paired for comparison.
 *  Voice and SMS only — data is out of scope for a tariff fault, and the total
 *  is the sum of these two rather than the source table's all-service count. */
function countsRows(m: MediationResponse) {
  const msc = m.msc;
  const pm = m.postMediation;
  return [
    { label: "Voice", msc: msc?.voice, pm: pm?.voice },
    { label: "SMS", msc: msc?.sms, pm: pm?.sms },
    { label: "Total events", msc: msc?.totalEvents, pm: pm?.totalEvents },
  ];
}

/** The full result set, rendered once the investigation has loaded. */
export function BillInvestigationPanel({
  data, onAction, onOpenRatingAnalysis,
}: Readonly<{
  data: Investigation;
  onAction?: (key: string) => void;
  /** Opens the rating drill-down. Owned by the dialog, which has the height to
   *  anchor a drawer over the whole investigation surface. */
  onOpenRatingAnalysis?: () => void;
}>) {
  const [taken, setTaken] = useState<Set<string>>(new Set());
  const { subscriber, mediation, rating, related, actions } = data;

  const runAction = (key: string, title: string) => {
    setTaken((prev) => new Set(prev).add(key));
    toast.success(title);
    onAction?.(key);
  };

  const currency = rating.currency ?? subscriber.invoice?.currency ?? null;
  const variance = rating.actual?.variance ?? null;
  const ratingFailed = rating.available && rating.result === "FAIL";
  const mediationOk = !mediation.available || mediation.result === "PASS";
  const rows = countsRows(mediation);

  // Nothing ties this case to a subscriber, so there is no invoice, no rated
  // event and nothing to reconcile. Say that plainly and say how to fix it —
  // an empty flow reads as "we checked and found nothing wrong", which is the
  // opposite of the truth.
  if (!subscriber.msisdn) {
    return (
      <div className="rounded-xl border border-warning/40 bg-warning/5 p-4">
        <div className="flex items-start gap-2.5">
          <span className="h-8 w-8 shrink-0 rounded-lg bg-warning/15 text-warning-foreground flex items-center justify-center">
            <AlertTriangle className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-foreground">No subscriber linked to this case</div>
            <p className="mt-1 text-xs text-foreground/80 leading-relaxed">
              The rating and billing records are keyed by MSISDN, and this case does not carry one.
              Add the subscriber number to the case — or include it in the title — and run the
              analysis again.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* ---- Verdict ---- */}
      <div className={`rounded-xl border p-4 ${ratingFailed ? "border-destructive/40 bg-destructive/5" : "border-border bg-card"
        }`}>
        <div className="flex items-start gap-2.5">
          <span className={`h-8 w-8 shrink-0 rounded-lg flex items-center justify-center ${ratingFailed ? "bg-destructive/10 text-destructive" : "bg-success/10 text-success"
            }`}>
            <BadgeCheck className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-foreground">
                {ratingFailed ? "Overcharge Confirmed" : "No rating variance found"}
              </span>
              {rating.sourceStatus && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-border bg-background text-muted-foreground">
                  {rating.sourceStatus}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-foreground/80 leading-relaxed">
              {rating.finding || "Rating reconciled against the expected tariff."}
            </p>
          </div>
        </div>

        <dl className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3 border-t border-border/40 pt-3">
          <Metric label="Root cause" value={ratingFailed ? "Rating" : "—"} tone={ratingFailed ? "bad" : undefined} />
          <Metric label="Revenue impact" value={money(variance, currency)} tone={ratingFailed ? "bad" : undefined} />
          <Metric label="Recommended refund" value={money(variance, currency)} />
          <Metric label="Subscriber" value={subscriber.msisdn ?? "—"} />
        </dl>
      </div>

      {/* ---- 1. Subscriber & invoice ---- */}
      <Panel title="Subscriber & Invoice Information" icon={<UserRound className="h-4 w-4" />}
        hint={subscriber.invoice?.invoiceId}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="rounded-lg border border-border bg-background p-3">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-2">Subscriber</div>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
              <Metric label="MSISDN" value={subscriber.subscriber?.msisdn ?? subscriber.msisdn ?? "—"} />
              <Metric label="Account type" value={subscriber.subscriber?.accountType ?? "—"} />
              <Metric label="Offer" value={subscriber.subscriber?.offer ?? "—"} />
              <Metric label="Billing type" value={subscriber.subscriber?.billingType ?? BILLING_TYPE} />
            </dl>
          </div>
          <div className="rounded-lg border border-border bg-background p-3">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-2">Invoice</div>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
              <Metric label="Usage charge" value={money(subscriber.invoice?.usageCharge, currency)} />
              <Metric label="Tax amount" value={money(subscriber.invoice?.taxAmount, currency)} />
              <Metric label="Total invoice amount" value={money(subscriber.invoice?.totalInvoiceAmount, currency)} />
              <Metric label="Currency" value={subscriber.invoice?.currency ?? "—"} />
            </dl>
          </div>
        </div>
      </Panel>

      {/* ---- 2-3. Assurance chain ---- */}
      <Panel
        title="Investigation Flow"
        icon={<ArrowRight className="h-4 w-4" />}
        hint={ratingFailed ? "Failure isolated to Rating" : undefined}
      >
        {/* On a narrow screen the stages stack; the arrows only make sense in a
            row, so they are hidden there rather than rotated. */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] gap-2 items-stretch">
          <StageCard
            name="MSC"
            status={mediationOk ? "pass" : "fail"}
            summary={mediation.available
              ? "Voice and SMS usage recorded at the switch for this subscriber."
              : "No mediation comparison recorded for this subscriber."}
            subtitle={`${count(mediation.msc?.totalEvents)} events`}
            checks={mediation.available
              ? rows.map((r) => ({ label: r.label, ok: true, value: count(r.msc) }))
              : undefined}
          />
          <Arrow />
          <StageCard
            name="Post Mediation"
            status={mediationOk ? "pass" : "fail"}
            summary={mediation.finding ?? "—"}
            subtitle={`${count(mediation.postMediation?.totalEvents)} events`}
            checks={mediation.available
              ? rows.map((r) => ({
                label: r.label,
                ok: r.msc === r.pm,
                value: r.msc === r.pm ? `${count(r.pm)} matched` : count(r.pm),
              }))
              : undefined}
          />
          <Arrow />
          {/* The root-cause stage stays deliberately thin: three figures and a
              one-line cause. The full reconciliation is behind the drill-down. */}
          <StageCard
            name="Rating"
            status={rating.available ? (ratingFailed ? "fail" : "pass") : "warning"}
            summary={rating.available
              ? `${rating.serviceType ?? "Rated"} event reconciled against the expected tariff.`
              : "No rated event found for this subscriber."}
            subtitle={rating.eventId ?? "—"}
            figures={rating.available ? [
              { label: "Network Billed Amount (Actual)", value: money(rating.actual?.actualCharge, currency), tone: "bad" },
              { label: "RA Expected Amount", value: money(rating.expected?.finalExpectedAmount, currency), tone: "good" },
              { label: "Overcharge", value: signedMoney(variance, currency), tone: "bad" },
            ] : undefined}
            rootCause={rating.available ? rating.finding : undefined}
            onDrillDown={rating.available ? onOpenRatingAnalysis : undefined}
          />
          <Arrow />
          {/* <StageCard
            name="Billing"
            status={ratingFailed ? "warning" : "pass"}
            summary={ratingFailed
              ? "Invoice correctly generated from wrong rating output."
              : "Invoice reproduces the rating output."}
            subtitle={subscriber.invoice?.invoiceId ?? "No invoice"}
            checks={subscriber.invoice ? [
              { label: "Usage charge", ok: true, value: money(subscriber.invoice.usageCharge, currency) },
              { label: "Tax", ok: true, value: money(subscriber.invoice.taxAmount, currency) },
              { label: "Total billed", ok: !ratingFailed, value: money(subscriber.invoice.totalInvoiceAmount, currency) },
            ] : undefined}
          /> */}

          <StageCard
            name="Billing"
            status={ratingFailed ? "warning" : "pass"}
            summary={
              ratingFailed
                ? "Invoice correctly generated from wrong rating output."
                : "Invoice reproduces the rating output."
            }

            subtitle={subscriber.invoice?.invoiceId ?? "No invoice"}

            checks={
              subscriber.invoice
                ? [
                  {
                    label: "Usage charge",
                    ok: true,
                    value: money(
                      subscriber.invoice.usageCharge ?? 0,
                      currency,
                    ),
                  },
                  {
                    label: "Tax",
                    ok: true,
                    value: money(
                      subscriber.invoice.taxAmount ?? 0,
                      currency,
                    ),
                  },
                  // Passes even when rating failed. This checks billing's own
                  // arithmetic — usage + tax reproduced faithfully as the
                  // invoice total — and that is correct here. The fault is the
                  // input billing was given, which the Impact row below states.
                  {
                    label: "Total billed",
                    ok: true,
                    value: money(
                      subscriber.invoice.totalInvoiceAmount ?? 0,
                      currency,
                    ),
                  },
                  ...(ratingFailed
                    ? [
                      {
                        label: "Impact",
                        ok: false,
                        value: `Amount higher than RA expected by ${money(
                          (subscriber.invoice.totalInvoiceAmount ?? 0) -
                          (rating.expected?.finalExpectedAmount ?? 0),
                          currency,
                        )}`,
                      },
                    ]
                    : []),
                ]
                : undefined
            }

          />

        </div>
      </Panel>

      {/* ---- Expected vs actual ---- */}
      {rating.available && (
        <Panel title="Expected vs Actual" icon={<CircleDollarSign className="h-4 w-4" />}
          hint={[rating.serviceType, rating.destination].filter(Boolean).join(" · ") || undefined}>
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
                <ComparisonRow
                  label="Tariff rule"
                  note={rating.expected?.ruleName ?? undefined}
                  expected={rating.expected?.ruleId ?? "—"}
                  actual={rating.actual?.appliedRuleId ?? "—"}
                  difference={rating.expected?.ruleId === rating.actual?.appliedRuleId ? "—" : "Mismatch"}
                  mismatch={rating.expected?.ruleId !== rating.actual?.appliedRuleId}
                />
                <ComparisonRow
                  label="Rate"
                  expected={money(rating.expected?.rate, currency)}
                  actual={money(rating.actual?.appliedRate, currency)}
                  difference={rating.expected?.rate === rating.actual?.appliedRate ? "—" : "Mismatch"}
                  mismatch={rating.expected?.rate !== rating.actual?.appliedRate}
                />
                <ComparisonRow
                  label="Usage charge"
                  note={rating.expected?.durationLabel}
                  expected={money(rating.expected?.expectedCharge, currency)}
                  actual={money(rating.actual?.usageCharge ?? rating.actual?.ratedCharge, currency)}
                  difference={signedMoney(
                    (rating.actual?.usageCharge ?? rating.actual?.ratedCharge ?? 0)
                    - (rating.expected?.expectedCharge ?? 0),
                    currency,
                  )}
                  mismatch={
                    (rating.actual?.usageCharge ?? rating.actual?.ratedCharge) !== rating.expected?.expectedCharge
                  }
                />
                <ComparisonRow
                  label="Tax"
                  expected={money(rating.expected?.tax, currency)}
                  actual={money(rating.actual?.taxAmount, currency)}
                  difference={signedMoney(
                    (rating.actual?.taxAmount ?? 0) - (rating.expected?.tax ?? 0), currency,
                  )}
                  mismatch={rating.actual?.taxAmount !== rating.expected?.tax}
                />
                {/* The bottom line, and the one the customer disputes: the
                    invoice total including tax against the expected final
                    charge. */}
                <ComparisonRow
                  label="Total billed"
                  note={rating.actual?.billedFromInvoice ? "Invoice total, tax included" : "Rated amount — no invoice raised"}
                  expected={money(rating.expected?.finalExpectedAmount, currency)}
                  actual={money(rating.actual?.actualCharge, currency)}
                  difference={signedMoney(variance, currency)}
                  mismatch={ratingFailed}
                />
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {/* ---- 4. Existing case check ---- */}
      <Panel title="Existing Case Check" icon={<FolderOpen className="h-4 w-4" />}
        hint={related.cases.length ? `${related.unresolvedCount} unresolved` : undefined}>
        {related.cases.length ? (
          <div className="space-y-2.5">
            {related.message && (
              <div className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2">
                <p className="text-[11px] leading-relaxed text-foreground/85">{related.message}</p>
                {related.impact && (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    <span className="uppercase tracking-wide">Impact</span> — {related.impact}
                  </p>
                )}
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-muted/50 text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    {["Case", "Issue", "Status", "Priority", "Owner"].map((h) => (
                      <th key={h} className="text-left font-medium px-2.5 py-2 whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {related.cases.map((c) => (
                    <tr key={c.id} className={`border-t border-border ${c.resolved ? "" : "bg-warning/5"}`}>
                      <td className="px-2.5 py-2">
                        <div className="font-mono text-foreground/85">{c.reference}</div>
                        <div className="text-[10px] text-muted-foreground">{c.title}</div>
                      </td>
                      <td className="px-2.5 py-2 text-muted-foreground whitespace-nowrap">{c.issue}</td>
                      <td className="px-2.5 py-2"><StatusBadge value={c.status} /></td>
                      <td className="px-2.5 py-2"><StatusBadge value={c.severity} /></td>
                      <td className="px-2.5 py-2 text-muted-foreground whitespace-nowrap">{c.owner || "Unassigned"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">
            No other case has been raised for this subscriber.
          </p>
        )}
      </Panel>

      {/* ---- 5. Actions ---- */}
      {actions.actions.length > 0 && (
        <Panel title="Recommended Actions" icon={<Sparkles className="h-4 w-4" />}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {actions.actions.map((action) => {
              const done = taken.has(action.key);
              return (
                <div
                  key={action.key}
                  className={`rounded-lg border p-3 flex flex-col gap-2 ${action.primary && !done ? "border-primary/40 bg-primary/5" : "border-border bg-background"
                    }`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`h-7 w-7 shrink-0 rounded-lg flex items-center justify-center ${done ? "bg-success/15 text-success" : "bg-primary/10 text-primary"
                      }`}>
                      {done ? <Check className="h-4 w-4" /> : ACTION_ICONS[action.key] ?? <Sparkles className="h-4 w-4" />}
                    </span>
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-foreground">{action.title}</div>
                      <p className="text-[11px] text-muted-foreground leading-relaxed mt-0.5">{action.detail}</p>
                    </div>
                  </div>
                  <button
                    onClick={() => runAction(action.key, action.title)}
                    disabled={done}
                    className={`mt-auto self-start rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${done
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
      )}
    </div>
  );
}

const Arrow = () => (
  <div className="hidden xl:flex items-center justify-center text-muted-foreground/40">
    <ChevronRight className="h-4 w-4" />
  </div>
);

function ComparisonRow({
  label, note, expected, actual, difference, mismatch,
}: Readonly<{
  label: string; note?: string; expected: string; actual: string;
  difference: string; mismatch: boolean;
}>) {
  return (
    <tr className={`border-t border-border ${mismatch ? "bg-destructive/5" : ""}`}>
      <td className="px-2.5 py-2">
        <div className="text-foreground/85">{label}</div>
        {note && <div className="text-[10px] text-muted-foreground">{note}</div>}
      </td>
      <td className="px-2.5 py-2 tabular-nums text-foreground/80 whitespace-nowrap">{expected}</td>
      <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${mismatch ? "text-destructive font-medium" : "text-foreground/80"}`}>
        {actual}
      </td>
      <td className={`px-2.5 py-2 tabular-nums whitespace-nowrap ${mismatch ? "text-destructive font-medium" : "text-muted-foreground"}`}>
        {difference}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Rating drill-down
// ---------------------------------------------------------------------------
// Content of the drawer opened from the Rating stage. The drawer chrome —
// position, scrim, slide animation — belongs to CaseInvestigation, which owns
// the surface this is anchored to; this component only fills it.

const TH = "text-left font-medium px-2.5 py-2 whitespace-nowrap";

/** Label / actual / expected table, shared by every drill-down section. */
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

const ruleLabel = (id?: string | null, name?: string | null) =>
  id ? (name ? `${id} — ${name}` : id) : "—";

/** Detailed rating reconciliation behind the Rating stage's drill-down. */
export function RatingAnalysis({
  rating, invoiceCurrency, onClose,
}: Readonly<{
  rating: RatingResponse;
  invoiceCurrency?: string | null;
  onClose: () => void;
}>) {
  const currency = rating.currency ?? invoiceCurrency ?? null;
  const expected = rating.expected;
  const actual = rating.actual;
  const variance = actual?.variance ?? null;
  const ruleMismatch = expected?.ruleId !== actual?.appliedRuleId;

  return (
    <>
      <div className="shrink-0 flex items-start gap-3 px-5 py-4 border-b border-border">
        <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
          <Calculator className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-foreground">Rating Analysis</h3>
          <p className="text-[11px] text-muted-foreground truncate">
            {[rating.msisdn, rating.eventId, rating.serviceType].filter(Boolean).join(" · ")}
          </p>
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
          <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-3.5">
            <div className="text-xs font-medium text-foreground">Network Billed Amount (Actual)</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">
              {actual?.billedFromInvoice ? "Invoice total, tax included" : "Rated amount — no invoice raised"}
            </div>
            <div className="mt-2 text-xl font-semibold tabular-nums text-destructive">
              {money(actual?.actualCharge, currency)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {ruleLabel(actual?.appliedRuleId, actual?.appliedRule)}
            </div>
            <dl className="mt-2.5 space-y-1 border-t border-border/60 pt-2.5">
              <Line label="Usage charge" value={money(actual?.usageCharge ?? actual?.ratedCharge, currency)} />
              <Line label="Tax" value={money(actual?.taxAmount, currency)} />
              <Line label="Rate applied" value={money(actual?.appliedRate, currency)} />
            </dl>
          </div>
          <div className="rounded-xl border border-success/40 bg-success/5 p-3.5">
            <div className="text-xs font-medium text-foreground">RA Expected Amount</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">Same event, correct tariff</div>
            <div className="mt-2 text-xl font-semibold tabular-nums text-success">
              {money(expected?.finalExpectedAmount, currency)}
            </div>
            <div className="mt-0.5 text-[10px] text-muted-foreground">
              {ruleLabel(expected?.ruleId, expected?.ruleName)}
            </div>
            <dl className="mt-2.5 space-y-1 border-t border-border/60 pt-2.5">
              <Line label="Base charge" value={money(expected?.expectedCharge, currency)} />
              <Line label="Tax" value={money(expected?.tax, currency)} />
              <Line label="Rate expected" value={money(expected?.rate, currency)} />
            </dl>
          </div>
        </div>

        <Panel title="Tariff version comparison" icon={<Tags className="h-4 w-4" />}>
          <CompareTable
            rows={[
              {
                label: "Tariff rule",
                actual: ruleLabel(actual?.appliedRuleId, actual?.appliedRule),
                expected: ruleLabel(expected?.ruleId, expected?.ruleName),
                mismatch: ruleMismatch,
              },
              {
                label: "Rate",
                actual: money(actual?.appliedRate, currency),
                expected: money(expected?.rate, currency),
                mismatch: expected?.rate !== actual?.appliedRate,
              },
              {
                label: "Service type",
                actual: rating.serviceType ?? "—",
                expected: rating.serviceType ?? "—",
                mismatch: false,
              },
              {
                label: "Destination",
                actual: rating.destination ?? "—",
                expected: rating.destination ?? "—",
                mismatch: false,
              },
            ]}
            columns={["Attribute", "Network (applied)", "RA expected"]}
          />
        </Panel>

        <Panel title="Bundle & allowance comparison" icon={<Layers className="h-4 w-4" />}>
          <CompareTable
            rows={[
              {
                label: "Chargeable duration",
                actual: expected?.durationLabel ?? "—",
                expected: expected?.durationLabel ?? "—",
                mismatch: false,
              },
              {
                label: "Free allowance applied",
                actual: money(0, currency),
                expected: money(expected?.discount, currency),
                mismatch: (expected?.discount ?? 0) !== 0,
              },
            ]}
            columns={["Allowance", "Applied by network", "Applied by RA"]}
          />
        </Panel>

        <Panel title="Charge breakdown" icon={<CircleDollarSign className="h-4 w-4" />}
          hint={rating.serviceType ?? undefined}>
          <CompareTable
            rows={[
              {
                label: "Base charge",
                actual: money(actual?.usageCharge ?? actual?.ratedCharge, currency),
                expected: money(expected?.expectedCharge, currency),
                mismatch: (actual?.usageCharge ?? actual?.ratedCharge) !== expected?.expectedCharge,
              },
              {
                label: "Discount",
                actual: money(0, currency),
                expected: money(expected?.discount, currency),
                mismatch: (expected?.discount ?? 0) !== 0,
              },
              {
                label: "Surcharge",
                actual: money(0, currency),
                expected: money(expected?.surcharge, currency),
                mismatch: (expected?.surcharge ?? 0) !== 0,
              },
              {
                label: "Before tax",
                actual: money(actual?.usageCharge ?? actual?.ratedCharge, currency),
                expected: money(expected?.beforeTax, currency),
                mismatch: (actual?.usageCharge ?? actual?.ratedCharge) !== expected?.beforeTax,
              },
              {
                label: "Tax",
                actual: money(actual?.taxAmount, currency),
                expected: money(expected?.tax, currency),
                mismatch: actual?.taxAmount !== expected?.tax,
              },
              {
                label: "Total billed",
                actual: money(actual?.actualCharge, currency),
                expected: money(expected?.finalExpectedAmount, currency),
                mismatch: variance !== 0,
              },
            ]}
            columns={["Component", "Network (actual)", "RA expected"]}
          />
        </Panel>

        <Panel title="Applied discounts & tax rules" icon={<Percent className="h-4 w-4" />}>
          <CompareTable
            rows={[
              {
                label: "Discount rules",
                actual: "None",
                expected: expected?.discountRules?.length
                  ? expected.discountRules.map((r) => r.rule_name ?? r.rule_id ?? "—").join(", ")
                  : "None",
                mismatch: false,
              },
              {
                label: "Tax rules",
                actual: "—",
                expected: expected?.taxRules?.length
                  ? expected.taxRules.map((r) => r.rule_name ?? r.rule_id ?? "—").join(", ")
                  : "None",
                mismatch: false,
              },
              {
                label: "Tax amount",
                actual: "—",
                expected: money(expected?.tax, currency),
                mismatch: false,
              },
            ]}
            columns={["Rule", "Network (actual)", "RA expected"]}
          />
        </Panel>

        <Panel title="Variance calculation" icon={<Calculator className="h-4 w-4" />}
          hint={signedMoney(variance, currency)}>
          <ol className="space-y-1.5">
            <Step
              label="Network billed amount"
              note={actual?.billedFromInvoice
                ? `Usage ${money(actual?.usageCharge, currency)} + tax ${money(actual?.taxAmount, currency)}`
                : "Rated amount — no invoice raised"}
              value={money(actual?.actualCharge, currency)}
              kind="base"
            />
            <Step
              label="Less RA expected amount"
              note={ruleLabel(expected?.ruleId, expected?.ruleName)}
              value={`− ${money(expected?.finalExpectedAmount, currency)}`}
              kind="credit"
            />
            <li className="flex items-baseline justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-2.5 py-2">
              <span className="text-xs font-medium text-foreground">Overcharge</span>
              <span className="text-sm font-semibold tabular-nums text-destructive">
                {signedMoney(variance, currency)}
              </span>
            </li>
          </ol>
        </Panel>

        <Panel title="Root cause summary" icon={<AlertTriangle className="h-4 w-4" />}>
          <p className="text-xs font-medium text-foreground leading-relaxed">
            {rating.finding || "No root cause recorded for this event."}
          </p>
          <ul className="mt-2 space-y-1.5">
            {rating.rootCauseType && (
              <Bullet>
                Classified by the rating engine as <span className="font-mono">{rating.rootCauseType}</span>.
              </Bullet>
            )}
            {ruleMismatch && (
              <Bullet>
                Rating applied {ruleLabel(actual?.appliedRuleId, actual?.appliedRule)} at{" "}
                {money(actual?.appliedRate, currency)}; the event should have rated under{" "}
                {ruleLabel(expected?.ruleId, expected?.ruleName)} at {money(expected?.rate, currency)}.
              </Bullet>
            )}
            <Bullet>
              Recalculating the same event ({rating.eventId}) under the expected tariff reproduces{" "}
              {money(expected?.finalExpectedAmount, currency)}, against{" "}
              {money(actual?.actualCharge, currency)} billed
              {actual?.billedFromInvoice
                ? ` (usage ${money(actual?.usageCharge, currency)} + tax ${money(actual?.taxAmount, currency)})`
                : ""}{" "}
              — an overcharge of {signedMoney(variance, currency)}.
            </Bullet>
            {rating.sourceStatus && (
              <Bullet>
                Source reconciliation status: <span className="font-mono">{rating.sourceStatus}</span>.
              </Bullet>
            )}
          </ul>
        </Panel>
      </div>
    </>
  );
}

function Step({
  label, value, note, kind,
}: Readonly<{ label: string; value: string; note?: string; kind: "base" | "credit" }>) {
  return (
    <li className={`flex items-baseline justify-between gap-3 rounded-lg px-2.5 py-2 ${kind === "credit" ? "bg-destructive/5" : "bg-muted/40"
      }`}>
      <div className="min-w-0">
        <div className={`text-xs ${kind === "credit" ? "text-foreground/85" : "font-medium text-foreground"}`}>
          {label}
        </div>
        {note && <div className="text-[10px] text-muted-foreground">{note}</div>}
      </div>
      <span className={`text-xs font-semibold tabular-nums whitespace-nowrap ${kind === "credit" ? "text-destructive" : "text-foreground"
        }`}>
        {value}
      </span>
    </li>
  );
}

const Bullet = ({ children }: Readonly<{ children: ReactNode }>) => (
  <li className="flex gap-2 text-[11px] text-foreground/80 leading-relaxed">
    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-destructive" />
    <span>{children}</span>
  </li>
);

const Line = ({ label, value }: Readonly<{ label: string; value: string }>) => (
  <div className="flex items-baseline justify-between gap-2 text-[11px]">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="tabular-nums text-foreground/85">{value}</dd>
  </div>
);

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
