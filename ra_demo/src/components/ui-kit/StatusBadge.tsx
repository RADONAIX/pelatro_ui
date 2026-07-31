import { useT } from "@/lib/i18n";

const map: Record<string, string> = {
  ok: "bg-success/10 text-success border-success/20",
  completed: "bg-success/10 text-success border-success/20",
  active: "bg-success/10 text-success border-success/20",
  enabled: "bg-success/10 text-success border-success/20",
  running: "bg-[#F8C800]/15 text-[#9a7d00] border-[#F8C800]/40",
  "in progress": "bg-[#F8C800]/15 text-[#9a7d00] border-[#F8C800]/40",
  partial: "bg-[#F97316]/10 text-[#F97316] border-[#F97316]/30",
  pending: "bg-muted text-muted-foreground border-border",
  success: "bg-success/10 text-success border-success/20",
  warning: "bg-warning/15 text-warning-foreground border-warning/30",
  acknowledged: "bg-info/10 text-info border-info/20",
  open: "bg-warning/15 text-warning-foreground border-warning/30",
  failed: "bg-destructive/10 text-destructive border-destructive/20",
  disabled: "bg-muted text-muted-foreground border-border",
  low: "bg-info/10 text-info border-info/20",
  medium: "bg-warning/15 text-warning-foreground border-warning/30",
  high: "bg-destructive/10 text-destructive border-destructive/20",
  critical: "bg-destructive text-destructive-foreground border-destructive",
};

export function StatusBadge({ value }: { value: string }) {
  const t = useT();
  // CSS tone is keyed off the original (English) value; only the label is translated.
  const k = value.toLowerCase();
  const cls = map[k] || "bg-muted text-muted-foreground border-border";
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-md border ${cls}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {t(value)}
    </span>
  );
}
