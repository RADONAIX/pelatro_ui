import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES, UNIVERSAL_SERVICES } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader, Tag } from "../primitives";

export function AdministrationSection({ app }: { app: AppMetadata }) {
  return (
    <div className="space-y-5">
      <SectionHeader
        title="Administration"
        description={`${app.name} administrators configure only the objects in this application's metadata scope.`}
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {app.adminObjects.map((o) => (
          <div key={o} className="panel p-4">
            <p className="text-sm font-medium">{o}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Configuration object · versioned in Metadata Engine
            </p>
            <button className="mt-3 rounded border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary">
              Configure
            </button>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Application metadata contract">
          <dl className="divide-y divide-border text-sm">
            {[
              ["Application", app.name],
              ["Control range", app.controlRange],
              ["Visible entities", app.entities.join(", ")],
              ["Visible rule types", app.ruleTypes.join(", ")],
              ["Dashboards", app.dashboards.join(", ")],
              ["Investigation chain", app.investigationChain.join(" → ")],
            ].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[140px_1fr] gap-3 px-4 py-2.5">
                <dt className="text-muted-foreground">{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </Panel>

        <div className="space-y-4">
          <Panel title="Universal services consumed" subtitle="no app-local engines">
            <ul className="divide-y divide-border text-sm">
              {UNIVERSAL_SERVICES.map((s) => (
                <li key={s.name} className="flex items-center justify-between px-4 py-2.5">
                  <span>{s.name}</span>
                  <span className="text-xs text-muted-foreground">{s.desc}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Primitive rule categories" subtitle="15 universal">
            <div className="flex flex-wrap gap-1.5 p-4">
              {RULE_CATEGORIES.map((c) => {
                const enabled = app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => r.category === c);
                return (
                  <span
                    key={c}
                    className={
                      enabled
                        ? "rounded border border-primary/40 bg-primary/12 px-2 py-0.5 text-xs text-primary"
                        : "rounded border border-border px-2 py-0.5 text-xs text-muted-foreground opacity-60"
                    }
                  >
                    {c}
                  </span>
                );
              })}
            </div>
          </Panel>
        </div>
      </div>

      <Panel title="Execution schedule">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-4 py-2 font-medium">Job</th>
              <th className="px-4 py-2 font-medium">Frequency</th>
              <th className="px-4 py-2 font-medium">Layer</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {[
              [`${app.short} ingestion`, "Every 15 min", "Bronze", "Pass"],
              [`${app.short} conformance`, "Hourly", "Silver", "Pass"],
              [`${app.short} control batch`, "Hourly", "Gold", "Warning"],
              [`${app.short} reconciliation`, "Daily 02:00", "Gold", "Pass"],
            ].map((r) => (
              <tr key={r[0]} className="border-b border-border/60 last:border-0">
                <td className="px-4 py-2">{r[0]}</td>
                <td className="px-4 py-2 text-muted-foreground">{r[1]}</td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">{r[2]}</td>
                <td className="px-4 py-2">
                  <Tag value={r[3]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}