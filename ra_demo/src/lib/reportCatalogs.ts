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

import { useEffect, useMemo, useState } from "react";
import { fetchReconReports } from "@/lib/assurance/reconciliation-api";
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

/**
 * The scope's catalog with its generated reconciliation reports merged in.
 *
 * One entry per compiled Reconciliation rule for this assurance, under a
 * "Reconciliation" heading below the declared groups. Nothing about them is
 * hardcoded — the list is whatever the backend has compiled, so authoring a
 * rule adds a report and deleting the rule removes it.
 *
 * A rule that has not completed a run yet comes back `available: false`, which
 * renders as the existing disabled "soon" row rather than a link into an empty
 * table.
 */
export function useReportCatalog(scope: string): ReportCatalog {
  const base = catalogForScope(scope);
  const [generated, setGenerated] = useState<ReportEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    // With no assurance selected there is nothing to scope generated reports
    // to, and an unscoped fetch would list every assurance's — so it is skipped
    // rather than sent without a filter.
    if (!scope) {
      setGenerated([]);
      return;
    }
    fetchReconReports(scope)
      .then((reports) => {
        if (cancelled) return;
        setGenerated(
          reports.map((r) => ({
            key: r.key,
            title: r.title,
            group: RECON_GROUP,
            available: r.available,
            description: r.description,
          })),
        );
      })
      // A reporting outage must not empty the menu of the static reports that
      // are still perfectly readable, so this failure is silent by design.
      .catch(() => {
        if (!cancelled) setGenerated([]);
      });
    return () => {
      cancelled = true;
    };
  }, [scope]);

  return useMemo(
    () =>
      generated.length === 0
        ? base
        : {
            ...base,
            entries: [...base.entries, ...generated],
            groups: [...base.groups, RECON_GROUP],
          },
    [base, generated],
  );
}

/** Heading the generated reports sit under, in both catalogs. */
export const RECON_GROUP = "Reconciliation Rules";
