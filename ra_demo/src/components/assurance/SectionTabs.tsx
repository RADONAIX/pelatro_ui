import { Link, useRouterState } from "@tanstack/react-router";
import {
  AlertTriangle,
  BarChart3,
  LayoutDashboard,
  Search,
  Settings2,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { NAV_SECTIONS, getWorkspace, type AppMetadata } from "@/lib/assurance/platform-metadata";

// ---------------------------------------------------------------------------
// Page header + section switcher for an Enterprise Assurance app.
//
// In the standalone ASSURA app these six sections lived in a dedicated 256px
// sidebar. Here the sidebar belongs to RADONaix, so the sections become an
// in-page tab strip instead — the app itself is what the sidebar selects, and
// this row switches between its sections.
// ---------------------------------------------------------------------------

const ICONS: Record<string, LucideIcon> = {
  dashboard: LayoutDashboard,
  controls: ShieldCheck,
  exceptions: AlertTriangle,
  investigations: Search,
  analytics: BarChart3,
  administration: Settings2,
};

export function SectionTabs({ app }: { app: AppMetadata }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const workspace = getWorkspace(app.workspace);

  return (
    <div className="mb-6">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl md:text-2xl font-semibold tracking-tight text-foreground">
              {app.name}
            </h1>
            {workspace && (
              <span className="rounded border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {workspace.name}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl leading-relaxed">
            {app.summary}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 animate-pulse rounded-full bg-success" />
            Execution engine live
          </span>
          <span className="hidden font-mono sm:inline">{app.controlRange}</span>
        </div>
      </div>

      <nav className="mt-4 flex gap-1 overflow-x-auto border-b border-border">
        {NAV_SECTIONS.map((section) => {
          const Icon = ICONS[section.id];
          const active = pathname === `/assurance/${app.id}/${section.id}`;
          return (
            <Link
              key={section.id}
              to="/assurance/$appId/$section"
              params={{ appId: app.id, section: section.id }}
              className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors ${
                active
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
              {section.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
