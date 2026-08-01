import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { NamedValue } from "@/lib/assurance/dashboard-config";
import { TOOLTIP_STYLE, TOOLTIP_WRAPPER, categorical, compact } from "./viz";

// ---------------------------------------------------------------------------
// Donut with a legend table beside it. The legend is not optional: three of the
// categorical steps sit below 3:1 against the light surface, so identity must
// never be carried by colour alone — every slice is named and valued in the
// list, which doubles as the chart's table view.
// ---------------------------------------------------------------------------

export function DonutChart({
  data,
  centerLabel,
  valueFormatter = compact,
  colors,
}: {
  data: NamedValue[];
  centerLabel: string;
  valueFormatter?: (n: number) => string;
  /**
   * Per-slice colours, positional, overriding the categorical order.
   *
   * Needed when a slice means the same thing as a series in a NEIGHBOURING
   * panel: "matched" drawn green in one card and orange in the next reads as
   * two different things. Passing colours here keeps one meaning to one colour
   * across the page.
   */
  colors?: readonly string[];
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0) || 1;
  const colorAt = (i: number) => colors?.[i] ?? categorical(i);

  return (
    <div className="flex h-[268px] flex-col gap-2 px-3">
      <div className="relative h-[150px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={48}
              outerRadius={70}
              paddingAngle={2}
              stroke="var(--viz-surface)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {data.map((d, i) => (
                <Cell key={d.name} fill={colorAt(i)} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              wrapperStyle={TOOLTIP_WRAPPER}
              formatter={(value: number, name: string) => [
                `${valueFormatter(Number(value))} · ${((Number(value) / total) * 100).toFixed(1)}%`,
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="tabular text-lg font-semibold leading-none text-foreground">
            {valueFormatter(total)}
          </span>
          <span className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            {centerLabel}
          </span>
        </div>
      </div>

      <ul className="flex-1 space-y-[5px] overflow-hidden px-1">
        {data.map((d, i) => (
          <li key={d.name} className="flex items-center gap-2 text-[11.5px]">
            <span
              className="size-2 shrink-0 rounded-[3px]"
              style={{ background: colorAt(i) }}
            />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {d.name}
            </span>
            <span className="tabular shrink-0 font-medium text-foreground">
              {((d.value / total) * 100).toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
