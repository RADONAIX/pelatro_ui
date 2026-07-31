// ---------------------------------------------------------------------------
// Which report catalog the sidebar shows, per Assurance Scope.
//
// The menu lists ONLY generated reports — one per compiled Reconciliation rule,
// fetched per assurance. Authoring a rule adds its report; deleting the rule
// removes it. Nothing here is declared.
//
// The hardcoded suites that used to fill this menu (Record/File Sequence Check,
// AIR/SDP Reconciliation, Daily Reconciliation Summary, Undercharge/Overcharge
// …) are gone from it: they were fixed lists that no rule produced, so they said
// nothing about what this assurance actually checks. Their modules are NOT
// deleted — src/lib/reportsCatalog.ts and src/lib/rating/reportsCatalog.ts still
// supply column schemas and key resolution to the two report pages, which is why
// those keys still render if one is opened directly by URL.
//
// Both pages read the same `?report=<key>` search param, so the sidebar renders
// one accordion either way and only the catalog behind it changes.
// ---------------------------------------------------------------------------

import { useEffect, useMemo, useState } from "react";
import { fetchReconReports } from "@/lib/assurance/reconciliation-api";
import type { ReportEntry } from "@/lib/reportsCatalog";

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

// Only `path` differs now — the entries are whatever useReportCatalog fetches.
export const RATING_REPORT_CATALOG: ReportCatalog = {
  path: "/rating/reports",
  entries: [],
  groups: [],
  defaultKey: "",
};

export const PLATFORM_REPORT_CATALOG: ReportCatalog = {
  path: "/reports",
  entries: [],
  groups: [],
  defaultKey: "",
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
            entries: generated,
            groups: [RECON_GROUP],
            // Highlight the first report that can actually be opened; a rule
            // still awaiting its first run is listed but not linkable.
            defaultKey: generated.find((r) => r.available)?.key ?? "",
          },
    [base, generated],
  );
}

/** Heading the generated reports sit under, in both catalogs. */
export const RECON_GROUP = "Reconciliation Rules";
