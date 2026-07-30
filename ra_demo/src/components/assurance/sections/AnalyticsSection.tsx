import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader } from "../primitives";

export function AnalyticsSection({ app }: { app: AppMetadata }) {
  const radar = app.ruleTypes.map((t, i) => ({
    category: t,
    coverage: 60 + ((i * 37 + app.id.length * 11) % 40),
  }));

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Analytics"
        description="Served by the shared Analytics and ML engines, scoped to this application's metadata."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Detection vs recovery trend" className="lg:col-span-2">
          <div className="h-72 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={app.leakageSeries}>
                <CartesianGrid stroke="var(--color-border)" vertical={false} />
                <XAxis dataKey="period" stroke="var(--color-muted-foreground)" fontSize={11} tickLine={false} />
                <YAxis stroke="var(--color-muted-foreground)" fontSize={11} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-popover)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="detected" stroke="var(--color-chart-4)" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="recovered" stroke="var(--color-chart-1)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Rule category coverage">
          <div className="h-72 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radar} outerRadius="72%">
                <PolarGrid stroke="var(--color-border)" />
                <PolarAngleAxis dataKey="category" tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }} />
                <Radar
                  dataKey="coverage"
                  stroke="var(--color-chart-1)"
                  fill="var(--color-chart-1)"
                  fillOpacity={0.25}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="ML engine signals">
          <ul className="divide-y divide-border text-sm">
            {[
              ["Anomaly score spike", `${app.entities[0]} volume deviates 3.4σ`],
              ["Predicted leakage", "Next cycle exposure $186K (p80)"],
              ["Emerging pattern", `${app.ruleLibrary[0].name} clustering by region`],
              ["Model drift", "Retrain recommended in 6 days"],
            ].map(([t, d]) => (
              <li key={t} className="px-4 py-3">
                <p className="font-medium">{t}</p>
                <p className="text-xs text-muted-foreground">{d}</p>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Reports">
          <ul className="divide-y divide-border text-sm">
            {[
              [`${app.short} leakage summary`, "Weekly · PDF · auto-distributed"],
              [`${app.short} control attestation`, "Monthly · XLSX · audit pack"],
              [`${app.short} exception ageing`, "Daily · CSV · ops queue"],
              [`${app.short} regulator extract`, "Quarterly · signed"],
            ].map(([t, d]) => (
              <li key={t} className="flex items-center justify-between px-4 py-3">
                <span>{t}</span>
                <span className="text-xs text-muted-foreground">{d}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}