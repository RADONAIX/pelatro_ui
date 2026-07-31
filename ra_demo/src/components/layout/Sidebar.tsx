import {
  ChevronLeft,
  ChevronRight,
  Database,
  // Aliased: `Home` collides with nothing here today, but the alias keeps the
  // icon distinguishable from the nav item it belongs to.
  Home as HomeIcon,
  LayoutDashboard,
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
//   RatingSidebarNav      — Reports, Pipelines, Rule Management (+children),
//                           Metadata Catalogue, Case Management, System
//                           Monitoring.  (src/lib/rating/nav.ts)
//   AssuranceSidebarNav   — the entity scope for the app currently selected in
//                           the header.
//
// Plus five injected: Home, the cross-assurance Enterprise Dashboard and the
// selected assurance's Assurance Dashboard above the list; Controls and Data
// Sources in the middle of it. There is deliberately no "Dashboard & KPIs" and
// no "Operations" group — see the notes in rating/nav.ts.
//
// Almost nothing moves when the Assurance Scope changes: it re-targets the
// Assurance Dashboard, Reports and Controls, and hides the two rating-specific
// modules. Every other module is present under every scope.
// ---------------------------------------------------------------------------

/**
 * Modules that only exist for the rating domain. Rule authoring and the
 * metadata catalogue are backed by ra_rating_backend and have no equivalent
 * for Usage, Billing, Partner and the rest, so listing them under those scopes
 * would offer rating screens for an app they say nothing about.
 */
const RATING_ONLY_PATHS = new Set(["/rating/rules", "/rating/catalog"]);

const RATING_SCOPE_ID = "rating";

/**
 * Controls and Data Sources are injected immediately before Case Management —
 * the slot the removed Operations group used to occupy, so nothing moved.
 */
const CASES_PATH = "/cases";

/** The reports entry RATING_NAV declares; re-targeted per scope below. */
const RATING_REPORTS_PATH = "/rating/reports";

/**
 * The landing page, above every other module. Where login lands, and where the
 * assurance scope gets chosen before the rest of the workflow.
 */
const OVERVIEW_ITEM: RatingNavItem = {
  to: "/overview",
  label: "Home",
  icon: HomeIcon,
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

/**
 * The feed register. Common to every assurance — the same sources back all of
 * them — so it sits at the top level rather than inside the assurance-scoped
 * Operations group.
 */
const DATA_SOURCES_ITEM: RatingNavItem = {
  to: "/data-sources",
  label: "Data Sources",
  icon: Database,
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

  // The rule explorer for the selected app. Top level rather than inside an
  // Operations group: that group's declared children were all unbuilt
  // placeholders, and once Data Sources moved out it wrapped this one item.
  const controls: RatingNavItem = useMemo(
    () => ({
      // Falls back to a placeholder path while nothing is selected. It is never
      // linkable in that state — availablePaths below omits it, so the row
      // renders disabled rather than pointing at /assurance/null.
      to: app ? `/assurance/${app.id}/controls` : "/assurance/controls",
      label: "Controls",
      icon: ShieldCheck,
      phase: 1,
    }),
    [app],
  );

  // The executive dashboard for the selected app. Scope-targeted like Controls,
  // so it is injected here rather than declared in RATING_NAV — and it sits
  // directly under Home, where choosing an assurance lands.
  const assuranceDashboard: RatingNavItem = useMemo(
    () => ({
      // Same placeholder rule as Controls above — disabled, not a dead link.
      to: app ? `/assurance/${app.id}/dashboard` : "/assurance/dashboard",
      label: "Assurance Dashboard",
      icon: LayoutDashboard,
      phase: 1,
    }),
    [app],
  );

  // Reports are two different suites. The rating catalog (Daily Reconciliation
  // Summary, Product Leakage, Undercharge/Overcharge, Tax Reconciliation) is
  // rating-service specific and says nothing about Usage, Billing or Partner;
  // those scopes get the platform catalog (/reports) instead. The nav entry
  // keeps its label and position either way — only its target moves.
  // No assurance selected yet resolves to the platform suite, which is what
  // every non-rating scope gets anyway.
  const reports = catalogForScope(scope ?? "");

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
      ...base.flatMap((item) => {
        // Both land in the slot Operations held, immediately above the queue
        // their findings go to.
        if (item.to === CASES_PATH) return [controls, DATA_SOURCES_ITEM, item];
        if (item.to === RATING_REPORTS_PATH) return [{ ...item, to: reports.path }];
        return [item];
      }),
    ];
  }, [scope, controls, assuranceDashboard, reports.path]);

  /**
   * Modules whose presence or target follows the selected assurance. Everything
   * not in here is common: the same screen under all eight.
   *
   * Presence: Rule Management and Metadata Catalogue exist only under Rating.
   * Target: the Assurance Dashboard, Reports and Controls all re-point at the
   * selected app.
   *
   * Pipelines & Job Monitor is listed as assurance-specific because that is the
   * intent, but note it does not re-target yet: /pipelines is the same
   * platform-wide monitor under every scope.
   */
  const scopeSpecificPaths = useMemo(
    () =>
      new Set([
        assuranceDashboard.to,
        reports.path,
        "/pipelines",
        controls.to,
        ...RATING_ONLY_PATHS,
      ]),
    [assuranceDashboard.to, reports.path, controls.to],
  );

  // RATING_AVAILABLE_PATHS is derived from RATING_NAV, so anything injected
  // here is absent from it and would render as a disabled "soon" row.
  //
  // The app-scoped entries are added only once an assurance is chosen: with
  // none selected their paths are placeholders pointing at no route, so leaving
  // them out is what makes those rows render disabled instead of dead links.
  const availablePaths = useMemo(
    () =>
      new Set([
        ...RATING_AVAILABLE_PATHS,
        OVERVIEW_ITEM.to,
        ENTERPRISE_DASHBOARD_ITEM.to,
        DATA_SOURCES_ITEM.to,
        reports.path,
        ...(app ? [assuranceDashboard.to, controls.to] : []),
      ]),
    [app, controls.to, assuranceDashboard, reports.path],
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
        <RatingSidebarNav
          collapsed={collapsed}
          items={navItems}
          availablePaths={availablePaths}
          reports={reports}
          scopeSpecificPaths={scopeSpecificPaths}
        />
        <AssuranceSidebarNav collapsed={collapsed} />
      </nav>

      {/* Legend. The tint on its own says nothing to someone who hasn't been
          told what it means — and nothing at all to a colour-blind reader — so
          the two classes are named, with the current assurance spelled out. */}
      {!collapsed && (
        <div className="px-4 pt-3 pb-1 space-y-1.5 border-t border-sidebar-border">
          <div className="flex items-center gap-2 text-[11px] text-sidebar-foreground/60">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" />
            {/* With nothing selected these rows are disabled, so the legend
                says why rather than naming an assurance that isn't chosen. */}
            <span className="truncate">
              {app ? `${t("Specific to")} ${app.name}` : t("Select an assurance to enable")}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-sidebar-foreground/60">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-sidebar-foreground/40" />
            <span className="truncate">{t("Common to all assurances")}</span>
          </div>
        </div>
      )}

      <div className={`px-4 py-4 border-t border-sidebar-border text-[11px] text-sidebar-foreground/50 ${collapsed ? "text-center" : ""}`}>
        {collapsed ? "v2.4" : "v2.4.1 · Production"}
      </div>
    </aside>
  );
}
