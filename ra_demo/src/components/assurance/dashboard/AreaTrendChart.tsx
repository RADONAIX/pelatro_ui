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
}: {
  data: TrendPoint[];
  unit: string;
}) {
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
              `${Number(value).toLocaleString("en-IN")}K ${unit}`,
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
