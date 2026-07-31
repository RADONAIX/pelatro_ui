// Postpaid billing-shock investigation — Billing Assurance only.
//
// Five reads against the case service, one per step of the flow. Every figure
// rendered from these types comes out of the canonical rating and billing
// tables; nothing in this module computes or defaults a money value, so a blank
// on screen means the source system had nothing rather than that the UI gave up.
//
// The endpoints 404 for a case from any other assurance. `supportsInvestigation`
// is the same rule applied client-side, so a Usage or Network case never fires
// the request in the first place.

import { request, type AssuranceCase } from "@/lib/cases";

/** The assurance that owns postpaid invoicing. */
export const BILLING_ASSURANCE_CODE = "BA";

/** Fixed for this platform — the source tables carry no billing-type column. */
export const BILLING_TYPE = "BILL_CYCLE";

/**
 * Whether the postpaid investigation applies to this case. Billing Assurance
 * only: every other assurance answers from the mismatch rows its own control
 * emitted, and has no link into rating or invoicing.
 */
export const supportsInvestigation = (c: AssuranceCase) =>
  c.assuranceCode === BILLING_ASSURANCE_CODE;

// --- 1. Subscriber & invoice ------------------------------------------------

export interface SubscriberInfo {
  msisdn: string;
  accountType: string | null;
  offer: string | null;
  billingType: string;
}

export interface InvoiceInfo {
  invoiceId: string;
  usageCharge: number | null;
  taxAmount: number | null;
  totalInvoiceAmount: number | null;
  currency: string | null;
  createdAt: string | null;
}

export interface SubscriberResponse {
  caseId: string;
  caseReference: string;
  msisdn: string | null;
  available: boolean;
  subscriber: SubscriberInfo | null;
  invoice: InvoiceInfo | null;
}

// --- 2. MSC vs post mediation -----------------------------------------------

/** Voice and SMS only — data records are out of scope for a tariff fault, and
 *  `totalEvents` is voice + SMS rather than the source table's all-service total. */
export interface EventCounts {
  voice: number | null;
  sms: number | null;
  totalEvents: number | null;
}

export interface MediationResponse {
  caseReference: string;
  msisdn: string | null;
  available: boolean;
  billingDate?: string;
  services?: string[];
  msc?: EventCounts;
  postMediation?: EventCounts;
  /** Recomputed by the service from the counts above. */
  result?: "PASS" | "FAIL";
  sourceResult?: string | null;
  finding?: string;
}

// --- 3. Rating analysis -----------------------------------------------------

export interface ExpectedRating {
  ruleId: string | null;
  ruleName: string | null;
  rate: number | null;
  durationSeconds: number | null;
  durationLabel: string;
  expectedCharge: number | null;
  discount: number | null;
  surcharge: number | null;
  beforeTax: number | null;
  tax: number | null;
  finalExpectedAmount: number | null;
  taxRules: { rule_id?: string; rule_name?: string }[];
  discountRules: { rule_id?: string; rule_name?: string }[];
}

export interface ActualRating {
  appliedRuleId: string | null;
  appliedRule: string | null;
  appliedRate: number | null;
  /** What the customer was billed: the invoice total, tax included. This is the
   *  actual side of every customer-facing figure. */
  actualCharge: number | null;
  /** False when no invoice exists and `actualCharge` fell back to the pre-tax
   *  rated amount — the UI says so rather than implying a bill that isn't there. */
  billedFromInvoice: boolean;
  usageCharge: number | null;
  taxAmount: number | null;
  /** What rating alone produced, before tax and invoicing. */
  ratedCharge: number | null;
  /** actualCharge − finalExpectedAmount. */
  variance: number | null;
  /** The source table's own pre-tax comparison, kept for traceability. */
  sourceVariance: number | null;
}

export interface RatingResponse {
  caseReference: string;
  msisdn: string | null;
  available: boolean;
  eventId?: string;
  currency?: string | null;
  serviceType?: string | null;
  destination?: string | null;
  calledNumber?: string | null;
  eventTime?: string | null;
  expected?: ExpectedRating;
  actual?: ActualRating;
  result?: "PASS" | "FAIL";
  sourceStatus?: string | null;
  finding?: string;
  rootCauseType?: string;
}

