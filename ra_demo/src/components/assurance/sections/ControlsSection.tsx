import { useMemo, useState } from "react";
import type { AppMetadata } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES, buildControls } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader, Tag } from "../primitives";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { RuleBuilder } from "../RuleBuilder";
import { useCustomRules } from "@/lib/assurance/rule-authoring";
import { cn } from "@/lib/utils";

export function ControlsSection({ app }: { app: AppMetadata }) {
  const all = useMemo(() => buildControls(app, 24), [app]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("All");
  const { rules, addRule, removeRule, toggleState } = useCustomRules(app.id);

  const scoped = RULE_CATEGORIES.filter((c) => app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => r.category === c));

  const rows = all.filter(
    (c) =>
      (category === "All" || c.category === category) &&
      (c.name.toLowerCase().includes(query.toLowerCase()) ||
        c.id.toLowerCase().includes(query.toLowerCase())),
  );

  const customRows = rules.filter(
    (r) =>
      (category === "All" || r.category === category) &&
      (r.name.toLowerCase().includes(query.toLowerCase()) ||
        r.id.toLowerCase().includes(query.toLowerCase())),
  );

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Rule Explorer"
        description={`${app.controlCount} controls provisioned for ${app.name} · range ${app.controlRange}`}
        actions={
          <RuleBuilder app={app} onCreate={(rule) => addRule(rule, app.prefix)} />
        }
      />

      <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
        <Panel title="Rule categories in scope">
          <div className="p-2">
            {["All", ...scoped].map((c) => (
              <button
                key={c}
                onClick={() => setCategory(c)}
                className={cn(
                  "block w-full rounded px-2.5 py-1.5 text-left text-sm transition-colors",
                  category === c ? "bg-primary/12 text-primary" : "text-muted-foreground hover:bg-accent",
                )}
              >
                {c}
              </button>
            ))}
          </div>
          <div className="border-t border-border p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Hidden by metadata
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {RULE_CATEGORIES.filter((c) => !scoped.includes(c)).join(", ")}
            </p>
          </div>
        </Panel>

        <Panel
          title="Controls"
          subtitle={`${rows.length + customRows.length} of ${all.length + rules.length} shown`}
        >
          <div className="border-b border-border p-3">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search control id or rule name…"
              className="h-8 max-w-sm"
            />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wider text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="px-4 py-2 font-medium">ID</th>
                  <th className="px-4 py-2 font-medium">Rule</th>
                  <th className="px-4 py-2 font-medium">Category</th>
                  <th className="px-4 py-2 font-medium">Entity</th>
                  <th className="px-4 py-2 font-medium">Severity</th>
                  <th className="px-4 py-2 font-medium">Frequency</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Last run</th>
                </tr>
              </thead>
              <tbody>
                {customRows.map((r) => (
                  <tr key={r.id} className="border-b border-border/60 bg-primary/5 hover:bg-accent/40">
                    <td className="px-4 py-2 font-mono text-xs text-primary">{r.id}</td>
                    <td className="px-4 py-2">
                      {r.name}
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                        authored
                      </span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{r.category}</td>
                    <td className="px-4 py-2 text-muted-foreground">{r.entity}</td>
                    <td className="px-4 py-2">
                      <Tag value={r.severity} />
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{r.frequency}</td>
                    <td className="px-4 py-2">
                      <Tag value={r.state} />
                    </td>
                    <td className="px-4 py-2 text-right text-xs">
                      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => toggleState(r.id)}>
                        {r.state === "Active" ? "Pause" : "Activate"}
                      </Button>
                      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs text-destructive" onClick={() => removeRule(r.id)}>
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.map((c) => (
                  <tr key={c.id} className="border-b border-border/60 last:border-0 hover:bg-accent/40">
                    <td className="px-4 py-2 font-mono text-xs text-primary">{c.id}</td>
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2 text-muted-foreground">{c.category}</td>
                    <td className="px-4 py-2 text-muted-foreground">{c.entity}</td>
                    <td className="px-4 py-2">
                      <Tag value={c.severity} />
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">{c.frequency}</td>
                    <td className="px-4 py-2">
                      <Tag value={c.status} />
                    </td>
                    <td className="px-4 py-2 text-right text-xs text-muted-foreground">{c.lastRun}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  );
}