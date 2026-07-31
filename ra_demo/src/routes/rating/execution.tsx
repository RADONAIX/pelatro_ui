import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { MirrorAssurancePanel } from "@/components/rating/MirrorAssurancePanel";
import { useT } from "@/lib/i18n";
import { useCanEditRuns } from "@/lib/rating/hooks";

// Assurance Execution — trigger, schedule and review the Rating Assurance
// reconciliation jobs that run against the mirror database.
//
// On main this panel replaced /rating/pipeline outright. Here it gets its own
// route instead: /rating/pipeline is the Pipelines & Job Monitor screen, which
// monitors AIR/SDP batch ingestion and is a different question from "run the
// rating reconciliation". Two screens, two paths, neither overwriting the
// other.
export const Route = createFileRoute("/rating/execution")({
  component: AssuranceExecutionPage,
});

function AssuranceExecutionPage() {
  const t = useT();
  const canEdit = useCanEditRuns();

  return (
    <AppShell>
      <PageHeader
        title={t("Assurance Execution")}
        description={t(
          "Trigger, schedule, monitor and review Rating Assurance reconciliation jobs.",
        )}
        info={t(
          "Rating Assurance reads the mirror database without modifying it and keeps each run's results in the control plane.",
        )}
      />

      <MirrorAssurancePanel canEdit={canEdit} />
    </AppShell>
  );
}
