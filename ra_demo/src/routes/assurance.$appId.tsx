import { createFileRoute, notFound, Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { getApp } from "@/lib/assurance/platform-metadata";
import { useAssuranceScope } from "@/lib/assuranceScope";

// ---------------------------------------------------------------------------
// DEMO-ONLY — Enterprise Assurance app layout.
//
// Every /assurance/:appId/* page renders inside RADONaix's AppShell (auth gate,
// sidebar, header, download tray). All data is generated client-side from
// src/lib/assurance/platform-metadata.ts; no backend call is made.
// ---------------------------------------------------------------------------

export const Route = createFileRoute("/assurance/$appId")({
  beforeLoad: ({ params }) => {
    if (!getApp(params.appId)) throw notFound();
  },
  component: AssuranceAppLayout,
});

function AssuranceAppLayout() {
  const { appId } = Route.useParams();
  const { scope, setScope } = useAssuranceScope();

  // The URL wins over the stored scope. Deep-linking to /assurance/billing/...
  // (a bookmark, a shared link, the back button) must leave the header switcher
  // and the sidebar's Enterprise Assurance links pointing at billing too —
  // otherwise the page shows one app while the nav targets another.
  useEffect(() => {
    if (appId !== scope) setScope(appId);
  }, [appId, scope, setScope]);

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
