import { useAssuranceScope } from "@/lib/assuranceScope";
import { useT } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// The entity scope block: what the assurance app selected in the header
// actually reconciles.
//
// This used to also render the ASSURA section links. Controls now lives under
// Operations (injected by the Sidebar so it can carry the selected app id), and
// the other five sections are not in the nav — so what remains here is purely
// the reference list. Routes for every section are untouched and still resolve.
// ---------------------------------------------------------------------------

export function AssuranceSidebarNav({ collapsed }: { collapsed: boolean }) {
  const { app } = useAssuranceScope();
  const t = useT();

  // Reference text, not navigation — there is nothing useful to show at icon
  // width, so the collapsed rail omits it entirely.
  if (collapsed) return null;
  // No assurance chosen yet: there is no entity scope to describe, and an empty
  // heading would read as "this assurance has no entities".
  if (!app) return null;

  return (
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
  );
}
