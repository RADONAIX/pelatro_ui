import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendPoint } from "@/lib/assurance/dashboard-config";
import { AXIS_TICK, TOOLTIP_STYLE, VIZ, compact } from "./viz";

// The hero chart. Three series, always the same three, always the same colours:
// healthy / exceptions / leakage. Only the title and the numbers change per
// assurance.

const SERIES = [
  { key: "healthy", label: "Healthy Records", color: VIZ.healthy },
  { key: "exceptions", label: "Exception Records", color: VIZ.exceptions },
  { key: "leakage", label: "Revenue Leakage", color: VIZ.leakage },
] as const;

export function AreaTrendChart({
  data,
  unit,
  scaled = true,
  moneyFormatter,
}: {
  data: TrendPoint[];
  unit: string;
  /**
   * Whether the values are authored in thousands.
   *
   * True for the synthetic profiles, which are. False for anything served from
   * a real results table, where 13 rows are 13 rows — the "K" this used to
   * append unconditionally turned them into 13,000.
   */
  scaled?: boolean;
  /** Formats the leakage series, which is money and not a record count. */
  moneyFormatter?: (value: number) => string;
}) {
  const LEAKAGE_LABEL = SERIES[2].label;
  const format = (value: number, name: string) => {
    // The third series is currency. Labelling it with the record unit said
    // "6 CDRs" for what is ₹6 of leakage.
    if (name === LEAKAGE_LABEL && moneyFormatter) return moneyFormatter(value);
    const count = Number(value).toLocaleString("en-IN");
    return scaled ? `${count}K ${unit}` : `${count} ${unit}`;
  };

  return (
    <div className="h-[268px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 8, right: 16, bottom: 0, left: 4 }}
        >
          <defs>
            {SERIES.map((s) => (
              <linearGradient
                key={s.key}
                id={`area-${s.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={s.color} stopOpacity={0.18} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0.01} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid
            stroke={VIZ.grid}
            vertical={false}
            strokeDasharray="3 3"
          />
          <XAxis
            dataKey="label"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: VIZ.grid }}
            tickMargin={10}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={compact}
            width={48}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            cursor={{
              stroke: VIZ.axis,
              strokeWidth: 1,
              strokeDasharray: "3 3",
            }}
            labelStyle={{
              color: "var(--color-foreground)",
              fontWeight: 600,
              marginBottom: 4,
            }}
            formatter={(value: number, name: string) => [
              format(Number(value), name),
              name,
            ]}
          />
          {SERIES.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#area-${s.key})`}
              activeDot={{ r: 4, strokeWidth: 2, stroke: VIZ.surface }}
              dot={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export const AREA_SERIES = SERIES;
