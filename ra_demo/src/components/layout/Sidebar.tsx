import {
  Briefcase,
  ChevronLeft,
  ChevronRight,
  Database,
  LayoutDashboard,
  LayoutGrid,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { useMemo } from "react";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { RatingSidebarNav } from "@/components/layout/RatingSidebarNav";
import { AssuranceSidebarNav } from "@/components/layout/AssuranceSidebarNav";
import { RATING_NAV, RATING_AVAILABLE_PATHS, type RatingNavItem } from "@/lib/rating/nav";
import { useAssuranceScope } from "@/lib/assuranceScope";
import { catalogForScope } from "@/lib/reportCatalogs";

// ---------------------------------------------------------------------------
// One continuous module list, from two sources:
//   RatingSidebarNav      — Reports, Pipelines, Case Management, Rule
//                           Management (+children), Metadata Catalogue,
//                           Operations, System Monitoring.
//                           (src/lib/rating/nav.ts)
//   AssuranceSidebarNav   — the entity scope for the app currently selected in
//                           the header.
//
// Plus three injected above them: Overview, the cross-assurance Enterprise
// Dashboard, and the selected assurance's own Assurance Dashboard. There is
// deliberately no fourth "Dashboard & KPIs" — see the note in rating/nav.ts.
//
// Almost nothing moves when the Assurance Scope changes: the scope re-targets
// Controls and Administration, and hides the two rating-specific modules below.
// Every other module is present under every scope.
// ---------------------------------------------------------------------------

/**
 * Modules that only exist for the rating domain. Rule authoring and the
 * metadata catalogue are backed by ra_rating_backend and have no equivalent
 * for Usage, Billing, Partner and the rest, so listing them under those scopes
 * would offer rating screens for an app they say nothing about.
 */
const RATING_ONLY_PATHS = new Set(["/rating/rules", "/rating/catalog"]);

const RATING_SCOPE_ID = "rating";

/** The group whose children this file replaces wholesale. */
const OPERATIONS_PATH = "/rating/admin";

/** The reports entry RATING_NAV declares; re-targeted per scope below. */
const RATING_REPORTS_PATH = "/rating/reports";

/**
 * The landing page, above every other module. Where login lands, and where the
 * assurance scope gets chosen before the rest of the workflow.
 */
const OVERVIEW_ITEM: RatingNavItem = {
  to: "/overview",
  label: "Overview",
  icon: LayoutGrid,
  phase: 1,
};

/**
 * The cross-assurance dashboard. Sits above the scoped modules because it is
 * scope-INDEPENDENT: it covers every assurance at once, where everything below
 * it re-targets with the header switcher. Injected outside the scope filter, so
 * it is present under all eight scopes.
 */
const ENTERPRISE_DASHBOARD_ITEM: RatingNavItem = {
  to: "/dashboard",
  label: "Enterprise Dashboard",
  icon: LayoutDashboard,
  phase: 1,
};

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const t = useT();
  const { scope, app } = useAssuranceScope();

  // Operations is rebuilt rather than taken from RATING_NAV as-is. Its declared
  // children (Replay & Recovery, Tolerance Policies, Audit Logs) are all
  // unbuilt phase-2+ placeholders; they are replaced by the two screens that do
  // exist — the assurance Controls explorer for the selected app, and the
  // platform's Data Sources register.
  const operations: RatingNavItem = useMemo(
    () => ({
      to: OPERATIONS_PATH,
      label: "Operations",
      icon: Settings2,
      phase: 1,
      children: [
        {
          to: `/assurance/${app.id}/controls`,
          label: "Controls",
          icon: ShieldCheck,
          phase: 1,
        },
        { to: "/data-sources", label: "Data Sources", icon: Database, phase: 1 },
        // Where rules raise cases. Without an entry here the Raise case action
        // on a control would open a case on a screen nobody can navigate to.
        { to: "/cases", label: "Assurance Cases", icon: Briefcase, phase: 1 },
      ],
    }),
    [app.id],
  );

  // The executive dashboard for the selected app. Scope-targeted like Controls,
  // so it is injected here rather than declared in RATING_NAV — and it sits
  // directly under Overview, where choosing an assurance lands.
  const assuranceDashboard: RatingNavItem = useMemo(
    () => ({
      to: `/assurance/${app.id}/dashboard`,
      label: "Assurance Dashboard",
      icon: LayoutDashboard,
      phase: 1,
    }),
    [app.id],
  );

  // Reports are two different suites. The rating catalog (Daily Reconciliation
  // Summary, Product Leakage, Undercharge/Overcharge, Tax Reconciliation) is
  // rating-service specific and says nothing about Usage, Billing or Partner;
  // those scopes get the platform catalog (/reports) instead. The nav entry
  // keeps its label and position either way — only its target moves.
  const reports = catalogForScope(scope);

  const navItems = useMemo(() => {
    const base =
      scope === RATING_SCOPE_ID
        ? RATING_NAV
        : RATING_NAV.filter((item) => !RATING_ONLY_PATHS.has(item.to));
    return [
      OVERVIEW_ITEM,
      // Cross-assurance first, then the selected assurance's own dashboard —
      // widest scope to narrowest, matching how the rest of the list narrows.
      ENTERPRISE_DASHBOARD_ITEM,
      assuranceDashboard,
      ...base.map((item) => {
        if (item.to === OPERATIONS_PATH) return operations;
        if (item.to === RATING_REPORTS_PATH) return { ...item, to: reports.path };
        return item;
      }),
    ];
  }, [scope, operations, assuranceDashboard, reports.path]);

  // RATING_AVAILABLE_PATHS is derived from RATING_NAV, so anything injected
  // here is absent from it and would render as a disabled "soon" row.
  const availablePaths = useMemo(
    () =>
      new Set([
        ...RATING_AVAILABLE_PATHS,
        OVERVIEW_ITEM.to,
        ENTERPRISE_DASHBOARD_ITEM.to,
        assuranceDashboard.to,
        reports.path,
        ...(operations.children ?? []).map((c) => c.to),
      ]),
    [operations, assuranceDashboard, reports.path],
  );

  return (
    <aside
      className={`hidden md:flex shrink-0 flex-col bg-sidebar text-sidebar-foreground border-r border-sidebar-border transition-all duration-300 ease-in-out sticky top-0 h-screen self-start z-40 ${
        collapsed ? "w-[72px]" : "w-72"
      }`}
    >
      <div className="px-4 py-5 border-b border-sidebar-border flex items-center gap-3 relative">
        <div className="h-12 w-12 shrink-0 rounded-xl bg-primary flex items-center justify-center shadow-md">
          <span className="font-extrabold text-lg tracking-tight text-primary-foreground" aria-label="RADONaix">
            RA
          </span>
        </div>
        {!collapsed && (
          <div className="min-w-0 transition-opacity">
            <div className="font-semibold tracking-tight text-base leading-none truncate">RADONaix</div>
            <div className="text-xs text-sidebar-foreground/60 mt-1 truncate">{t("Revenue Assurance")}</div>
          </div>
        )}
        <Tooltip
          label={collapsed ? t("Expand sidebar") : t("Collapse sidebar")}
          side="right"
          className="!absolute -right-3 top-1/2 -translate-y-1/2 z-50"
        >
          <button
            onClick={onToggle}
            className="h-6 w-6 rounded-full border border-sidebar-border bg-card shadow-sm hover:bg-muted flex items-center justify-center text-muted-foreground hover:text-foreground transition"
            aria-label={collapsed ? t("Expand sidebar") : t("Collapse sidebar")}
          >
            {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </Tooltip>
      </div>

      {/* Collapsed: keep overflow visible so the Reports hover flyout (and the
          per-item tooltips) can escape the narrow rail. Expanded: scroll the
          catalog. */}
      <nav className={`flex-1 px-2 py-4 space-y-1 ${collapsed ? "overflow-visible" : "overflow-y-auto"}`}>
        {!collapsed && (
          <div className="px-3 pb-2 text-[10px] tracking-widest text-sidebar-foreground/40 font-semibold">{t("MODULES")}</div>
        )}
        <RatingSidebarNav collapsed={collapsed} items={navItems} availablePaths={availablePaths} reports={reports} />
        <AssuranceSidebarNav collapsed={collapsed} />
      </nav>

      <div className={`px-4 py-4 border-t border-sidebar-border text-[11px] text-sidebar-foreground/50 ${collapsed ? "text-center" : ""}`}>
        {collapsed ? "v2.4" : "v2.4.1 · Production"}
      </div>
    </aside>
  );
}
