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
import { useReportCatalog } from "@/lib/reportCatalogs";

// ---------------------------------------------------------------------------
// Two groups, in the order the product is used:
//
//   PLATFORM              — every module that means the same thing regardless of
//                           which assurance is selected: Home, the
//                           cross-assurance Enterprise Dashboard, Case
//                           Management, Pipelines & Job Monitor, System
//                           Monitoring, Data Sources.
//   ASSURANCE APPLICATION — the modules that ARE the selected assurance:
//                           Dashboard, Controls, Reports, and under Rating the
//                           two rating-service modules.
//
// The split is the information architecture, not decoration: everything under
// the second heading re-targets when the Assurance Scope changes in the header,
// and renders disabled while nothing is selected. Nothing under the first one
// moves. That is why the group a module belongs to is decided by whether its
// path carries the app id, not by taste.
//
// AssuranceSidebarNav renders the entity-scope reference block below both.
// There is deliberately no "Dashboard & KPIs" and no "Operations" group — see
// the notes in rating/nav.ts.
// ---------------------------------------------------------------------------

/**
 * Modules that only exist for the rating domain. Rule authoring and the
 * metadata catalogue are backed by ra_rating_backend and have no equivalent
 * for Usage, Billing, Partner and the rest, so listing them under those scopes
 * would offer rating screens for an app they say nothing about.
 */
const RATING_ONLY_PATHS = new Set([
  "/rating/rules",
  "/rating/catalog",
  "/rating/execution",
]);

const RATING_SCOPE_ID = "rating";

/** The reports entry RATING_NAV declares; re-targeted per scope below. */
const RATING_REPORTS_PATH = "/rating/reports";

/**
 * The RATING_NAV entries that belong to the platform group, in the order they
 * are listed. Taken from RATING_NAV rather than redeclared so each keeps its
 * icon and label; anything not named here is assurance-specific.
 *
 * Pipelines & Job Monitor is here because /pipelines is the same platform-wide
 * batch monitor under every scope — it was previously tinted as
 * assurance-specific on the strength of intent rather than behaviour.
 */
const PLATFORM_NAV_PATHS = ["/cases", "/pipelines", "/rating/monitoring"] as const;

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
  // so it is injected here rather than declared in RATING_NAV. Labelled just
  // "Dashboard": it opens the assurance group, and the heading above it already
  // says which assurance — "Assurance Dashboard" repeated the word.
  const assuranceDashboard: RatingNavItem = useMemo(
    () => ({
      // Same placeholder rule as Controls above — disabled, not a dead link.
      to: app ? `/assurance/${app.id}/dashboard` : "/assurance/dashboard",
      label: "Dashboard",
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
  // every non-rating scope gets anyway. Generated reconciliation reports are
  // merged in on top, so a rule authored in Controls shows up in this menu
  // without anything else being touched — and with no scope selected there is
  // no assurance to scope them to, so only the static suite is listed.
  const reports = useReportCatalog(scope ?? "");

  /** Common to all eight assurances. Nothing here moves with the scope. */
  const platformItems = useMemo(
    () => [
      OVERVIEW_ITEM,
      ENTERPRISE_DASHBOARD_ITEM,
      ...PLATFORM_NAV_PATHS.map((path) => RATING_NAV.find((item) => item.to === path)).filter(
        (item): item is RatingNavItem => !!item,
      ),
      DATA_SOURCES_ITEM,
    ],
    [],
  );

  /**
   * The selected assurance's own modules, widest first: its dashboard, the
   * controls that produce its findings, the reports they feed.
   *
   * Rule Management and the Metadata Catalogue are assurance-specific too, but
   * exist only for Rating — they are backed by ra_rating_backend and have no
   * equivalent elsewhere, so under any other scope the group is the first three.
   */
  const assuranceItems = useMemo(() => {
    const reportsEntry = RATING_NAV.find((item) => item.to === RATING_REPORTS_PATH);
    const ratingOnly =
      scope === RATING_SCOPE_ID
        ? RATING_NAV.filter((item) => RATING_ONLY_PATHS.has(item.to))
        : [];
    return [
      assuranceDashboard,
      controls,
      ...(reportsEntry ? [{ ...reportsEntry, to: reports.path }] : []),
      ...ratingOnly,
    ];
  }, [scope, controls, assuranceDashboard, reports.path]);

  /**
   * Every module in the assurance group is scope-specific by construction, so
   * this is just its paths. It still matters to the renderer: a row in here that
   * has no target yet renders "pick one" rather than "soon", because the screen
   * exists and is only waiting on an assurance being chosen.
   */
  const scopeSpecificPaths = useMemo(
    () => new Set(assuranceItems.map((item) => item.to)),
    [assuranceItems],
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
            <div className="text-xs text-sidebar-foreground/60 mt-1 truncate">{t("Enterprise Assurance")}</div>
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
          <div className="px-3 pb-2 text-[10px] tracking-widest text-sidebar-foreground/40 font-semibold">
            {t("PLATFORM")}
          </div>
        )}
        <RatingSidebarNav
          collapsed={collapsed}
          items={platformItems}
          availablePaths={availablePaths}
          reports={reports}
        />

        {/* The heading names the assurance rather than repeating the word,
            because that is the question this group answers: everything below is
            THIS assurance. Collapsed, the rule alone separates the two. */}
        <div className={collapsed ? "mt-3 pt-3 border-t border-sidebar-border" : "mt-4 pt-3 border-t border-sidebar-border"}>
          {!collapsed && (
            <div className="px-3 pb-2">
              <div className="text-[10px] tracking-widest text-sidebar-foreground/40 font-semibold">
                {t("ASSURANCE APPLICATION")}
              </div>
              <div className="mt-1 text-[13px] font-medium text-sidebar-foreground/80 truncate">
                {app ? app.name : t("Select an assurance")}
              </div>
            </div>
          )}
          <RatingSidebarNav
            collapsed={collapsed}
            items={assuranceItems}
            availablePaths={availablePaths}
            reports={reports}
            scopeSpecificPaths={scopeSpecificPaths}
          />
        </div>

        <AssuranceSidebarNav collapsed={collapsed} />
      </nav>

      {/* No tint legend any more: the two headings say which modules follow the
          assurance and which don't, in words, which is what the legend existed
          to explain. */}
    </aside>
  );
}
