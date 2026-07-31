// Rating Assurance report catalog — mirrors the backend REPORTS registry in
// app/modules/reports/registry.py. Declared here (rather than fetched) for the
// same reason the mediation catalog is: the Sidebar renders it as a grouped
// nav accordion before any request has been made.
//
// Shared by the rating Reports page (drill-down table) and the rating sidebar.

export interface RatingReportEntry {
  key: string;
  title: string;
  group: string;
  available: boolean;
  description?: string;
  /** Column headers, so the table head renders even when a report has no rows. */
  columns?: string[];
}

const RECON_COLUMNS = (dimension: string) => [
  dimension,
  "cdrs",
  "matched",
  "match_rate_pct",
  "expected",
  "billed",
  "undercharge",
  "overcharge",
  "net_leakage",
];

const DETAIL_COLUMNS = [
  "cdr_id",
  "event_date",
  "msisdn",
  "service",
  "product",
  "destination_zone",
  "status",
  "root_cause",
  "expected",
  "billed",
  "variance",
  "currency",
  "run_id",
];

export const RATING_REPORTS: RatingReportEntry[] = [
  {
    key: "daily_reconciliation_summary",
    title: "Daily Reconciliation Summary",
    group: "Reconciliation",
    available: true,
    description:
      "Expected versus billed revenue and both exposures, one row per event day. The first place to look when a day's revenue moves unexpectedly.",
    columns: RECON_COLUMNS("event_date"),
  },
  {
    key: "product_reconciliation",
    title: "Product Leakage Report",
    group: "Reconciliation",
    available: true,
    description:
      "Leakage and overcharge per product, ranked by total exposure so a product that both leaks and overbills cannot hide behind a net of zero.",
    columns: RECON_COLUMNS("product"),
  },
  {
    key: "service_reconciliation",
    title: "Service Reconciliation",
    group: "Reconciliation",
    available: true,
    description:
      "The same reconciliation cut by service — voice, SMS, data and roaming side by side.",
    columns: RECON_COLUMNS("service"),
  },
  {
    key: "destination_reconciliation",
    title: "Destination Reconciliation",
    group: "Reconciliation",
    available: true,
    description:
      "Leakage per destination zone. Mispriced routes usually show up here before anywhere else.",
    columns: RECON_COLUMNS("destination_zone"),
  },
  {
    key: "root_cause_summary",
    title: "Rule Leakage Report",
    group: "Reconciliation",
    available: true,
    description:
      "Variance grouped by diagnosed root cause — wrong pulse, wrong tax, missing rule, rounding — so the fix with the biggest payoff is obvious.",
    columns: RECON_COLUMNS("root_cause"),
  },
  {
    key: "undercharge_detail",
    title: "Undercharge Report",
    group: "Detail",
    available: true,
    description:
      "Every CDR billed below its expected charge, biggest variance first. This is revenue the operator never collected.",
    columns: DETAIL_COLUMNS,
  },
  {
    key: "overcharge_detail",
    title: "Overcharge Report",
    group: "Detail",
    available: true,
    description:
      "Every CDR billed above its expected charge. Not revenue — customer harm, and a regulatory exposure.",
    columns: DETAIL_COLUMNS,
  },
  {
    key: "no_matching_rule",
    title: "No Matching Rule Report",
    group: "Detail",
    available: true,
    description:
      "CDRs no rule could price, plus ambiguous multi-rule matches and unresolved products — the coverage gaps in the rule estate.",
    columns: DETAIL_COLUMNS,
  },
  {
    key: "unrated_cdrs",
    title: "Unrated CDR Report",
    group: "Detail",
    available: true,
    description:
      "Rateable usage the billing system produced no charge for at all.",
    columns: DETAIL_COLUMNS,
  },
  {
    key: "tax_reconciliation",
    title: "Tax Reconciliation",
    group: "Financial",
    available: true,
    description:
      "Expected tax and tax-attributed variance per product, with the count of tax-caused exceptions.",
    columns: [
      "product",
      "cdrs",
      "expected_tax",
      "tax_variance",
      "tax_exceptions",
    ],
  },
  {
    key: "exception_ageing",
    title: "Exception Ageing Report",
    group: "Operations",
    available: true,
    description:
      "Open investigations by age — what has been sitting unworked, and how much money is waiting on it.",
    columns: [
      "title",
      "status",
      "severity",
      "root_cause",
      "cdr_count",
      "revenue_impact",
      "assigned_to",
      "age_days",
      "created_at",
    ],
  },
];

export const RATING_REPORT_GROUPS = [
  "Reconciliation",
  "Detail",
  "Financial",
  "Operations",
];

export const AVAILABLE_RATING_REPORTS = RATING_REPORTS.filter(
  (r) => r.available,
);

export const DEFAULT_RATING_REPORT_KEY = AVAILABLE_RATING_REPORTS[0]?.key ?? "";

/** Resolve a (possibly user-supplied) report key to a valid, available one. */
export function resolveRatingReportKey(key: string | undefined): string {
  if (key && RATING_REPORTS.some((r) => r.key === key && r.available))
    return key;
  return DEFAULT_RATING_REPORT_KEY;
}
