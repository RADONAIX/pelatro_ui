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
      {/* Chips, not a list: this is a reference set of five to seven short
          names, and one row each cost more vertical space than the navigation
          above it. Wrapping keeps every entity visible without scrolling. */}
      <ul className="flex flex-wrap gap-1 px-3 pb-2">
        {app.entities.map((entity) => (
          <li
            key={entity}
            className="rounded-full border border-sidebar-border bg-sidebar-accent/40 px-2 py-0.5 text-[11px] leading-4 text-sidebar-foreground/70"
          >
            {entity}
          </li>
        ))}
      </ul>
    </div>
  );
}
