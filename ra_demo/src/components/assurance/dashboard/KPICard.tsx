import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// The KPI card. Five of these open every assurance dashboard, always the same
// five in the same order — only the values change.
//
// The sparkline is decoration for the trend, not a readable chart: no axes, no
// tooltip. The delta beside it carries the number, and its colour is decided by
// `higherIsBetter`, so "-6.4%" reads as good on Revenue at Risk and bad on
// Reconciliation Success without the caller having to say which.
// ---------------------------------------------------------------------------

export function KPICard({
  icon: Icon,
  title,
  description,
  value,
  delta,
  higherIsBetter,
  spark,
  sparkId,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  value: string;
  delta: number;
  higherIsBetter: boolean;
  spark: number[];
  sparkId: string;
}) {
  const positive = delta >= 0;
  const good = positive === higherIsBetter;
  const Trend = positive ? ArrowUpRight : ArrowDownRight;
  const color = good ? "var(--viz-good)" : "var(--viz-bad)";
  const data = spark.map((v, i) => ({ i, v }));

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

      <div className="mt-3 flex items-end justify-between gap-3">
        <span
          className={cn("tabular flex items-center gap-1 text-xs font-medium")}
          style={{ color }}
        >
          <Trend className="size-3.5" />
          {Math.abs(delta).toFixed(1)}%
          <span className="ml-1 font-normal text-muted-foreground">
            vs prior
          </span>
        </span>

        <div className="h-8 w-[84px] shrink-0" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={data}
              margin={{ top: 2, right: 0, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id={sparkId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.22} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="v"
                stroke={color}
                strokeWidth={2}
                fill={`url(#${sparkId})`}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