// --- 4. Existing case check -------------------------------------------------

export interface RelatedCase {
  id: string;
  reference: string;
  title: string;
  issue: string;
  status: string;
  severity: string;
  owner: string;
  detectedAt: string;
  createdAt: string;
  resolved: boolean;
}

export interface RelatedCasesResponse {
  caseReference: string;
  msisdn: string | null;
  cases: RelatedCase[];
  unresolvedCount: number;
  message: string;
  /** What the unresolved case meant in practice, e.g. billing ran anyway. */
  impact: string;
}

// --- 5. Recommended actions -------------------------------------------------

export interface RecommendedAction {
  key: string;
  title: string;
  detail: string;
  primary: boolean;
}

export interface ActionsResponse {
  caseReference: string;
  msisdn: string | null;
  currency?: string;
  variance?: number | null;
  actions: RecommendedAction[];
}

/** Everything the investigation renders, in one place. */
export interface Investigation {
  subscriber: SubscriberResponse;
  mediation: MediationResponse;
  rating: RatingResponse;
  related: RelatedCasesResponse;
  actions: ActionsResponse;
}

const base = (caseId: string) => `/api/cases/${encodeURIComponent(caseId)}/investigation`;

export const fetchSubscriber = (caseId: string) =>
  request<SubscriberResponse>(`${base(caseId)}/subscriber`);

export const fetchMediation = (caseId: string) =>
  request<MediationResponse>(`${base(caseId)}/mediation`);

export const fetchRating = (caseId: string) =>
  request<RatingResponse>(`${base(caseId)}/rating`);

export const fetchRelatedCases = (caseId: string) =>
  request<RelatedCasesResponse>(`${base(caseId)}/cases`);

export const fetchActions = (caseId: string) =>
  request<ActionsResponse>(`${base(caseId)}/actions`);

/**
 * All five steps. They are independent reads, so they run together — the flow
 * shows every stage at once and there is nothing to serialise.
 */
export async function fetchInvestigation(caseId: string): Promise<Investigation> {
  const [subscriber, mediation, rating, related, actions] = await Promise.all([
    fetchSubscriber(caseId),
    fetchMediation(caseId),
    fetchRating(caseId),
    fetchRelatedCases(caseId),
    fetchActions(caseId),
  ]);
  return { subscriber, mediation, rating, related, actions };
}

/** Money as the source system reports it — never rounded into a nicer number. */
export function money(amount: number | null | undefined, currency?: string | null): string {
  if (amount == null) return "—";
  const value = amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency ? `${currency} ${value}` : value;
}

/** Signed variance: the sign is the whole point, so it is always shown. */
export function signedMoney(amount: number | null | undefined, currency?: string | null): string {
  if (amount == null) return "—";
  return `${amount > 0 ? "+" : ""}${money(amount, currency)}`;
}

// --- Assistant ---------------------------------------------------------------
// The assistant answers a billing-shock case from the loaded investigation, so
// every figure it quotes is the one on screen. There is no model behind this —
// the replies are composed from the same payload the panels render, which is
// why they can never drift from it.

export const INVESTIGATION_SUGGESTIONS = [
  "Why was the customer overcharged?",
  "Show expected vs actual charges.",
  "Was the usage captured correctly?",
  "Is there an existing case?",
  "What is the recommended fix?",
];

