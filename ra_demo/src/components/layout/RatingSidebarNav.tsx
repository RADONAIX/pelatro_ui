import { Link, useRouterState } from "@tanstack/react-router";
import {
  ChevronDown,
  FileBarChart2,
  FileText,
  Landmark,
  ListChecks,
  Scale,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n";
import {
  RATING_REPORTS,
  RATING_REPORT_GROUPS,
  DEFAULT_RATING_REPORT_KEY,
} from "@/lib/rating/reportsCatalog";
import {
  RATING_NAV,
  RATING_AVAILABLE_PATHS,
  EXACT_MATCH_PATHS,
  type RatingNavChild,
  type RatingNavItem,
} from "@/lib/rating/nav";

// The Rating Assurance module's navigation. Rendered by the Sidebar in place of
// the default module list while the Assurance Scope is "Rating Assurance"; every
// class string here is copied verbatim from the existing Sidebar so the two look
// identical and no theme token changes.
//
// Entries whose route doesn't exist yet render exactly like the existing "SOON"
// report rows — visible but disabled — so the module's full shape is legible
// from day one without anyone clicking into a 404.

function SoonRow({
  label,
  icon: Icon,
  nested,
}: {
  label: string;
  icon: RatingNavChild["icon"];
  nested?: boolean;
}) {
  const t = useT();
  return (
    <div
      title={t("Not available yet")}
      aria-disabled="true"
      className={
        nested
          ? "flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] text-sidebar-foreground/35 cursor-not-allowed select-none"
          : "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-sidebar-foreground/35 cursor-not-allowed select-none"
      }
    >
      <Icon className={nested ? "h-3.5 w-3.5 shrink-0" : "h-4 w-4 shrink-0"} />
      <span className="flex-1 truncate">{t(label)}</span>
      <span className="text-[9px] uppercase tracking-wide">{t("soon")}</span>
    </div>
  );
}

// A distinct icon per report group, mirroring how the mediation sidebar
// renders its own catalog.
const REPORT_GROUP_ICON: Record<string, RatingNavChild["icon"]> = {
  Reconciliation: Scale,
  Detail: ListChecks,
  Financial: Landmark,
  Operations: FileText,
};

export function RatingSidebarNav({
  collapsed,
  items = RATING_NAV,
}: {
  collapsed: boolean;
  /**
   * Which modules to render. Defaults to the full list; the Sidebar narrows it
   * when the selected Assurance Scope is not Rating, since some modules are
   * rating-specific. Passing the list in rather than filtering inside keeps the
   * scope rule in one place.
   */
  items?: RatingNavItem[];
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const t = useT();

  // "/rating" must match exactly — otherwise the overview stays highlighted on
  // every child route, same rule the default sidebar applies to "/". Children
  // that are a prefix of a sibling (Rule Catalogue vs Create Rule) also
  // exact-match, so the list route doesn't stay lit on its leaf routes.
  const isActive = (to: string) =>
    to === "/rating" || EXACT_MATCH_PATHS.has(to)
      ? pathname === to
      : pathname.startsWith(to);

  // One open flag per expandable group; the group containing the current
  // route opens itself so a deep link never lands in a collapsed tree.
  const groupOf = (path: string) =>
    items.find((item) => item.children?.some((c) => path.startsWith(c.to)))
      ?.to;
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => {
    const active = groupOf(pathname);
    return active ? { [active]: true } : {};
  });
  const search = useRouterState({ select: (st) => st.location.search }) as {
    report?: string;
  };
  const onReports = pathname.startsWith("/rating/reports");
  const selectedReport = onReports
    ? RATING_REPORTS.some((r) => r.key === search.report && r.available)
      ? search.report
      : DEFAULT_RATING_REPORT_KEY
    : null;

  // The grouped report catalog, rendered under "Reports & Certified Exports".
  const renderReportGroups = () =>
    RATING_REPORT_GROUPS.map((g) => {
      const groupReports = RATING_REPORTS.filter((r) => r.group === g);
      if (groupReports.length === 0) return null;
      return (
        <div key={g}>
          <div className="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wide text-sidebar-foreground/40">
            {t(g)}
          </div>
          {groupReports.map((r) => {
            const RIcon = REPORT_GROUP_ICON[r.group] ?? FileText;
            if (!r.available) {
              return (
                <SoonRow key={r.key} label={r.title} icon={RIcon} nested />
              );
            }
            const childActive = selectedReport === r.key;
            return (
              <Link
                key={r.key}
                to="/rating/reports"
                search={{ report: r.key }}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors ${
                  childActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <RIcon
                  className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`}
                />
                <span className="flex-1 truncate">{t(r.title)}</span>
              </Link>
            );
          })}
        </div>
      );
    });

  const activeGroup = groupOf(pathname);
  useEffect(() => {
    if (activeGroup) {
      setOpenGroups((prev) =>
        prev[activeGroup] ? prev : { ...prev, [activeGroup]: true },
      );
    }
  }, [activeGroup]);

  return (
    <>
      {items.map((item) => {
        const Icon = item.icon;
        const children = item.children;

        // Reports is a catalog accordion, not a child list — same treatment
        // the mediation sidebar gives its own report catalog.
        if (
          item.to === "/rating/reports" &&
          RATING_AVAILABLE_PATHS.has(item.to)
        ) {
          const open = openGroups[item.to] ?? onReports;
          if (collapsed) {
            return (
              <div key={item.to} className="group relative">
                <Link
                  to="/rating/reports"
                  aria-label={t(item.label)}
                  className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg text-sm transition-colors ${
                    onReports
                      ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                      : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                  }`}
                >
                  <FileBarChart2
                    className={`h-4 w-4 shrink-0 ${onReports ? "text-primary" : ""}`}
                  />
                </Link>
                <div className="invisible -translate-x-1 opacity-0 group-hover:visible group-hover:translate-x-0 group-hover:opacity-100 transition duration-150 ease-out absolute left-full top-0 pl-2.5 z-50">
                  <span className="absolute left-[6px] top-4 z-10 h-2.5 w-2.5 rotate-45 rounded-[2px] border-l border-b border-sidebar-border bg-sidebar" />
                  <div className="relative w-64 rounded-xl border border-sidebar-border bg-sidebar shadow-2xl ring-1 ring-black/5 py-2 max-h-[70vh] overflow-y-auto">
                    <div className="flex items-center gap-2.5 px-3 pb-2.5 mb-1 border-b border-sidebar-border">
                      <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                        <FileBarChart2 className="h-4 w-4" />
                      </span>
                      <div className="text-[13px] font-semibold text-sidebar-foreground leading-tight truncate">
                        {t(item.label)}
                      </div>
                    </div>
                    <div className="px-1.5">{renderReportGroups()}</div>
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div key={item.to}>
              <button
                onClick={() =>
                  setOpenGroups((prev) => ({ ...prev, [item.to]: !open }))
                }
                aria-expanded={open}
                className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  onReports
                    ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <span className="flex items-center gap-3 min-w-0">
                  <FileBarChart2
                    className={`h-4 w-4 shrink-0 ${onReports ? "text-primary" : ""}`}
                  />
                  <span className="truncate">{t(item.label)}</span>
                </span>
                <ChevronDown
                  className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${open ? "rotate-180" : ""}`}
                />
              </button>
              {open && (
                <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                  {renderReportGroups()}
                </div>
              )}
            </div>
          );
        }
        const available = RATING_AVAILABLE_PATHS.has(item.to);
        const active = children
          ? children.some((c) => isActive(c.to))
          : isActive(item.to);

        if (!available && !children) {
          return collapsed ? (
            <div
              key={item.to}
              title={`${t(item.label)} — ${t("Not available yet")}`}
              className="flex items-center justify-center px-3 py-2.5 rounded-lg text-sidebar-foreground/35 cursor-not-allowed select-none"
            >
              <Icon className="h-4 w-4 shrink-0" />
            </div>
          ) : (
            <SoonRow key={item.to} label={item.label} icon={Icon} />
          );
        }

        // Expandable group.
        if (children && !collapsed) {
          const open = !!openGroups[item.to];
          return (
            <div key={item.to}>
              <button
                onClick={() =>
                  setOpenGroups((prev) => ({ ...prev, [item.to]: !open }))
                }
                aria-expanded={open}
                className={`group relative w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <span className="flex items-center gap-3 min-w-0">
                  <Icon
                    className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`}
                  />
                  <span className="truncate">{t(item.label)}</span>
                </span>
                <ChevronDown
                  className={`h-4 w-4 shrink-0 text-sidebar-foreground/50 transition-transform ${open ? "rotate-180" : ""}`}
                />
              </button>

              {open && (
                <div className="mt-1 mb-1 ml-4 pl-3 border-l border-sidebar-border space-y-0.5">
                  {children.map((c) => {
                    const CIcon = c.icon;
                    if (!RATING_AVAILABLE_PATHS.has(c.to)) {
                      return (
                        <SoonRow
                          key={c.to}
                          label={c.label}
                          icon={CIcon}
                          nested
                        />
                      );
                    }
                    const childActive = isActive(c.to);
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
                        <CIcon
                          className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`}
                        />
                        <span className="flex-1 truncate">{t(c.label)}</span>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        }

        // Collapsed rail: a hover flyout, same treatment the default sidebar
        // gives Reports and Operations.
        if (children && collapsed) {
          return (
            <div key={item.to} className="group relative">
              <Link
                to={
                  (
                    children.find((c) => RATING_AVAILABLE_PATHS.has(c.to)) ??
                    children[0]
                  ).to
                }
                aria-label={t(item.label)}
                className={`relative flex items-center justify-center px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                }`}
              >
                <Icon
                  className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`}
                />
              </Link>

              <div className="invisible -translate-x-1 opacity-0 group-hover:visible group-hover:translate-x-0 group-hover:opacity-100 transition duration-150 ease-out absolute left-full top-0 pl-2.5 z-50">
                <span className="absolute left-[6px] top-4 z-10 h-2.5 w-2.5 rotate-45 rounded-[2px] border-l border-b border-sidebar-border bg-sidebar" />
                <div className="relative w-60 rounded-xl border border-sidebar-border bg-sidebar shadow-2xl ring-1 ring-black/5 py-2">
                  <div className="flex items-center gap-2.5 px-3 pb-2.5 mb-1 border-b border-sidebar-border">
                    <span className="h-8 w-8 shrink-0 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="text-[13px] font-semibold text-sidebar-foreground leading-tight truncate">
                        {t(item.label)}
                      </div>
                    </div>
                  </div>
                  <div className="px-1.5 space-y-0.5">
                    {children.map((c) => {
                      const CIcon = c.icon;
                      if (!RATING_AVAILABLE_PATHS.has(c.to)) {
                        return (
                          <SoonRow
                            key={c.to}
                            label={c.label}
                            icon={CIcon}
                            nested
                          />
                        );
                      }
                      const childActive = isActive(c.to);
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
                          <CIcon
                            className={`h-3.5 w-3.5 shrink-0 ${childActive ? "text-primary" : ""}`}
                          />
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

        return (
          <Link
            key={item.to}
            to={item.to}
            title={collapsed ? t(item.label) : undefined}
            className={`group relative flex items-center ${collapsed ? "justify-center" : "gap-3"} px-3 py-2.5 rounded-lg text-sm transition-colors ${
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
            }`}
          >
            <Icon
              className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`}
            />
            {!collapsed && <span className="truncate">{t(item.label)}</span>}
            {collapsed && (
              <span className="pointer-events-none absolute left-full ml-2 whitespace-nowrap rounded-md bg-foreground text-background text-xs px-2 py-1 opacity-0 group-hover:opacity-100 transition shadow-lg z-50">
                {t(item.label)}
              </span>
            )}
          </Link>
        );
      })}
    </>
  );
}
