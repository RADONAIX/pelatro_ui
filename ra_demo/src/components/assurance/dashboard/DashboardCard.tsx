import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// The one card surface the whole executive dashboard is built from: 14px
// corners, soft border, soft shadow, generous padding. Every panel uses it, so
// the grid stays visually identical across all eight assurances.

export function DashboardCard({
  title,
  subtitle,
  meta,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  subtitle?: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cn(
        "flex flex-col rounded-[14px] border border-border/70 bg-card",
        "shadow-[0_1px_2px_0_rgb(16_24_40/0.04),0_1px_3px_0_rgb(16_24_40/0.06)]",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h3 className="truncate text-[13px] font-semibold tracking-tight text-foreground">
            {title}
          </h3>
          {subtitle && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {subtitle}
            </p>
          )}
        </div>
        {meta && <div className="shrink-0">{meta}</div>}
      </header>
      <div className={cn("flex-1 px-2 pb-4", bodyClassName)}>{children}</div>
    </section>
  );
}

/** The colour swatch + label pair used by every legend on the dashboard. */
export function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <span className="size-2 rounded-[3px]" style={{ background: color }} />
      {label}
    </span>
  );
}