export function investigationAnswer(question: string, data: Investigation): string {
  const q = question.toLowerCase();
  const { subscriber, mediation, rating, related, actions } = data;
  const currency = rating.currency ?? subscriber.invoice?.currency ?? null;
  const variance = rating.actual?.variance;
  const msisdn = subscriber.msisdn ?? "this subscriber";

  const ruleOf = (id?: string | null, name?: string | null) =>
    id ? (name ? `${id} (${name})` : id) : "an unrecorded rule";

  if (q.includes("captur") || q.includes("mediat") || q.includes("msc") || q.includes("usage")) {
    if (!mediation.available) return `No mediation comparison is recorded for ${msisdn}.`;
    return (
      `${mediation.result === "PASS" ? "Yes" : "No"} — the switch recorded ` +
      `${mediation.msc?.totalEvents} billable events (voice ${mediation.msc?.voice}, ` +
      `SMS ${mediation.msc?.sms}) and post mediation shows ` +
      `${mediation.postMediation?.totalEvents}. ${mediation.finding ?? ""}`.trim()
    );
  }

  if (q.includes("existing case") || q.includes("already") || q.includes("raised before") || q.includes("history")) {
    if (!related.cases.length) return `No other case has been raised against ${msisdn}.`;
    return related.message || `${related.cases.length} other case(s) exist for ${msisdn}.`;
  }

  if (q.includes("expected") || q.includes("compare") || q.includes("breakdown") || q.includes("charge")) {
    if (!rating.available) return `No rated event is recorded for ${msisdn}.`;
    return (
      `Billed ${money(rating.actual?.actualCharge, currency)} ` +
      `(usage ${money(rating.actual?.usageCharge, currency)} + tax ` +
      `${money(rating.actual?.taxAmount, currency)}) against an expected ` +
      `${money(rating.expected?.finalExpectedAmount, currency)} — an overcharge of ` +
      `${signedMoney(variance, currency)}. The event rated ${rating.expected?.durationLabel} at ` +
      `${money(rating.expected?.rate, currency)} under ${ruleOf(rating.expected?.ruleId, rating.expected?.ruleName)}, ` +
      `but the network applied ${ruleOf(rating.actual?.appliedRuleId, rating.actual?.appliedRule)} at ` +
      `${money(rating.actual?.appliedRate, currency)}.`
    );
  }

  if (q.includes("fix") || q.includes("recommend") || q.includes("action") || q.includes("next") || q.includes("refund")) {
    if (!actions.actions.length) return "No remediation is available — the source tables show no variance to correct.";
    return actions.actions.map((a) => `${a.title}: ${a.detail}`).join(" ");
  }

  if (q.includes("impact") || q.includes("revenue") || q.includes("how much")) {
    return variance == null
      ? "No variance is recorded for this event."
      : `${money(variance, currency)}. The customer was billed ` +
        `${money(subscriber.invoice?.totalInvoiceAmount, currency)} ` +
        `(usage ${money(subscriber.invoice?.usageCharge, currency)} + tax ` +
        `${money(subscriber.invoice?.taxAmount, currency)}) where ` +
        `${money(rating.expected?.finalExpectedAmount, currency)} was expected.`;
  }

  if (q.includes("subscriber") || q.includes("who") || q.includes("account") || q.includes("offer")) {
    return (
      `${msisdn} — ${subscriber.subscriber?.accountType ?? "unknown account type"} on ` +
      `${subscriber.subscriber?.offer ?? "an unrecorded offer"}, billed ` +
      `${subscriber.subscriber?.billingType ?? BILLING_TYPE}. Invoice ` +
      `${subscriber.invoice?.invoiceId ?? "—"} totals ` +
      `${money(subscriber.invoice?.totalInvoiceAmount, currency)}.`
    );
  }

  // Why / cause / root — also the fallback, since it is the question the case exists to answer.
  if (!rating.available) return `No rated event is recorded for ${msisdn}, so the cause cannot be isolated.`;
  return (
    `${rating.finding || "Rating did not reconcile"}. Usage capture and mediation are clean ` +
    `(${mediation.msc?.totalEvents ?? "—"} voice and SMS events matched), so the fault is in ` +
    `rating: the event rated under ` +
    `${ruleOf(rating.actual?.appliedRuleId, rating.actual?.appliedRule)} instead of ` +
    `${ruleOf(rating.expected?.ruleId, rating.expected?.ruleName)}, and the customer was billed ` +
    `${money(rating.actual?.actualCharge, currency)} against an expected ` +
    `${money(rating.expected?.finalExpectedAmount, currency)} — an overcharge of ` +
    `${signedMoney(variance, currency)}.`
  );
}
