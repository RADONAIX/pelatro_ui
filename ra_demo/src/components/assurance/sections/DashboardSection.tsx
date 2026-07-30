import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { buildControls } from "@/lib/assurance/platform-metadata";
import { KpiCard, Panel, SectionHeader, Tag } from "../primitives";

export function DashboardSection({ app }: { app: AppMetadata }) {
  const controls = buildControls(app, 8);

  return (
    <div className="space-y-5">
      <SectionHeader
        title={app.dashboards[0]}
        description={app.summary}
        actions={
          <div className="flex gap-1.5">
            {app.dashboards.map((d, i) => (
              <span
                key={d}
                className={
                  i === 0
                    ? "rounded border border-primary/40 bg-primary/12 px-2.5 py-1 text-xs text-primary"
                    : "rounded border border-border px-2.5 py-1 text-xs text-muted-foreground"
                }
              >
                {d}
              </span>
            ))}
          </div>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {app.kpis.map((k) => (
          <KpiCard key={k.label} {...k} />
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Leakage detected vs recovered" subtitle="USD thousands" className="lg:col-span-2">
          <div className="h-64 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={app.leakageSeries}>
                <defs>
                  <linearGradient id="gDet" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-chart-4)" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="var(--color-chart-4)" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gRec" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-chart-1)" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="var(--color-chart-1)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--color-border)" vertical={false} />
                <XAxis dataKey="period" stroke="var(--color-muted-foreground)" fontSize={11} tickLine={false} />
                <YAxis stroke="var(--color-muted-foreground)" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-popover)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="detected"
                  stroke="var(--color-chart-4)"
                  fill="url(#gDet)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="recovered"
                  stroke="var(--color-chart-1)"
                  fill="url(#gRec)"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Top exception sources" subtitle="last 24h">
          <div className="h-64 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={app.breakdown} layout="vertical" margin={{ left: 24 }}>
                <CartesianGrid stroke="var(--color-border)" horizontal={false} />
                <XAxis type="number" stroke="var(--color-muted-foreground)" fontSize={11} axisLine={false} />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={110}
                  stroke="var(--color-muted-foreground)"
                  fontSize={10}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={{ fill: "var(--color-accent)" }}
                  contentStyle={{
                    background: "var(--color-popover)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="value" fill="var(--color-chart-2)" radius={[0, 3, 3, 0]} barSize={14} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <Panel title="Control health" subtitle={app.controlRange}>
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-4 py-2 font-medium">Control</th>
              <th className="px-4 py-2 font-medium">Rule category</th>
              <th className="px-4 py-2 font-medium">Entity</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 text-right font-medium">Pass %</th>
              <th className="px-4 py-2 text-right font-medium">Exceptions</th>
            </tr>
          </thead>
          <tbody>
            {controls.map((c) => (
              <tr key={c.id} className="border-b border-border/60 last:border-0 hover:bg-accent/40">
                <td className="px-4 py-2">
                  <span className="font-mono text-xs text-primary">{c.id}</span>
                  <span className="ml-2">{c.name}</span>
                </td>
                <td className="px-4 py-2 text-muted-foreground">{c.category}</td>
                <td className="px-4 py-2 text-muted-foreground">{c.entity}</td>
                <td className="px-4 py-2">
                  <Tag value={c.status} />
                </td>
                <td className="tabular px-4 py-2 text-right">{c.passRate}%</td>
                <td className="tabular px-4 py-2 text-right">{c.exceptions.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}