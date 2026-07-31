import { createFileRoute, notFound } from "@tanstack/react-router";
import { NAV_SECTIONS, getApp, type AppMetadata } from "@/lib/assurance/platform-metadata";
import { SectionTabs } from "@/components/assurance/SectionTabs";
import { DashboardSection } from "@/components/assurance/sections/DashboardSection";
import { ControlsSection } from "@/components/assurance/sections/ControlsSection";
import { ExceptionsSection } from "@/components/assurance/sections/ExceptionsSection";
import { InvestigationsSection } from "@/components/assurance/sections/InvestigationsSection";
import { AnalyticsSection } from "@/components/assurance/sections/AnalyticsSection";
import { AdministrationSection } from "@/components/assurance/sections/AdministrationSection";

export const Route = createFileRoute("/assurance/$appId/$section")({
  beforeLoad: ({ params }) => {
    const app = getApp(params.appId);
    const valid = NAV_SECTIONS.some((s) => s.id === params.section);
    if (!app || !valid) throw notFound();
  },
  head: ({ params }) => {
    const app = getApp(params.appId);
    const section = NAV_SECTIONS.find((s) => s.id === params.section);
    const title = `${section?.label ?? "Workspace"} — ${app?.name ?? "Assurance"} | RADONaix`;
    const description =
      app?.summary ?? "Metadata-driven enterprise assurance across usage, billing and revenue.";
    return {
      meta: [
        { title },
        { name: "description", content: description },
        { property: "og:title", content: title },
        { property: "og:description", content: description },
      ],
    };
  },
  component: SectionPage,
});

function SectionPage() {
  const { appId, section } = Route.useParams();
  const app = getApp(appId)!;

  // The dashboard carries its own executive header (title, subtitle, filters),
  // so the generic section header would be a second copy of the same thing.
  return (
    <>
      {section !== "dashboard" && <SectionTabs app={app} section={section} />}
      {renderSection(app, section)}
    </>
  );
}

function renderSection(app: AppMetadata, section: string) {
  switch (section) {
    case "controls":
      return <ControlsSection app={app} />;
    case "exceptions":
      return <ExceptionsSection app={app} />;
    case "investigations":
      return <InvestigationsSection app={app} />;
    case "analytics":
      return <AnalyticsSection app={app} />;
    case "administration":
      return <AdministrationSection app={app} />;
    default:
      return <DashboardSection app={app} />;
  }
}
