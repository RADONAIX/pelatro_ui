import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  FileBarChart2,
  Activity,
  Gauge,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ListOrdered,
  FileStack,
  FileWarning,
  ClipboardList,
  Scale,
  GitCompare,
  ScrollText,
  FileText,
  Cpu,
  Database,
  Server,
  ServerCog,
  // Wrench,  — restore with the Assurance Workbench nav entry below
  Briefcase,
  GitCompareArrows,
  SlidersHorizontal,
  Layers,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth, type PermKey } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { GROUPS, REPORTS, DEFAULT_REPORT_KEY } from "@/lib/reportsCatalog";
import { useAssuranceScope } from "@/lib/assuranceScope";
import { RatingSidebarNav } from "@/components/layout/RatingSidebarNav";
import { APPS } from "@/lib/assurance/platform-metadata";

// A distinct icon per report (falls back to a per-group icon, then a default).
const REPORT_ICON: Record<string, typeof LayoutDashboard> = {
  record_sequence_check: ListOrdered,
  file_sequence_check: FileStack,
  file_exception: FileWarning,
  file_summary: ClipboardList,
  air_reconciliation: Scale,
  sdp_reconciliation: Scale,
  msc_reconciliation: Scale,
  report_batch_log: ScrollText,
};
const GROUP_ICON: Record<string, typeof LayoutDashboard> = {
  Reconciliation: GitCompare,
  Operations: ScrollText,
};
const reportIcon = (key: string, group: string): typeof LayoutDashboard =>
  REPORT_ICON[key] ?? GROUP_ICON[group] ?? FileText;

// System Monitoring sub-sections (navigated via /monitoring?view=<key>).
const MONITORING_CHILDREN: { key: string; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "applications", label: "Applications", icon: Cpu },
  { key: "databases", label: "Databases", icon: Database },
  { key: "reportservers", label: "Report Servers", icon: Server },
  { key: "serverops", label: "Server Operations", icon: ServerCog },
];

interface NavChild {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
}

// Operations groups the day-to-day setup and investigation surfaces for the
// assurance domain: where data comes from and the rules that reconcile it.
const OPERATIONS_CHILDREN: NavChild[] = [
  { to: "/data-sources", label: "Data Sources", icon: Database },
  { to: "/recon-workflows", label: "Recon Workflows", icon: GitCompareArrows },
  // Hidden from the nav for now — the route at src/routes/workbench.tsx is
  // untouched, so it still resolves by direct URL and this line restores it.
  // { to: "/workbench", label: "Assurance Workbench", icon: Wrench },
];

interface NavItem {
  // For a group, this is the landing route used by the collapsed rail.
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  badge: number | null;
  // Omitted for the demo-only modules, which carry no backend permission key —
  // they are always visible to any signed-in user.
  perm?: PermKey;
  // Route-based sub-items. System Monitoring deliberately stays top-level: it
  // already has its own `?view=` children, so nesting it here would make a third
  // level, and it reports platform health rather than assurance-domain state.
  children?: NavChild[];
}

