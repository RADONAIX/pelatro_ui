import type { NamedValue } from "@/lib/assurance/dashboard-config";

// ---------------------------------------------------------------------------
// Horizontal bars, drawn in plain HTML rather than Recharts.
//
// At this size a bar chart is a labelled list: the name, the bar, the number.
// Rendering it as flex rows keeps every value directly labelled (no tooltip
// needed to read it), keeps the rows on the same rhythm as the donut legend
// next to it, and avoids the category-axis truncation Recharts does with long
// entity names like "Roaming Settlement Difference".
// ---------------------------------------------------------------------------

export function HorizontalBarChart({
  data,
  color,
  valueFormatter,
}: {
  data: NamedValue[];
  color: string;
  valueFormatter: (n: number) => string;
}) {
  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="flex h-[268px] flex-col justify-center gap-3.5 px-4">
      {data.map((d) => (
        <div key={d.name}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate text-[12px] text-foreground">
              {d.name}
            </span>
            <span className="tabular shrink-0 text-[12px] font-medium text-muted-foreground">
              {valueFormatter(d.value)}
            </span>
          </div>
          <div className="mt-1.5 h-[7px] w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${Math.max(3, (d.value / max) * 100)}%`,
                background: color,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
