import { type LucideIcon } from "lucide-react";

// ---------------------------------------------------------------------------
// The KPI card. Five of these open every assurance dashboard, always the same
// five in the same order — only the values change.
//
// The card shows the figure and nothing else. It previously carried a
// period-on-period delta and a sparkline; both are gone by request. The
// underlying data still holds them, so restoring the row means rendering
// `delta`/`spark` again rather than recomputing anything.
// ---------------------------------------------------------------------------

export function KPICard({
  icon: Icon,
  title,
  description,
  value,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  value: string;
}) {
  return (
    <div
      className="group rounded-[14px] border border-border/70 bg-card px-5 py-4 shadow-[0_1px_2px_0_rgb(16_24_40/0.04),0_1px_3px_0_rgb(16_24_40/0.06)] transition-colors hover:border-border"
      title={description}
    >
      <div className="flex items-center gap-2.5">
        <span className="flex size-7 items-center justify-center rounded-lg bg-primary/8 text-primary ring-1 ring-inset ring-primary/12">
          <Icon className="size-3.5" strokeWidth={2} />
        </span>
        <p className="truncate text-[12px] font-medium text-muted-foreground">
          {title}
        </p>
      </div>

      <p className="tabular mt-3.5 text-[26px] font-semibold leading-none tracking-tight text-foreground">
        {value}
      </p>
    </div>
  );
}
