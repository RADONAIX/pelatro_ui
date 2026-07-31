// DEMO-ONLY. Mock analysis of a customer-supplied postpaid bill.
//
// The scenario: a customer uploads their bill with the Add Case form and says
// "I was charged more than expected". Clicking Analyze Bill walks the postpaid
// assurance chain — MSC → Post Mediation → Rating → Billing — and lands on the
// stage that actually failed.
//
// Nothing here talks to a backend: there is no OCR, no model and no re-rating
// engine in this project. Every figure below is fixed mock data chosen to be
// internally consistent, so the numbers reconcile if a viewer adds them up.
// Wiring this to real analysis means replacing this module, not the UI.

import type { AssuranceCase } from "@/lib/cases";

/** Issue type used for cases an analyst raises without a control rule. */
export const MANUAL_INVESTIGATION = "Manual Investigation";

/** Ghanaian cedi — this scenario is a Ghana postpaid account. */
export const ghs = (amount: number) =>
  `GHS ${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Bill analysis applies to cases an analyst raised by hand — the ones where a
 * customer document is the starting point rather than a failed control.
 */
export const isManualInvestigation = (c: AssuranceCase) =>
  c.ruleCategory === MANUAL_INVESTIGATION || (c.origin === "analyst_raised" && !c.ruleId);

/** The bill the customer uploaded. Pre-filled into the Add Case dialog. */
export const MOCK_BILL = {
  filename: "Postpaid_Bill_July_2026.pdf",
  customer: "Kwame Mensah",
  msisdn: "233241567890",
  account: "ACC-4471902",
  plan: "Postpaid Flex 150",
  cycle: "July 2026",
  billedAmount: 428.5,
  expectedAmount: 290.0,
} as const;

export const OVERCHARGE = MOCK_BILL.billedAmount - MOCK_BILL.expectedAmount;   // 138.50

/** Stages the progress animation walks through. Cosmetic, but it sets the
 *  expectation that real analysis is several passes, not one lookup. */
export const ANALYSIS_STEPS = [
  { key: "read", label: "Reading bill", detail: "Extracting line items from the PDF" },
  { key: "match", label: "Matching subscriber", detail: `Resolving MSISDN ${MOCK_BILL.msisdn}` },
  { key: "validate", label: "Validating records", detail: "Checking CDRs across the assurance chain" },
  { key: "recalc", label: "Recalculating charges", detail: "Re-rating usage against the active tariff" },
  { key: "done", label: "Completing investigation", detail: "Assembling findings" },
] as const;

export type StageStatus = "pass" | "fail" | "warning";

export interface FlowStage {
  key: string;
  name: string;
  status: StageStatus;
  /** One-line verdict shown under the stage name. */
  summary: string;
  recordsProcessed: string;
  /** Per-check results. Omitted on a stage that drills down instead. */
  checks?: { label: string; ok: boolean; value: string }[];
  /** Only the failing stage carries figures. */
  figures?: { label: string; value: string; tone?: "bad" | "good" }[];
  /** One-line cause, shown in place of a findings list on a compact card. */
  rootCause?: string;
  /** Opens the detailed reconciliation drawer rather than expanding in place. */
  drillDown?: boolean;
  findings?: string[];
}

/** MSC → Post Mediation → Rating → Billing, in processing order. */
export const FLOW_STAGES: FlowStage[] = [
  {
    key: "msc",
    name: "MSC",
    status: "pass",
    summary: "All usage the switch recorded reached the platform.",
    recordsProcessed: "42 CDRs",
    checks: [
      { label: "CDRs found for the cycle", ok: true, value: "42" },
      { label: "Duplicate detection", ok: true, value: "0 duplicates" },
      { label: "Sequence continuity", ok: true, value: "No missing records" },
    ],
  },
  {
    key: "mediation",
    name: "Post Mediation",
    status: "pass",
    summary: "Every switch record was mediated and reconciled.",
    recordsProcessed: "42 matched",
    checks: [
      { label: "Matched to switch export", ok: true, value: "42 / 42" },
      { label: "Rejected records", ok: true, value: "0 rejected" },
      { label: "Reconciliation", ok: true, value: "Passed" },
    ],
  },
  // The root-cause stage stays deliberately thin: three figures and a one-line
  // cause. The full reconciliation lives in RATING_ANALYSIS behind the
  // drill-down, so the flow row reads at a glance instead of turning into a
  // report squeezed into a quarter-width column.
  {
    key: "rating",
    name: "Rating",
    status: "fail",
    summary: "Charges were computed against the wrong tariff version.",
    recordsProcessed: "42 rated",
    figures: [
      { label: "Network Rated Amount (Actual)", value: ghs(356.0), tone: "bad" },
      { label: "RA Recalculated Amount (Expected)", value: ghs(217.5), tone: "good" },
      { label: "Variance", value: `+${ghs(138.5)}`, tone: "bad" },
    ],
    rootCause: "Wrong tariff version and included voice allowance not applied.",
    drillDown: true,
  },
  {
    key: "billing",
    name: "Billing",
    status: "warning",
    summary: "Billing is faithful to its input — the input was wrong.",
    recordsProcessed: "1 invoice",
    checks: [
      { label: "Invoice vs rating output", ok: true, value: "Matches exactly" },
      { label: "Rental and tax lines", ok: true, value: "Correct" },
      { label: "Upstream input", ok: false, value: "Inherited the rating error" },
    ],
    findings: [
      "The invoice reproduces the rating output to the cent, so no billing defect is present.",
      "The customer-visible overcharge originates in Rating and is corrected by re-rating, not by a billing adjustment.",
    ],
  },
];

export const ROOT_CAUSE_STAGE = "Rating";

// ---------------------------------------------------------------------------
// Rating drill-down
// ---------------------------------------------------------------------------
// The reconciliation behind the Rating card. Every figure is derived from the
// same usage, so the two sides tie out:
//
//   voice usage 312m 40s @ GHS 0.75/min                     = 234.50
//     less 150 included minutes            (150m    × 0.75) = -112.50
//     less on-net promo minutes            ( 34m40s × 0.75) =  -26.00
//                                                    voice  =   96.00
//   data 8.10 GB out of bundle @ GHS 15.00/GB               =  121.50  (both sides)
//   SMS 138 of 200 in bundle                                =    0.00  (both sides)
//
//   network rated 234.50 + 121.50 = 356.00
//   RA rated       96.00 + 121.50 = 217.50      variance = 138.50
//
// Rating output excludes the GHS 72.50 monthly rental, which Billing adds
// downstream — that is why these totals are not the bill totals.

export interface RatingSide {
  title: string;
  engine: string;
  tariff: string;
  amount: number;
  tone: "bad" | "good";
  lines: { label: string; value: string }[];
}

export interface RatingCompareRow {
  label: string;
  actual: string;
  expected: string;
  mismatch: boolean;
}

export interface AllowanceRow {
  label: string;
  entitlement: string;
  consumed: string;
  networkApplied: string;
  raApplied: string;
  mismatch: boolean;
}

export interface ChargeRow {
  service: string;
  usage: string;
  actual: string;
  expected: string;
  variance: string;
  mismatch: boolean;
  total?: boolean;
}

export interface VarianceStep {
  label: string;
  value: string;
  note?: string;
  kind: "base" | "credit" | "total";
}

export const RATING_ANALYSIS = {
  subtitle: `${MOCK_BILL.plan} · ${MOCK_BILL.cycle} · ${MOCK_BILL.msisdn}`,
  scopeNote:
    `Rating output only — the ${ghs(72.5)} monthly rental is added downstream by Billing, ` +
    `so these totals differ from the ${ghs(MOCK_BILL.billedAmount)} invoice.`,

  sides: [
    {
      title: "Network Rating (Actual)",
      engine: "Network rating engine · run RTG-2026-07-31",
      tariff: "Tariff v11 (superseded 30 Jun 2026)",
      amount: 356.0,
      tone: "bad",
      lines: [
        { label: "Voice", value: ghs(234.5) },
        { label: "Data", value: ghs(121.5) },
        { label: "SMS", value: ghs(0) },
        { label: "Credits applied", value: ghs(0) },
        { label: "Records rated", value: "42 CDRs" },
      ],
    },
    {
      title: "RA Recalculated Rating (Expected)",
      engine: "Assurance re-rating · same 42 CDRs",
      tariff: "Tariff v12 (effective 01 Jul 2026)",
      amount: 217.5,
      tone: "good",
      lines: [
        { label: "Voice", value: ghs(96.0) },
        { label: "Data", value: ghs(121.5) },
        { label: "SMS", value: ghs(0) },
        { label: "Credits applied", value: `−${ghs(138.5)}` },
        { label: "Records rated", value: "42 CDRs" },
      ],
    },
  ] as RatingSide[],

  tariff: [
    { label: "Tariff version", actual: "v11", expected: "v12", mismatch: true },
    { label: "Version effective from", actual: "01 Jan 2026", expected: "01 Jul 2026", mismatch: true },
    { label: "Rate plan", actual: MOCK_BILL.plan, expected: MOCK_BILL.plan, mismatch: false },
    { label: "Out-of-bundle voice rate", actual: "GHS 0.75 / min", expected: "GHS 0.75 / min", mismatch: false },
    { label: "Included voice minutes", actual: "0 min (not applied)", expected: "150 min", mismatch: true },
    { label: "On-net promo", actual: "Not applied", expected: "Applied", mismatch: true },
    { label: "Data rate", actual: "GHS 15.00 / GB", expected: "GHS 15.00 / GB", mismatch: false },
    { label: "SMS bundle", actual: "200 SMS", expected: "200 SMS", mismatch: false },
  ] as RatingCompareRow[],

  allowances: [
    {
      label: "Included voice minutes", entitlement: "150 min", consumed: "312m 40s",
      networkApplied: "0 min", raApplied: "150 min", mismatch: true,
    },
    {
      label: "On-net promo minutes", entitlement: "Unlimited on-net", consumed: "34m 40s",
      networkApplied: "0 min", raApplied: "34m 40s", mismatch: true,
    },
    {
      label: "Data bundle", entitlement: "10.00 GB", consumed: "18.10 GB",
      networkApplied: "10.00 GB", raApplied: "10.00 GB", mismatch: false,
    },
    {
      label: "SMS bundle", entitlement: "200 SMS", consumed: "138 SMS",
      networkApplied: "138 SMS", raApplied: "138 SMS", mismatch: false,
    },
  ] as AllowanceRow[],

  charges: [
    {
      service: "Voice", usage: "312m 40s across 42 calls",
      actual: ghs(234.5), expected: ghs(96.0), variance: `+${ghs(138.5)}`, mismatch: true,
    },
    {
      service: "Data", usage: "8.10 GB out of bundle",
      actual: ghs(121.5), expected: ghs(121.5), variance: "—", mismatch: false,
    },
    {
      service: "SMS", usage: "138 of 200 in bundle",
      actual: ghs(0), expected: ghs(0), variance: "—", mismatch: false,
    },
    {
      service: "Total rated", usage: "Excludes monthly rental",
      actual: ghs(356.0), expected: ghs(217.5), variance: `+${ghs(138.5)}`, mismatch: true, total: true,
    },
  ] as ChargeRow[],

  discounts: [
    {
      label: "Included voice allowance (150 min × GHS 0.75)",
      actual: "Not applied", expected: `−${ghs(112.5)}`, mismatch: true,
    },
    {
      label: "On-net promo credit (34m 40s × GHS 0.75)",
      actual: "Not applied", expected: `−${ghs(26.0)}`, mismatch: true,
    },
    {
      label: "Loyalty / corporate discount",
      actual: "Not eligible", expected: "Not eligible", mismatch: false,
    },
    {
      label: "Total credits applied",
      actual: ghs(0), expected: `−${ghs(138.5)}`, mismatch: true,
    },
  ] as RatingCompareRow[],

  variance: [
    { label: "Network rated amount (v11)", value: ghs(356.0), kind: "base" },
    {
      label: "Included voice allowance not applied", value: `−${ghs(112.5)}`,
      note: "150 min charged out of bundle at GHS 0.75/min", kind: "credit",
    },
    {
      label: "On-net promo credit not applied", value: `−${ghs(26.0)}`,
      note: "34m 40s of on-net calls charged at the standard rate", kind: "credit",
    },
    { label: "RA recalculated amount (v12)", value: ghs(217.5), kind: "total" },
  ] as VarianceStep[],

  varianceTotal: `+${ghs(138.5)}`,

  rootCause: {
    headline: "Rating executed against a superseded tariff version, so no voice allowance was applied.",
    points: [
      "The subscriber moved to tariff v12 on 01 Jul 2026, but the rating run resolved v11 — the version that was current when the rate-plan mapping was last published.",
      "Under v11 the Postpaid Flex 150 bundle carries no included minutes, so all 312m 40s of voice was charged out of bundle at GHS 0.75/min instead of the first 150 minutes drawing on the allowance.",
      "The v12 on-net promotion was not evaluated either, leaving a further 34m 40s of on-net calls charged at the standard rate.",
      "Data and SMS rated identically on both sides — the rates are unchanged between v11 and v12, so the entire variance sits in voice.",
      `Re-rating the same 42 CDRs under v12 reproduces ${ghs(217.5)} exactly, which confirms the tariff mapping as the sole cause.`,
    ],
  },
};

/** Headline verdict shown above the flow. */
export const INVESTIGATION_SUMMARY = {
  status: "Overcharge Confirmed",
  confidence: 96,
  rootCause: ROOT_CAUSE_STAGE,
  revenueImpact: OVERCHARGE,
  recommendedRefund: OVERCHARGE,
  narrative:
    `${MOCK_BILL.customer} was billed ${ghs(MOCK_BILL.billedAmount)} for ${MOCK_BILL.cycle} against an ` +
    `expected ${ghs(MOCK_BILL.expectedAmount)}. Usage capture and mediation are clean; Rating charged all ` +
    `42 calls out of bundle because it applied a superseded tariff version.`,
} as const;

export interface ComparisonRow {
  label: string;
  expected: string;
  actual: string;
  difference: string;
  /** Marks the rows that actually diverge, so the eye lands on them. */
  mismatch: boolean;
  note?: string;
}

/** Rental + voice + data reconcile to the final bill on both sides. */
export const COMPARISON_ROWS: ComparisonRow[] = [
  { label: "Monthly rental", expected: ghs(72.5), actual: ghs(72.5), difference: "—", mismatch: false },
  {
    label: "Voice charges", expected: ghs(96.0), actual: ghs(234.5), difference: `+${ghs(138.5)}`,
    mismatch: true, note: "All 42 calls rated out of bundle at GHS 0.75/min",
  },
  { label: "Data charges", expected: ghs(121.5), actual: ghs(121.5), difference: "—", mismatch: false },
  {
    label: "Included minutes applied", expected: "150 mins", actual: "0 mins", difference: "-150 mins",
    mismatch: true, note: "Bundle allowance not deducted by Rating",
  },
  {
    label: "Final bill", expected: ghs(MOCK_BILL.expectedAmount), actual: ghs(MOCK_BILL.billedAmount),
    difference: `+${ghs(OVERCHARGE)}`, mismatch: true,
  },
];

export interface AffectedRecord {
  cdrId: string;
  date: string;
  duration: string;
  expected: string;
  actual: string;
  difference: string;
  reason: string;
}

/** Five of the 42 affected calls. Charged at 0.75/min against an allowance
 *  that should have covered them, so `expected` is nil on every row. */
export const AFFECTED_RECORDS: AffectedRecord[] = [
  { cdrId: "CDR-88412001", date: "02 Jul 2026 09:14", duration: "12m 30s", expected: ghs(0), actual: ghs(9.38), difference: `+${ghs(9.38)}`, reason: "Included voice allowance not applied." },
  { cdrId: "CDR-88412088", date: "05 Jul 2026 14:02", duration: "8m 10s", expected: ghs(0), actual: ghs(6.13), difference: `+${ghs(6.13)}`, reason: "Included voice allowance not applied." },
  { cdrId: "CDR-88412154", date: "09 Jul 2026 18:47", duration: "21m 05s", expected: ghs(0), actual: ghs(15.81), difference: `+${ghs(15.81)}`, reason: "Included voice allowance not applied." },
  { cdrId: "CDR-88412207", date: "14 Jul 2026 11:23", duration: "5m 40s", expected: ghs(0), actual: ghs(4.25), difference: `+${ghs(4.25)}`, reason: "Included voice allowance not applied." },
  { cdrId: "CDR-88412290", date: "22 Jul 2026 20:16", duration: "17m 45s", expected: ghs(0), actual: ghs(13.31), difference: `+${ghs(13.31)}`, reason: "Included voice allowance not applied." },
];

export const AFFECTED_TOTAL = 42;
export const AFFECTED_SAMPLE_VALUE = 48.88;   // the five rows above

export interface RecommendedAction {
  key: string;
  title: string;
  detail: string;
  /** Wording of the confirmation toast — these are demo-only interactions. */
  done: string;
  primary?: boolean;
}

export const RECOMMENDED_ACTIONS: RecommendedAction[] = [
  {
    key: "refund", title: "Refund customer", primary: true,
    detail: `Credit ${ghs(OVERCHARGE)} to ${MOCK_BILL.account} on the next cycle.`,
    done: `Refund of ${ghs(OVERCHARGE)} queued for ${MOCK_BILL.account}`,
  },
  {
    key: "tariff", title: "Correct tariff version",
    detail: "Point the Postpaid Flex 150 rating rule at tariff v12.",
    done: "Change request CHG-4482 raised against the rating configuration",
  },
  {
    key: "rerate", title: "Re-rate subscriber",
    detail: `Re-run rating for ${MOCK_BILL.msisdn} across the ${MOCK_BILL.cycle} cycle.`,
    done: `Re-rate job queued for ${MOCK_BILL.msisdn}`,
  },
  {
    key: "similar", title: "Check similar subscribers",
    detail: "Sweep every account on Flex 150 rated between 01–31 Jul for the same pattern.",
    done: "Sweep started — 1,284 Flex 150 accounts queued for review",
  },
  {
    key: "escalate", title: "Escalate to Rating team",
    detail: "Hand the root cause to the team that owns the tariff catalogue.",
    done: "Escalated to the Rating team with the analysis attached",
  },
];

/** Suggested questions shown in the assistant for a bill investigation. */
export const BILL_SUGGESTIONS = [
  "Why was the customer overcharged?",
  "Show expected vs actual charges.",
  "Which records are affected?",
  "Where did the failure occur?",
  "What is the recommended fix?",
];

/**
 * Canned assistant answers for the bill scenario. First matching entry wins,
 * so the more specific intents are listed before the general ones.
 */
export const BILL_ANSWERS: { match: string[]; answer: string }[] = [
  {
    match: ["why", "overcharg", "reason", "cause", "root"],
    answer:
      `${MOCK_BILL.customer} was overcharged ${ghs(OVERCHARGE)} because Rating applied tariff v11 instead of ` +
      `the v12 version active from 01 Jul 2026. Under v11 the 150 included voice minutes on Postpaid Flex 150 ` +
      `were never deducted, so all 42 calls were charged out of bundle at GHS 0.75/min. Usage capture and ` +
      "mediation were clean — this is a rating configuration fault, not lost or duplicated usage.",
  },
  {
    match: ["expected vs actual", "expected", "compare", "comparison", "breakdown", "charges"],
    answer:
      `Expected ${ghs(MOCK_BILL.expectedAmount)} vs actual ${ghs(MOCK_BILL.billedAmount)}. Rental is ` +
      `${ghs(72.5)} on both sides and data is ${ghs(121.5)} on both sides. The whole difference sits in voice: ` +
      `${ghs(96.0)} expected against ${ghs(234.5)} charged, a ${ghs(OVERCHARGE)} overcharge.`,
  },
  {
    match: ["which record", "records affected", "affected", "cdr", "calls"],
    answer:
      `All ${AFFECTED_TOTAL} voice CDRs in the ${MOCK_BILL.cycle} cycle are affected — every call that should ` +
      `have drawn on the 150-minute allowance. The five shown in Affected Records account for ` +
      `${ghs(AFFECTED_SAMPLE_VALUE)} of the ${ghs(OVERCHARGE)} total. Data and SMS records are unaffected.`,
  },
  {
    match: ["where", "which stage", "fail", "stage", "flow"],
    answer:
      "The failure is in Rating. MSC captured all 42 CDRs with no duplicates or gaps, Post Mediation matched " +
      "all 42 with nothing rejected, and Billing reproduced the rating output exactly. Rating is the only " +
      "stage that changed the number, so it is the root cause; Billing is flagged only because it inherited " +
      "the bad input.",
  },
  {
    match: ["fix", "recommend", "remediat", "next", "action", "refund", "resolve"],
    answer:
      `Refund ${ghs(OVERCHARGE)} to ${MOCK_BILL.account}, repoint the Flex 150 rating rule at tariff v12, then ` +
      `re-rate ${MOCK_BILL.msisdn} for ${MOCK_BILL.cycle} to confirm the corrected total of ` +
      `${ghs(MOCK_BILL.expectedAmount)}. Because the tariff mapping is shared, sweep the other Flex 150 ` +
      "accounts rated in the same window before closing this case.",
  },
  {
    match: ["impact", "revenue", "how much", "amount"],
    answer:
      `${ghs(OVERCHARGE)} on this account. If the same tariff mapping was used for every Flex 150 subscriber ` +
      "rated in July, the exposure is materially larger — the sweep in Recommended Actions sizes it.",
  },
  {
    match: ["confiden", "sure", "certain"],
    answer:
      `Confidence is ${INVESTIGATION_SUMMARY.confidence}%. The recalculation reproduces the expected ` +
      `${ghs(MOCK_BILL.expectedAmount)} exactly under tariff v12, and the upstream stages are clean, which ` +
      "leaves little room for an alternative explanation.",
  },
];

export function billAnswer(question: string): string {
  const q = question.toLowerCase();
  for (const entry of BILL_ANSWERS) {
    if (entry.match.some((needle) => q.includes(needle))) return entry.answer;
  }
  return (
    `I can help with this bill investigation for ${MOCK_BILL.customer} (${MOCK_BILL.msisdn}). Try: ` +
    '"why was the customer overcharged", "show expected vs actual charges", "which records are affected", ' +
    '"where did the failure occur", or "what is the recommended fix".'
  );
}
