import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { MirrorAssurancePanel } from "@/components/rating/MirrorAssurancePanel";
import { useT } from "@/lib/i18n";
import { useCanEditRuns } from "@/lib/rating/hooks";

export const Route = createFileRoute("/rating/pipeline")({
  component: PipelinePage,
});

function PipelinePage() {
  const t = useT();
  const canEdit = useCanEditRuns();

  return (
    <AppShell>
      <PageHeader
        title={t("Pipelines & Job Monitor")}
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
