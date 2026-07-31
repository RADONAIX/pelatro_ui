import { useMemo, useState } from "react";
import type { AppMetadata, RuleCategory } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES, buildControls, ruleCategories } from "@/lib/assurance/platform-metadata";
import { Panel, SectionHeader, Tag } from "../primitives";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { RuleBuilder } from "../RuleBuilder";
import { useCustomRules, type CustomRule } from "@/lib/assurance/rule-authoring";
import { tableLabel } from "@/lib/assurance/tables";
import { createCase } from "@/lib/cases";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

export function ControlsSection({ app }: { app: AppMetadata }) {
  const all = useMemo(() => buildControls(app, 24), [app]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<RuleCategory | "All">("All");
  const [raising, setRaising] = useState<string | null>(null);
  const { rules, addRule, removeRule, toggleState } = useCustomRules(app.id);

  // Raising is manual: nothing evaluates a rule, so a breach can't trigger this
  // itself. The real evaluator posts the same identity to /api/cases/ingest
  // along with the breach figures this cannot know.
  //
  // Everything but priority and owner is derived rather than asked for — the
  // app is the assurance, the rule's entity is the module, its category is the
  // issue type.
  //
  // `assurance` and `ruleCategory` line up with the case catalog exactly: all
  // eight app names are catalog assurances whose codes equal the app prefixes,
  // and all fifteen rule categories are catalog rule categories.
  //
  // `module` does NOT line up yet. Only Usage Assurance's entities are all
  // catalog modules; the other seven carry entities the catalog has no module
  // for (Rating's "Rated Event"/"Price Plan"/"Product", Network's five, and so
  // on). It is sent as-is because the entity is what the rule actually targets
  // — silently dropping it would hide the mismatch. If the case service rejects
  // unknown modules, reconcile platform-metadata `entities` with the catalog's
  // `modules` rather than mapping here.
  const raiseCase = async (rule: CustomRule) => {
    if (!rule.caseRouting || raising) return;
    setRaising(rule.id);
    try {
      const created = await createCase({
        title: rule.name,
        description:
          rule.description.trim() ||
          `${rule.category} control ${rule.id} breached on ${rule.entity}.`,
        assurance: app.name,
        module: rule.entity,
        ruleId: rule.id,
        ruleName: rule.name,
        ruleCategory: rule.category,
        severity: rule.caseRouting.priority,
        status: "Open",
        owner: rule.caseRouting.owner,
        // A reconciliation rule already names both sides; anything else has no
        // feeds to report rather than a blank pair worth inventing.
        ...(rule.comparison?.table1
          ? {
              sourceFeed: tableLabel(rule.comparison.table1),
              ...(rule.comparison.table2
                ? { targetFeed: tableLabel(rule.comparison.table2) }
                : {}),
            }
          : {}),
        createdBy: "controls",
        // No expected/actual, impact or affected count: nothing has run, and a
        // zero would read as a measured result.
      });
      toast.success(`${created.reference} raised from ${rule.id}`, {
        description: created.owner ? `Assigned to ${created.owner}` : "Unassigned",
      });
    } catch (e) {
      toast.error(`Could not raise a case for ${rule.id}`, {
        description: (e as Error).message,
      });
    } finally {
      setRaising(null);
    }
  };

  const scoped = RULE_CATEGORIES.filter(
    (c) => app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => ruleCategories(r).includes(c)),
  );

  const rows = all.filter(
    (c) =>
      (category === "All" || c.categories.includes(category)) &&
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
            {(["All", ...scoped] as (RuleCategory | "All")[]).map((c) => (
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
                      {r.caseRouting?.raiseCase && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-xs"
                          title="Open a case in Case Management using this rule's routing"
                          disabled={raising === r.id}
                          onClick={() => raiseCase(r)}
                        >
                          {raising === r.id ? "Raising…" : "Raise case"}
                        </Button>
                      )}
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
                    <td className="px-4 py-2 text-muted-foreground">{c.categories.join(" + ")}</td>
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