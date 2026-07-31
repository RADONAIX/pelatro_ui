import { createFileRoute } from "@tanstack/react-router";
import { LayoutDashboard } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { ASSURANCE_APPS } from "@/lib/assuranceScope";

// ---------------------------------------------------------------------------
// Enterprise Dashboard — the cross-assurance view.
//
// Deliberately empty for now: the placement, route and nav entry are settled so
// the module has a home, but no KPIs have been designed yet.
//
// The one property that matters and is already true: this page is
// scope-INDEPENDENT. Every other module below it in the sidebar re-targets when
// the header's Assurance Scope changes; this one always covers all assurances,
// which is why it sits above them rather than among them. Whatever is built
// here must aggregate across ASSURANCE_APPS rather than read the current scope.
// ---------------------------------------------------------------------------

export const Route = createFileRoute("/dashboard")({
  component: EnterpriseDashboardPage,
});

function EnterpriseDashboardPage() {
  return (
    <AppShell>
      <PageHeader
        title="Enterprise Dashboard"
        description="High-level view across all assurance use cases. Independent of the assurance scope selected in the header."
      />

      <div className="grid min-h-[320px] place-items-center rounded-xl border border-dashed border-border bg-card/40 p-10 text-center">
        <div className="max-w-md">
          <span className="mx-auto grid size-11 place-items-center rounded-xl bg-muted text-muted-foreground">
            <LayoutDashboard className="h-5 w-5" />
          </span>
          <p className="mt-4 text-sm font-medium text-foreground">Not built yet</p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            This module will roll up all {ASSURANCE_APPS.length} assurances into one view. Each
            assurance keeps its own Dashboard &amp; KPIs below.
          </p>
        </div>
      </div>
    </AppShell>
  );
}
