import { createFileRoute, redirect } from "@tanstack/react-router";

// /assurance/:appId has no page of its own. Selecting an assurance lands on its
// executive dashboard — the "how much revenue is at risk" read-out — which is
// what a user picking an assurance is asking to see.
export const Route = createFileRoute("/assurance/$appId/")({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/assurance/$appId/$section",
      params: { appId: params.appId, section: "dashboard" },
    });
  },
});
