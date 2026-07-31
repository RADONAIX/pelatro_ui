// ---------------------------------------------------------------------------
// Which report catalog the sidebar shows, per Assurance Scope.
//
// There are two report suites in the product and they are not interchangeable:
//
//   Rating Assurance → /rating/reports — the rating-service reports (Daily
//     Reconciliation Summary, Product Leakage, Undercharge/Overcharge, Tax
//     Reconciliation …). Backed by ra_rating_backend; they only mean something
//     for the rating domain.
//
//   Every other scope → /reports — the platform reports (Record/File Sequence
//     Check, File Exception, File Summary, AIR/SDP Reconciliation, Report Batch
//     Log). Backed by the main API and meaningful under any assurance.
//
// Both pages read the same `?report=<key>` search param, so the sidebar renders
// one accordion either way and only the catalog behind it changes.
// ---------------------------------------------------------------------------

import {
  REPORTS,
  GROUPS,
  DEFAULT_REPORT_KEY,
  type ReportEntry,
} from "@/lib/reportsCatalog";
import {
  RATING_REPORTS,
  RATING_REPORT_GROUPS,
  DEFAULT_RATING_REPORT_KEY,
} from "@/lib/rating/reportsCatalog";

export interface ReportCatalog {
  /** The page the catalog's entries link into. */
  path: string;
  /** Every entry, available or not — unavailable ones render as "soon". */
  entries: readonly ReportEntry[];
  /** Group headings, in render order. Empty groups are skipped. */
  groups: readonly string[];
  /** Selected when the URL carries no (or an unknown) `?report=` key. */
  defaultKey: string;
}

export const RATING_REPORT_CATALOG: ReportCatalog = {
  path: "/rating/reports",
  entries: RATING_REPORTS,
  groups: RATING_REPORT_GROUPS,
  defaultKey: DEFAULT_RATING_REPORT_KEY,
};

export const PLATFORM_REPORT_CATALOG: ReportCatalog = {
  path: "/reports",
  entries: REPORTS,
  groups: GROUPS,
  defaultKey: DEFAULT_REPORT_KEY,
};

/** The catalog for an Assurance Scope id. */
export function catalogForScope(scope: string): ReportCatalog {
  return scope === "rating" ? RATING_REPORT_CATALOG : PLATFORM_REPORT_CATALOG;
}
