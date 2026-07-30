import { useMemo, useState } from "react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { buildExceptions } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader, Tag } from "../primitives";
import { cn } from "@/lib/utils";

const STATUSES = ["All", "Open", "Investigating", "Assigned", "Resolved"] as const;

export function ExceptionsSection({ app }: { app: AppMetadata }) {
  const all = useMemo(() => buildExceptions(app, 28), [app]);
  const [status, setStatus] = useState<string>("All");
  const [selected, setSelected] = useState(all[0].id);

  const rows = all.filter((e) => status === "All" || e.status === status);
  const active = all.find((e) => e.id === selected) ?? all[0];

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Exception Explorer"
        description="Exceptions raised by the execution engine against this application's control set."
        actions={
          <div className="flex gap-1">
            {STATUSES.map((s) => (
              <button
                key={s}
                onClick={() => setStatus(s)}
                className={cn(
                  "rounded border px-2.5 py-1 text-xs transition-colors",
                  status === s
                    ? "border-primary/40 bg-primary/12 text-primary"
                    : "border-border text-muted-foreground hover:bg-accent",
                )}
              >
                {s}
              </button>
            ))}
          </div>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Panel title="Exceptions" subtitle={`${rows.length} records`}>
          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="sticky top-0 bg-card text-left text-[11px] uppercase tracking-wider text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="px-4 py-2 font-medium">Exception</th>
                  <th className="px-4 py-2 font-medium">Control</th>
                  <th className="px-4 py-2 font-medium">Entity</th>
                  <th className="px-4 py-2 font-medium">Severity</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Impact</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr
                    key={e.id}
                    onClick={() => setSelected(e.id)}
                    className={cn(
                      "cursor-pointer border-b border-border/60 last:border-0 hover:bg-accent/40",
                      e.id === active.id && "bg-primary/8",
                    )}
                  >
                    <td className="px-4 py-2 font-mono text-xs text-primary">{e.id}</td>
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-muted-foreground">{e.controlId}</span>{" "}
                      {e.control}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{e.entity}</td>
                    <td className="px-4 py-2">
                      <Tag value={e.severity} />
                    </td>
                    <td className="px-4 py-2">
                      <Tag value={e.status} />
                    </td>
                    <td className="tabular px-4 py-2 text-right">{e.impact}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Exception detail" subtitle={active.id}>
          <dl className="divide-y divide-border text-sm">
            {[
              ["Control", `${active.controlId} · ${active.control}`],
              ["Rule category", active.category],
              ["Entity", active.entity],
              ["Severity", active.severity],
              ["Status", active.status],
              ["Owner", active.owner],
              ["Financial impact", active.impact],
              ["Age", active.age],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 px-4 py-2.5">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="text-right">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="border-t border-border p-4">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Investigation path
            </p>
            <ol className="mt-2 space-y-1.5">
              {app.investigationChain.map((step, i) => (
                <li key={step} className="flex items-center gap-2 text-xs">
                  <span className="grid size-5 place-items-center rounded-full border border-border font-mono text-[10px] text-muted-foreground">
                    {i + 1}
                  </span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
        </Panel>
      </div>
    </div>
  );
}