const items: NavItem[] = [
  { to: "/", label: "Dashboard & KPIs", icon: LayoutDashboard, badge: null, perm: "dashboard" },
  { to: "/reports", label: "Reports & Certified Exports", icon: FileBarChart2, badge: REPORTS.length, perm: "reports" },
  { to: "/pipelines", label: "Pipelines & Job Monitor", icon: Activity, badge: null, perm: "pipelines" },
  { to: "/cases", label: "Case Management", icon: Briefcase, badge: 3 },
  { to: "/data-sources", label: "Operations", icon: SlidersHorizontal, badge: null, children: OPERATIONS_CHILDREN },
  { to: "/monitoring", label: "System Monitoring", icon: Gauge, badge: null, perm: "settings" },
];

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const activeReport = useRouterState({
    select: (s) => (s.location.search as { report?: string } | undefined)?.report,
  });
  const { permissions } = useAuth();
  const t = useT();
  const visible = items.filter((i) => !i.perm || permissions[i.perm]?.view);
  // The Rating Assurance scope owns the module list while it is selected; every
  // other scope falls through to the existing render below, unchanged.
  const { isRating } = useAssuranceScope();

  const activeView = useRouterState({
    select: (s) => (s.location.search as { view?: string } | undefined)?.view,
  });

  const onReports = pathname.startsWith("/reports");
  const [reportsOpen, setReportsOpen] = useState(onReports);
  // Auto-expand the catalog whenever we navigate onto the Reports screen.
  useEffect(() => {
    if (onReports) setReportsOpen(true);
  }, [onReports]);

  const onOperations = OPERATIONS_CHILDREN.some((c) => pathname.startsWith(c.to));
  const [operationsOpen, setOperationsOpen] = useState(onOperations);
  // Auto-expand Operations whenever we navigate onto one of its screens.
  useEffect(() => {
    if (onOperations) setOperationsOpen(true);
  }, [onOperations]);

  const onMonitoring = pathname.startsWith("/monitoring");
  const [monitoringOpen, setMonitoringOpen] = useState(onMonitoring);
  // Auto-expand the monitoring sub-menu whenever we're on the monitoring screen.
  useEffect(() => {
    if (onMonitoring) setMonitoringOpen(true);
  }, [onMonitoring]);

  const onAssurance = pathname.startsWith("/assurance/");
  const [assuranceOpen, setAssuranceOpen] = useState(onAssurance);
  // Auto-expand the assurance app list whenever we're inside one of its apps.
  useEffect(() => {
    if (onAssurance) setAssuranceOpen(true);
  }, [onAssurance]);

  const selectedReport = onReports
    ? REPORTS.some((r) => r.key === activeReport && r.available)
      ? activeReport
      : DEFAULT_REPORT_KEY
    : null;

  // The grouped report catalog — shared by the expanded accordion and the
  // collapsed hover flyout so both stay in sync.
  const renderReportGroups = () =>
    GROUPS.map((g) => {
      const groupReports = REPORTS.filter((r) => r.group === g);
      if (groupReports.length === 0) return null;
      return (
        <div key={g}>
          <div className="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wide text-sidebar-foreground/40">{t(g)}</div>
          {groupReports.map((r) => {
            const childActive = selectedReport === r.key;
            const RIcon = reportIcon(r.key, r.group);
            if (!r.available) {
              return (
                <div
                  key={r.key}
                  title={t("Not available yet")}
                  aria-disabled="true"
                  className="flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] text-sidebar-foreground/35 cursor-not-allowed select-none"
                >
                  <RIcon className="h-3.5 w-3.5 shrink-0" />
                  <span className="flex-1 truncate">{t(r.title)}</span>
                  <span className="text-[9px] uppercase tracking-wide">{t("soon")}</span>
                </div>
              );
            }
            return (
              <Link
                key={r.key}
                to="/reports"
                search={{ report: r.key }}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
                  childActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <RIcon className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`} />
                <span className="flex-1 truncate">{t(r.title)}</span>
              </Link>
            );
          })}
        </div>
      );
    });

  // The eight Enterprise Assurance apps — shared by the expanded accordion and
  // the collapsed hover flyout so both stay in sync. Each app lands on its own
  // dashboard; the six sections are switched by tabs inside the page, not here.
  const renderAssuranceApps = () =>
    APPS.map((a) => {
      const appActive = pathname.startsWith(`/assurance/${a.id}/`);
      return (
        <Link
          key={a.id}
          to="/assurance/$appId/$section"
          params={{ appId: a.id, section: "dashboard" }}
          className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
            appActive
              ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
              : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
          }`}
        >
          <span
            className={`shrink-0 font-mono text-[9px] tracking-wider ${appActive ? "text-primary" : "text-sidebar-foreground/45"}`}
          >
            {a.prefix}
          </span>
          <span className="flex-1 truncate">{a.name}</span>
        </Link>
      );
    });

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
        {isRating && <RatingSidebarNav collapsed={collapsed} />}
        {!isRating && visible.map((item) => {
          const Icon = item.icon;
          // A group is active whenever any of its children is — its own `to` is
          // only a landing route, not a page of its own.
          const active = item.children
            ? item.children.some((c) => pathname.startsWith(c.to))
            : pathname === item.to || (item.to !== "/" && pathname.startsWith(item.to));

          // Groups with plain route children (Operations) — an expandable parent
          // when open, a hover flyout when the rail is collapsed.
          if (item.children) {
            const children = item.children;
            if (!collapsed) {
              return (
                <div key={item.to}>
                  <button
                    onClick={() => setOperationsOpen((o) => !o)}
                    aria-expanded={operationsOpen}
                    className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                      active
                        ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                    }`}
                  >
                    <span className="flex items-center gap-3 min-w-0">
                      <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                      <span className="truncate">{t(item.label)}</span>
                    </span>
                    <ChevronDown
                      className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${operationsOpen ? "rotate-180" : ""}`}
                    />
                  </button>

                  {operationsOpen && (
                    <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                      {children.map((c) => {
                        const CIcon = c.icon;
                        const childActive = pathname.startsWith(c.to);
                        return (
                          <Link
                            key={c.to}
                            to={c.to}
                            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
                              childActive
                                ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                                : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                            }`}
                          >
                            <CIcon className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`} />
                            <span className="flex-1 truncate">{t(c.label)}</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            }

            // Collapsed rail: same hover-flyout treatment as Reports, so the
            // children stay reachable without expanding the sidebar.
            return (
              <div key={item.to} className="group relative">
                <Link
                  to={children[0].to}
                  aria-label={t(item.label)}
                  className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                  }`}
                >
                  <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                </Link>

                <div className="invisible -translate-x-1 opacity-0 group-hover:visible group-hover:translate-x-0 group-hover:opacity-100 transition duration-150 ease-out absolute left-full top-0 pl-2.5 z-50">
                  <span className="absolute left-[6px] top-4 z-10 h-2.5 w-2.5 rotate-45 rounded-[2px] border-l border-b border-sidebar-border bg-sidebar" />
                  <div className="relative w-60 rounded-xl border border-sidebar-border bg-sidebar shadow-2xl ring-1 ring-black/5 py-2">
                    <div className="flex items-center gap-2.5 px-3 pb-2.5 mb-1 border-b border-sidebar-border">
                      <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                        <Icon className="h-4 w-4" />
                      </span>
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-sidebar-foreground leading-tight truncate">{t(item.label)}</div>
                      </div>
                    </div>
                    <div className="px-1.5 space-y-0.5">
                      {children.map((c) => {
                        const CIcon = c.icon;
                        const childActive = pathname.startsWith(c.to);
                        return (
                          <Link
                            key={c.to}
                            to={c.to}
                            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
                              childActive
                                ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                                : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                            }`}
                          >
                            <CIcon className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`} />
                            <span className="flex-1 truncate">{t(c.label)}</span>
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          }

          // Reports is special: an expandable catalog (only when the sidebar is
          // expanded; collapsed falls back to a plain link to the default report).
          const isReports = item.to === "/reports";

          if (isReports && !collapsed) {
            return (
              <div key={item.to}>
                <button
                  onClick={() => setReportsOpen((o) => !o)}
                  aria-expanded={reportsOpen}
                  className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                  }`}
                >
                  <span className="flex items-center gap-3 min-w-0">
                    <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                    <span className="truncate">{t(item.label)}</span>
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${reportsOpen ? "rotate-180" : ""}`}
                  />
                </button>

                {reportsOpen && (
                  <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                    {renderReportGroups()}
                  </div>
                )}
              </div>
            );
          }

          // Reports collapsed: a plain link to the default report, plus a hover
          // flyout listing the whole catalog so children stay reachable.
          if (isReports && collapsed) {
            return (
              <div key={item.to} className="group relative">
                <Link
                  to="/reports"
                  aria-label={t(item.label)}
                  className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                  }`}
                >
                  <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                  {item.badge !== null && (
                    <span className="absolute top-1 right-1 text-[9px] font-medium px-1 rounded bg-primary text-primary-foreground">
                      {item.badge}
                    </span>
                  )}
                </Link>

                {/* pl-2 is a hoverable bridge so the cursor can cross the gap
                    from the rail to the panel without the flyout closing. */}
                <div className="invisible -translate-x-1 opacity-0 group-hover:visible group-hover:translate-x-0 group-hover:opacity-100 transition duration-150 ease-out absolute left-full top-0 pl-2.5 z-50">
                  {/* Caret lives on the (non-clipping) wrapper — the card below
                      has overflow-y-auto which would clip an arrow poking out. */}
                  <span className="absolute left-[6px] top-4 z-10 h-2.5 w-2.5 rotate-45 rounded-[2px] border-l border-b border-sidebar-border bg-sidebar" />
                  <div className="relative w-64 max-h-[78vh] overflow-y-auto rounded-xl border border-sidebar-border bg-sidebar shadow-2xl ring-1 ring-black/5 py-2">
                    <div className="flex items-center gap-2.5 px-3 pb-2.5 mb-1 border-b border-sidebar-border">
                      <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                        <Icon className="h-4 w-4" />
                      </span>
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-sidebar-foreground leading-tight truncate">{t(item.label)}</div>
                        <div className="text-[10px] text-sidebar-foreground/50 mt-0.5">
                          {REPORTS.filter((r) => r.available).length} {t("reports")}
                        </div>
                      </div>
                    </div>
                    <div className="px-1.5 space-y-0.5">
                      {renderReportGroups()}
                    </div>
                  </div>
                </div>
              </div>
            );
          }

          // System Monitoring is an expandable parent (Applications / Databases /
          // Report Servers) when the sidebar is open; collapsed → plain link.
          const isMonitoring = item.to === "/monitoring";
          if (isMonitoring && !collapsed) {
            return (
              <div key={item.to}>
                <button
                  onClick={() => setMonitoringOpen((o) => !o)}
                  aria-expanded={monitoringOpen}
                  className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                  }`}
                >
                  <span className="flex items-center gap-3 min-w-0">
                    <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                    <span className="truncate">{t(item.label)}</span>
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${monitoringOpen ? "rotate-180" : ""}`}
                  />
                </button>

                {monitoringOpen && (
                  <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                    {MONITORING_CHILDREN.map((c) => {
                      const CIcon = c.icon;
                      const childActive = onMonitoring && (activeView ?? MONITORING_CHILDREN[0].key) === c.key;
                      return (
                        <Link
                          key={c.key}
                          to="/monitoring"
                          search={{ view: c.key }}
                          className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
                            childActive
                              ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                              : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                          }`}
                        >
                          <CIcon className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`} />
                          <span className="flex-1 truncate">{t(c.label)}</span>
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          }

          return (
            <Link
              key={item.to}
              to={item.to}
              title={collapsed ? t(item.label) : undefined}
              className={`group relative flex items-center ${collapsed ? "justify-center" : "justify-between"} gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                  : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
              }`}
            >
              <span className={`flex items-center ${collapsed ? "" : "gap-3"} min-w-0`}>
                <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
                {!collapsed && <span className="truncate">{t(item.label)}</span>}
              </span>
              {item.badge !== null && !collapsed && (
                <span
                  className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md min-w-5 text-center ${
                    active ? "bg-primary text-primary-foreground" : "bg-sidebar-accent text-sidebar-foreground/80"
                  }`}
                >
                  {item.badge}
                </span>
              )}
              {item.badge !== null && collapsed && (
                <span className="absolute top-1 right-1 text-[9px] font-medium px-1 rounded bg-primary text-primary-foreground">
                  {item.badge}
                </span>
              )}
              {collapsed && (
                <span className="pointer-events-none absolute left-full ml-2 whitespace-nowrap rounded-md bg-foreground text-background text-xs px-2 py-1 opacity-0 group-hover:opacity-100 transition shadow-lg z-50">
                  {t(item.label)}
                </span>
              )}
            </Link>
          );
        })}

        {/* Enterprise Assurance — the ported ASSURA apps, listed below the
            RADONaix modules. Rendered outside the `isRating` branch above so
            they stay reachable in every Assurance Scope; wrap this block in
            `{!isRating && ...}` to hide it while the Rating scope owns the nav. */}
        <div className="pt-3 mt-3 border-t border-sidebar-border">
          {!collapsed && (
            <div className="px-3 pb-2 text-[10px] tracking-widest text-sidebar-foreground/40 font-semibold">
              {t("ENTERPRISE ASSURANCE")}
            </div>
          )}

          {!collapsed ? (
            <>
              <button
                onClick={() => setAssuranceOpen((o) => !o)}
                aria-expanded={assuranceOpen}
                className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  onAssurance
                    ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <span className="flex items-center gap-3 min-w-0">
                  <Layers className={`h-4 w-4 shrink-0 ${onAssurance ? "text-primary" : ""}`} />
                  <span className="truncate">{t("Assurance Apps")}</span>
                </span>
                <ChevronDown
                  className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${assuranceOpen ? "rotate-180" : ""}`}
                />
              </button>

              {assuranceOpen && (
                <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                  {renderAssuranceApps()}
                </div>
              )}
            </>
          ) : (
            // Collapsed rail: same hover-flyout treatment as Operations/Reports.
            <div className="group relative">
              <Link
                to="/assurance/$appId/$section"
                params={{ appId: APPS[0].id, section: "dashboard" }}
                aria-label={t("Assurance Apps")}
                className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  onAssurance
                    ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <Layers className={`h-4 w-4 shrink-0 ${onAssurance ? "text-primary" : ""}`} />
              </Link>

              <div className="invisible -translate-x-1 opacity-0 group-hover:visible group-hover:translate-x-0 group-hover:opacity-100 transition duration-150 ease-out absolute left-full top-0 pl-2.5 z-50">
                <span className="absolute left-[6px] top-4 z-10 h-2.5 w-2.5 rotate-45 rounded-[2px] border-l border-b border-sidebar-border bg-sidebar" />
                <div className="relative w-64 rounded-xl border border-sidebar-border bg-sidebar shadow-2xl ring-1 ring-black/5 py-2">
                  <div className="flex items-center gap-2.5 px-3 pb-2.5 mb-1 border-b border-sidebar-border">
                    <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                      <Layers className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-sidebar-foreground leading-tight truncate">
                        {t("Assurance Apps")}
                      </div>
                      <div className="text-[10px] text-sidebar-foreground/50 mt-0.5">
                        {APPS.length} {t("apps")}
                      </div>
                    </div>
                  </div>
                  <div className="px-1.5 space-y-0.5">{renderAssuranceApps()}</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </nav>

      <div className={`px-4 py-4 border-t border-sidebar-border text-[11px] text-sidebar-foreground/50 ${collapsed ? "text-center" : ""}`}>
        {collapsed ? "v2.4" : "v2.4.1 · Production"}
      </div>
    </aside>
  );
}
