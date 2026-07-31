import type { ReactNode } from "react";
import { ArrowUp, ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  subtitle,
  action,
  className,
  children,
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("card-surface flex h-full flex-col p-4", className)}>
      {title ? (
        <div className="mb-3 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
          <h2 className="truncate text-sm font-semibold tracking-tight text-foreground">
            {title}
            {subtitle ? (
              <span className="ml-1 font-normal text-muted-foreground">
                {subtitle}
              </span>
            ) : null}
          </h2>

          {action}
        </div>
      ) : null}

      <div className="min-w-0 flex-1">{children}</div>
    </section>
  );
}

export function LinkAction({ label = "View All" }: { label?: string }) {
  return (
    <button className="shrink-0 text-[11px] font-semibold text-info hover:underline">{label}</button>
  );
}

export function Delta({
  value,
  up = true,
  good = true,
  suffix,
}: {
  value: string;
  up?: boolean;
  good?: boolean;
  suffix?: string;
}) {
  const Icon = up ? ArrowUp : ArrowDown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-[11px] font-semibold",
        good ? "text-success" : "text-destructive",
      )}
    >
      <Icon className="size-3" />
      {value}
      {suffix ? <span className="font-normal text-muted-foreground"> {suffix}</span> : null}
    </span>
  );
}
