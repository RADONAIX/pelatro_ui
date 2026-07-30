import { cn } from "@/lib/utils";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { ReactNode } from "react";

export function SectionHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  children,
  className,
}: {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("panel overflow-hidden", className)}>
      {title && (
        <header className="flex items-baseline justify-between border-b border-border px-4 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            {title}
          </h3>
          {subtitle && <span className="font-mono text-[10px] text-muted-foreground">{subtitle}</span>}
        </header>
      )}
      {children}
    </section>
  );
}

export function KpiCard({
  label,
  value,
  delta,
  intent,
}: {
  label: string;
  value: string;
  delta: number;
  intent: "good" | "bad" | "neutral";
}) {
  const Icon = delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="panel p-4">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="tabular mt-2 text-2xl font-semibold">{value}</p>
      <p
        className={cn(
          "mt-1.5 flex items-center gap-1 text-xs",
          intent === "good" && "text-success",
          intent === "bad" && "text-destructive",
          intent === "neutral" && "text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
        {Math.abs(delta).toFixed(1)}% vs prior period
      </p>
    </div>
  );
}

const TONES: Record<string, string> = {
  critical: "border-destructive/40 bg-destructive/12 text-destructive",
  high: "border-warning/40 bg-warning/12 text-warning",
  medium: "border-info/40 bg-info/12 text-info",
  Fail: "border-destructive/40 bg-destructive/12 text-destructive",
  Warning: "border-warning/40 bg-warning/12 text-warning",
  Pass: "border-success/40 bg-success/12 text-success",
  Open: "border-destructive/40 bg-destructive/12 text-destructive",
  Investigating: "border-warning/40 bg-warning/12 text-warning",
  "In Progress": "border-warning/40 bg-warning/12 text-warning",
  "Pending Review": "border-info/40 bg-info/12 text-info",
  Assigned: "border-info/40 bg-info/12 text-info",
  Resolved: "border-success/40 bg-success/12 text-success",
  Active: "border-success/40 bg-success/12 text-success",
  Draft: "border-info/40 bg-info/12 text-info",
  Closed: "border-success/40 bg-success/12 text-success",
};

export function Tag({ value, className }: { value: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        TONES[value] ?? "border-border bg-secondary text-secondary-foreground",
        className,
      )}
    >
      {value}
    </span>
  );
}