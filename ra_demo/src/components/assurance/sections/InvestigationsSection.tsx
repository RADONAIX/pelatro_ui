import { useMemo, useState } from "react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { buildInvestigations } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader, Tag } from "../primitives";
import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";

export function InvestigationsSection({ app }: { app: AppMetadata }) {
  const cases = useMemo(() => buildInvestigations(app, 7), [app]);
  const [selected, setSelected] = useState(cases[0].id);
  const active = cases.find((c) => c.id === selected) ?? cases[0];

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Investigation & Case Management"
        description={`Drill-down chain for ${app.name}: ${app.investigationChain.join(" → ")}`}
      />

      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <Panel title="Open cases" subtitle={`${cases.length} active`}>
          <ul className="divide-y divide-border">
            {cases.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => setSelected(c.id)}
                  className={cn(
                    "w-full px-4 py-3 text-left transition-colors hover:bg-accent/40",
                    c.id === active.id && "bg-primary/8",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-primary">{c.id}</span>
                    <Tag value={c.status} />
                  </div>
                  <p className="mt-1 text-sm">{c.title}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {c.owner} · opened {c.opened} · {c.impact}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-4">
          <Panel title="Record lineage" subtitle={active.id}>
            <div className="flex flex-wrap items-stretch gap-2 p-4">
              {app.investigationChain.map((step, i) => (
                <div key={step} className="flex items-center gap-2">
                  <div
                    className={cn(
                      "min-w-[150px] rounded border px-3 py-2.5",
                      i <= active.stage
                        ? "border-primary/40 bg-primary/10"
                        : "border-border bg-secondary/40",
                    )}
                  >
                    <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                      Stage {i + 1}
                    </p>
                    <p className="text-sm">{step}</p>
                    <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                      {i <= active.stage ? "traced" : "pending"}
                    </p>
                  </div>
                  {i < app.investigationChain.length - 1 && (
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  )}
                </div>
              ))}
            </div>
          </Panel>

          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="Evidence" subtitle="canonical layers">
              <ul className="divide-y divide-border text-sm">
                {[
                  ["Bronze", "raw feed payload attached"],
                  ["Silver", "conformed record, 3 field deltas"],
                  ["Gold", "assurance mart row rebuilt"],
                  ["TIM", "entity mapped to canonical model"],
                ].map(([layer, note]) => (
                  <li key={layer} className="flex justify-between px-4 py-2.5">
                    <span className="font-mono text-xs text-primary">{layer}</span>
                    <span className="text-right text-xs text-muted-foreground">{note}</span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel title="Workflow" subtitle="case timeline">
              <ol className="space-y-3 p-4 text-sm">
                {[
                  ["Detected", "Execution engine raised exception cluster"],
                  ["Triaged", `Assigned to ${active.owner}`],
                  ["Root cause", "Upstream feed truncation suspected"],
                  ["Remediation", "Reprocess request queued"],
                ].map(([t, d], i) => (
                  <li key={t} className="flex gap-3">
                    <span className="mt-1 size-2 shrink-0 rounded-full bg-primary" />
                    <span>
                      <span className="block font-medium">{t}</span>
                      <span className="block text-xs text-muted-foreground">{d}</span>
                    </span>
                    <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                      T+{i * 6}h
                    </span>
                  </li>
                ))}
              </ol>
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}