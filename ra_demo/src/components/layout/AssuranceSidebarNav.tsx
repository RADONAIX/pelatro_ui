import { Link, useRouterState } from "@tanstack/react-router";
import { Settings2, ShieldCheck, type LucideIcon } from "lucide-react";
import { NAV_SECTIONS } from "@/lib/assurance/platform-metadata";
import { useAssuranceScope } from "@/lib/assuranceScope";
import { useT } from "@/lib/i18n";
import { Tooltip } from "@/components/ui-kit/Tooltip";

// ---------------------------------------------------------------------------
// The Enterprise Assurance entries, merged into the main module list rather
// than sitting under their own heading — they read as ordinary modules.
//
// Only Controls and Administration are surfaced. The other four ASSURA sections
// (dashboard, exceptions, investigations, analytics) duplicate modules the
// rating nav already provides above, so they are not listed. Their routes are
// untouched and still resolve by URL; restoring one is a matter of adding its
// id back to SECTIONS below.
//
// Which app these point at is decided by the header's Assurance Scope switcher.
// ---------------------------------------------------------------------------

const SECTIONS = ["controls", "administration"] as const;

const ICONS: Record<string, LucideIcon> = {
  controls: ShieldCheck,
  administration: Settings2,
};

export function AssuranceSidebarNav({ collapsed }: { collapsed: boolean }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { app } = useAssuranceScope();
  const t = useT();

  const sections = SECTIONS.map((id) => NAV_SECTIONS.find((s) => s.id === id)).filter(
    (s): s is (typeof NAV_SECTIONS)[number] => !!s,
  );

  return (
    <>
      {sections.map((section) => {
        const Icon = ICONS[section.id];
        const active = pathname === `/assurance/${app.id}/${section.id}`;

        const link = (
          <Link
            to="/assurance/$appId/$section"
            params={{ appId: app.id, section: section.id }}
            title={collapsed ? t(section.label) : undefined}
            className={`group relative flex items-center ${collapsed ? "justify-center" : ""} gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary"
                : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
            }`}
          >
            <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : ""}`} />
            {!collapsed && <span className="truncate">{t(section.label)}</span>}
          </Link>
        );

        return collapsed ? (
          <Tooltip key={section.id} label={t(section.label)} side="right">
            {link}
          </Tooltip>
        ) : (
          <div key={section.id}>{link}</div>
        );
      })}

      {/* Entity scope — what the selected app reconciles. Reference text, not
          navigation, so there is nothing useful to render at icon width. */}
      {!collapsed && (
        <div className="pt-3 mt-3 border-t border-sidebar-border">
          <div className="px-3 pb-2 text-[10px] tracking-widest text-sidebar-foreground/40 font-semibold">
            {t("ENTITY SCOPE")}
          </div>
          <ul className="space-y-1.5 px-3 pb-2">
            {app.entities.map((entity) => (
              <li
                key={entity}
                className="flex items-center gap-2 text-[13px] text-sidebar-foreground/60"
              >
                <span className="size-1 shrink-0 rounded-full bg-primary/60" />
                <span className="truncate">{entity}</span>
              </li>
            ))}
          </ul>
          <div className="px-3 pt-2 pb-1">
            <div className="text-[10px] uppercase tracking-widest text-sidebar-foreground/40">
              {t("Control set")}
            </div>
            <div className="font-mono text-xs text-sidebar-foreground/70">{app.controlRange}</div>
          </div>
        </div>
      )}
    </>
  );
}
