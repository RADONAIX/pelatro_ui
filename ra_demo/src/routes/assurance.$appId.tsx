import { createFileRoute, notFound, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { SectionTabs } from "@/components/assurance/SectionTabs";
import { getApp } from "@/lib/assurance/platform-metadata";

// ---------------------------------------------------------------------------
// DEMO-ONLY — Enterprise Assurance app layout.
//
// Every /assurance/:appId/* page renders inside RADONaix's own AppShell (auth
// gate, sidebar, header, download tray), with the app's six sections switched
// by the in-page tab strip. All data is generated client-side from
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
  // beforeLoad already rejected unknown ids, so this is always defined.
  const app = getApp(appId)!;

  return (
    <AppShell>
      <SectionTabs app={app} />
      <Outlet />
    </AppShell>
  );
}
