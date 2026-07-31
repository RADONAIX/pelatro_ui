import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Point } from "@/lib/assurance/dashboard-config";
import { AXIS_TICK, TOOLTIP_STYLE, VIZ } from "./viz";

// Revenue at Risk, last 30 days. One series, so no legend — the card title
// names it. Common to every assurance, unchanged.

export function RiskLineChart({ data }: { data: Point[] }) {
  return (
    <div className="h-[268px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
        >
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
            interval={6}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(v: number) => `₹${v.toFixed(0)}`}
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
            formatter={(value: number) => [
              `₹${Number(value).toFixed(2)} Cr`,
              "Revenue at Risk",
            ]}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={VIZ.risk}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: VIZ.surface }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
