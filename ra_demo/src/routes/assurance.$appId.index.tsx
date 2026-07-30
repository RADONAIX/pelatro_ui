import { createFileRoute, redirect } from "@tanstack/react-router";

// /assurance/:appId has no page of its own — land on the app's dashboard.
export const Route = createFileRoute("/assurance/$appId/")({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/assurance/$appId/$section",
      params: { appId: params.appId, section: "dashboard" },
    });
  },
});
