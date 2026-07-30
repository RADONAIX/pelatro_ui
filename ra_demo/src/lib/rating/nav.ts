import {
  Activity,
  LayoutDashboard,
  Wallet,
  BookOpenCheck,
  Library,
  FileCog,
  FileSpreadsheet,
  ShieldCheck,
  Package,
  PlayCircle,
  FlaskConical,
  RotateCcw,
  AlertTriangle,
  FileBarChart2,
  Plug,
  Scale,
  ListChecks,
  TrendingDown,
  Settings2,
  Landmark,
  ScrollText,
  Gauge,
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
  { to: "/rating", label: "Dashboard & KPIs", icon: LayoutDashboard, phase: 1 },
  {
    to: "/rating/reports",
    label: "Reports & Certified Exports",
    icon: FileBarChart2,
    phase: 1,
  },
  {
    to: "/rating/pipeline",
    label: "Pipelines & Job Monitor",
    icon: Activity,
    phase: 1,
  },
  {
    to: "/rating/exceptions",
    label: "Case Management",
    icon: AlertTriangle,
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
  // by drilling down from Dashboard & KPIs and from each other, not from the
  // sidebar — the dashboard is already their entry point, so listing them here
  // duplicated navigation without adding a destination.
  {
    to: "/rating/admin",
    label: "Operations",
    icon: Settings2,
    phase: 2,
    children: [
      {
        to: "/rating/replay",
        label: "Replay & Recovery",
        icon: RotateCcw,
        phase: 5,
      },
      {
        to: "/rating/admin/tolerances",
        label: "Tolerance Policies",
        icon: Landmark,
        phase: 2,
      },
      {
        to: "/rating/admin/audit",
        label: "Audit Logs",
        icon: ScrollText,
        phase: 2,
      },
    ],
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
