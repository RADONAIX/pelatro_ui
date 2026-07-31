import { useT } from "@/lib/i18n";
import type { RuleStatus } from "@/lib/rating/types";

// Same visual language as the existing StatusBadge (bordered pill + dot), keyed
// to the rule lifecycle instead of pipeline states. Kept separate rather than
// extending StatusBadge's map so nothing about the existing badge changes.
const TONE: Record<string, string> = {
  DRAFT: "bg-muted text-muted-foreground border-border",
  VALIDATED: "bg-info/10 text-info border-info/20",
  REVIEWED: "bg-[#F8C800]/15 text-[#9a7d00] border-[#F8C800]/40",
  APPROVED: "bg-success/10 text-success border-success/20",
  COMPILED: "bg-success/10 text-success border-success/20",
  PUBLISHED: "bg-success/10 text-success border-success/20",
  ACTIVE: "bg-success/10 text-success border-success/20",
  SUPERSEDED: "bg-[#F97316]/10 text-[#F97316] border-[#F97316]/30",
  RETIRED: "bg-muted text-muted-foreground border-border",
};

const LABEL: Record<string, string> = {
  DRAFT: "Draft",
  VALIDATED: "Validated",
  REVIEWED: "In review",
  APPROVED: "Approved",
  COMPILED: "Compiled",
  PUBLISHED: "Published",
  ACTIVE: "Active",
  SUPERSEDED: "Superseded",
  RETIRED: "Retired",
};

export function RuleStatusBadge({ status }: { status: RuleStatus | string }) {
  const t = useT();
  const cls = TONE[status] ?? "bg-muted text-muted-foreground border-border";
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-md border ${cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {t(LABEL[status] ?? status)}
    </span>
  );
}
