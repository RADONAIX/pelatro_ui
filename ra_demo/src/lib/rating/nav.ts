import {
  Activity,
  BookOpenCheck,
  Briefcase,
  FileBarChart2,
  FileCog,
  FileSpreadsheet,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Library,
  Package,
  Plug,
  ShieldCheck,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Navigation for the Rating Assurance module. The Sidebar renders THIS list
// instead of the default one while the Assurance Scope is "Rating Assurance";
// every other scope falls through to the existing code path untouched.
//
// Structured as the eight product modules from the UX review: overview,
// reconciliation, rule governance, metadata, exceptions, replay, reports,
// administration. `phase` marks what is not built yet — those entries render
// exactly like the existing "SOON" report rows (disabled, not hidden) so the
// product's full shape is visible from day one and no one goes looking for a
// screen that isn't there.
// ---------------------------------------------------------------------------

export type RatingIcon = typeof LayoutDashboard;

export interface RatingNavChild {
  to: string;
  label: string;
  icon: RatingIcon;
  /** Delivery phase. Anything above 1 is shown disabled with a "soon" tag. */
  phase: number;
}

export interface RatingNavItem {
  to: string;
  label: string;
  icon: RatingIcon;
  phase: number;
  children?: RatingNavChild[];
}

export const RATING_NAV: RatingNavItem[] = [
  // No "Dashboard & KPIs" entry: the Assurance Dashboard the Sidebar injects
  // covers the selected assurance, and the Enterprise Dashboard covers all of
  // them, so a third rating-only dashboard was a duplicate of the first. The
  // /rating route is untouched and still resolves by URL.
  {
    to: "/rating/reports",
    label: "Reports & Certified Exports",
    icon: FileBarChart2,
    phase: 1,
  },
  // The batch job monitor (/pipelines), not the rating-run screen
  // (/rating/pipeline). This is the platform-wide AIR/SDP monitor — batch
  // status, the four stage cards, file quality and the export dialog — and it
  // is meaningful under every assurance scope, which the rating-run screen is
  // not. /rating/pipeline is unchanged and still resolves by URL.
  {
    to: "/pipelines",
    label: "Pipelines & Job Monitor",
    icon: Activity,
    phase: 1,
  },
  {
    to: "/rating/rules",
    label: "Rule Management",
    icon: BookOpenCheck,
    phase: 1,
    children: [
      {
        to: "/rating/rules/overview",
        label: "Rule Operations",
        icon: Gauge,
        phase: 1,
      },
      { to: "/rating/rules", label: "Rule Catalogue", icon: Library, phase: 1 },
      {
        to: "/rating/rules/new",
        label: "Create Rule",
        icon: FileCog,
        phase: 1,
      },
      {
        to: "/rating/rules/import",
        label: "Import Rules",
        icon: FileSpreadsheet,
        phase: 1,
      },
      { to: "/rating/connectors", label: "Rule Sources", icon: Plug, phase: 1 },
      {
        to: "/rating/approvals",
        label: "Approvals",
        icon: ShieldCheck,
        phase: 1,
      },
      { to: "/rating/snapshots", label: "Snapshots", icon: Package, phase: 1 },
      {
        to: "/rating/simulation",
        label: "Simulation",
        icon: FlaskConical,
        phase: 1,
      },
    ],
  },
  {
    to: "/rating/catalog",
    label: "Metadata Catalogue",
    icon: Library,
    phase: 1,
  },
  // The reconciliation screens (runs, records, leakage, balances) are reached
  // by drilling down from the dashboards and from each other, not from the
  // sidebar — the dashboard is already their entry point, so listing them here
  // duplicated navigation without adding a destination.
  //
  // No "Operations" group. Its declared children were all unbuilt phase-2+
  // placeholders, and once Data Sources moved to the top level its only real
  // entry was Controls — a group wrapping one item. The Sidebar injects
  // Controls and Data Sources directly, in the position this entry held.
  // The platform-wide case queue (/cases), not the rating exception list
  // (/rating/exceptions). Case Management is where every assurance's rules land
  // their findings — the same screen an analyst raises a case on by hand — so it
  // is meaningful under all eight scopes, which the rating-only exception list
  // is not. /rating/exceptions is unchanged and still reached by drilling down
  // from the rating dashboard, runs and pipeline screens.
  //
  // Sits after Operations: a case is the outcome of the controls configured
  // there, so the list reads setup-then-findings rather than the reverse.
  {
    to: "/cases",
    label: "Case Management",
    icon: Briefcase,
    phase: 1,
  },
  {
    to: "/rating/monitoring",
    label: "System Monitoring",
    icon: Gauge,
    phase: 1,
  },
];

/** Routes that exist today — everything else renders as a disabled "soon" row. */
export const RATING_AVAILABLE_PATHS: ReadonlySet<string> = new Set(
  RATING_NAV.flatMap((item) => [item, ...(item.children ?? [])])
    .filter((entry) => entry.phase === 1)
    .map((entry) => entry.to),
);

/**
 * Children whose path is a prefix of a sibling's (e.g. "/rating/rules" vs
 * "/rating/rules/new") must exact-match, or they stay highlighted on every
 * sibling route. Computed here so the renderer never hard-codes a path.
 */
export const EXACT_MATCH_PATHS: ReadonlySet<string> = new Set(
  RATING_NAV.flatMap((item) => item.children ?? [])
    .filter((child, _i, all) =>
      all.some(
        (other) => other.to !== child.to && other.to.startsWith(child.to),
      ),
    )
    .map((child) => child.to),
);
