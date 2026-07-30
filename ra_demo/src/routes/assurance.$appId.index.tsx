import { createFileRoute, redirect } from "@tanstack/react-router";

// /assurance/:appId has no page of its own. Lands on Controls — the app's
// dashboard section is no longer in the sidebar (the rating nav's Dashboard &
// KPIs covers that), so Controls is the first section actually navigable.
export const Route = createFileRoute("/assurance/$appId/")({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/assurance/$appId/$section",
      params: { appId: params.appId, section: "controls" },
    });
  },
});
