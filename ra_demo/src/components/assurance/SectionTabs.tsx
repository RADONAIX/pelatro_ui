import { NAV_SECTIONS, getWorkspace, type AppMetadata } from "@/lib/assurance/platform-metadata";

// ---------------------------------------------------------------------------
// Page header for an Enterprise Assurance app.
//
// This used to carry a tab strip for the six sections. The sidebar now lists
// them (AssuranceSidebarNav), so tabs here would be a second copy of the same
// navigation — the header just identifies which app and section you are on.
// ---------------------------------------------------------------------------

export function SectionTabs({ app, section }: { app: AppMetadata; section?: string }) {
  const workspace = getWorkspace(app.workspace);
  const current = NAV_SECTIONS.find((s) => s.id === section);

  return (
    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between mb-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl md:text-2xl font-semibold tracking-tight text-foreground">
            {current ? `${current.label} — ${app.name}` : app.name}
          </h1>
          {workspace && (
            <span className="rounded border border-border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {workspace.name}
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl leading-relaxed">{app.summary}</p>
      </div>
      <div className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="size-1.5 animate-pulse rounded-full bg-success" />
          Execution engine live
        </span>
        <span className="hidden font-mono sm:inline">{app.controlRange}</span>
      </div>
    </div>
  );
}
