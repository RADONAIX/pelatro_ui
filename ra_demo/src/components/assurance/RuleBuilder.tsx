import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { AppMetadata, RuleCategory } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES } from "@/lib/assurance/platform-metadata";
import {
  CATEGORY_PARAMS,
  COMPARISON_CATEGORIES,
  emptyCaseRouting,
  emptyComparison,
  type AttrPair,
  type CaseRouting,
  type CustomRule,
  type RuleComparison,
} from "@/lib/assurance/rule-authoring";
import { RULE_TABLES, tableColumns } from "@/lib/assurance/tables";
// Case vocabularies, aliased — this file already has its own SEVERITIES for the
// rule's own severity, which is a narrower set than a case's.
import {
  FINDING_TYPES,
  SEVERITIES as CASE_SEVERITIES,
  STREAMS,
} from "@/lib/casesDemo";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Draft = Omit<CustomRule, "id" | "appId" | "createdAt">;

const SEVERITIES: CustomRule["severity"][] = ["critical", "high", "medium"];
const FREQUENCIES: CustomRule["frequency"][] = ["Real-time", "Hourly", "Daily", "Cycle"];

export function RuleBuilder({
  app,
  onCreate,
}: {
  app: AppMetadata;
  onCreate: (rule: Draft) => void;
}) {
  const scoped = useMemo(
    () =>
      RULE_CATEGORIES.filter(
        (c) => app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => r.category === c),
      ),
    [app],
  );

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<RuleCategory>(scoped[0] ?? RULE_CATEGORIES[0]);
  const [entity, setEntity] = useState(app.entities[0]);
  const [severity, setSeverity] = useState<CustomRule["severity"]>("high");
  const [frequency, setFrequency] = useState<CustomRule["frequency"]>("Daily");
  const [state, setState] = useState<CustomRule["state"]>("Draft");
  const [params, setParams] = useState<Record<string, string>>({});
  // Kept mounted across category changes so switching away and back — or
  // toggling Single/Multiple — doesn't discard a half-built comparison.
  const [comparison, setComparison] = useState<RuleComparison>(emptyComparison);
  const [routing, setRouting] = useState<CaseRouting>(() => emptyCaseRouting("high"));

  const fields = CATEGORY_PARAMS[category];
  const isComparison = COMPARISON_CATEGORIES.has(category);
  const isMultiple = comparison.mode === "Multiple";

  // A comparison rule needs enough to actually run: both tables, at least one
  // complete metric pair, and — when joining two tables — at least one complete
  // key pair. Without a key there is nothing to join on and the four outcomes
  // (matched / mismatch / left-only / right-only) can't be produced.
  const comparisonValid =
    !isComparison ||
    (!!comparison.table1 &&
      (!isMultiple || !!comparison.table2) &&
      comparison.metrics.some((m) => m.left && (!isMultiple || m.right)) &&
      (!isMultiple || comparison.keys.some((k) => k.left && k.right)));

  const valid = name.trim().length > 1 && comparisonValid;

  function reset() {
    setName("");
    setDescription("");
    setCategory(scoped[0] ?? RULE_CATEGORIES[0]);
    setEntity(app.entities[0]);
    setSeverity("high");
    setFrequency("Daily");
    setState("Draft");
    setParams({});
    setComparison(emptyComparison());
    setRouting(emptyCaseRouting("high"));
  }

  function submit() {
    if (!valid) return;
    onCreate({
      name: name.trim(),
      description: description.trim(),
      category,
      entity,
      severity,
      frequency,
      state,
      params,
      // Drop the half-filled other mode so a Single rule never carries a
      // table2/keys that nothing reads.
      ...(isComparison
        ? {
            comparison: isMultiple
              ? {
                  ...comparison,
                  metrics: comparison.metrics.filter((m) => m.left && m.right),
                  keys: comparison.keys.filter((k) => k.left && k.right),
                }
              : {
                  ...comparison,
                  table2: "",
                  metrics: comparison.metrics.filter((m) => m.left).map((m) => ({ ...m, right: "" })),
                  keys: [],
                },
          }
        : {}),
      // Omitted entirely when not raising, so a rule that routes nowhere carries
      // no policy at all rather than a disabled one.
      ...(routing.raiseCase ? { caseRouting: { ...routing, owner: routing.owner.trim() } } : {}),
    });
    reset();
    setOpen(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Plus className="size-4" />
          New rule
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create control rule</DialogTitle>
          <DialogDescription>
            Rules are metadata. Pick a primitive category and the universal rule engine compiles
            the parameters into an executable control for {app.name}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rule-name">Rule name</Label>
              <Input
                id="rule-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Roaming CDR completeness"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Primitive category</Label>
              <Select
                value={category}
                onValueChange={(v) => {
                  setCategory(v as RuleCategory);
                  setParams({});
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RULE_CATEGORIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                      {!scoped.includes(c) && " · out of scope"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="rule-desc">Intent</Label>
            <Textarea
              id="rule-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What leakage or integrity risk does this control detect?"
              rows={2}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Entity scope</Label>
              <Select value={entity} onValueChange={setEntity}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {app.entities.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Severity</Label>
              <Select
                value={severity}
                onValueChange={(v) => {
                  const next = v as CustomRule["severity"];
                  setSeverity(next);
                  // Case priority tracks rule severity until the author edits it
                  // below — one control instead of two saying the same thing.
                  setRouting((r) => ({ ...r, priority: next }));
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Execution frequency</Label>
              <Select
                value={frequency}
                onValueChange={(v) => setFrequency(v as CustomRule["frequency"])}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FREQUENCIES.map((f) => (
                    <SelectItem key={f} value={f}>
                      {f}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {isComparison && (
            <ComparisonEditor value={comparison} onChange={setComparison} />
          )}

          <div className="rounded-md border border-border p-3">
            <p className="mb-3 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {category} parameters
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map((f) => (
                <div key={f.key} className="space-y-1.5">
                  <Label htmlFor={`p-${f.key}`}>{f.label}</Label>
                  <Input
                    id={`p-${f.key}`}
                    value={params[f.key] ?? ""}
                    onChange={(e) => setParams((p) => ({ ...p, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Lifecycle state</Label>
              <Select value={state} onValueChange={(v) => setState(v as CustomRule["state"])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Draft">Draft — not scheduled</SelectItem>
                  <SelectItem value="Active">Active — scheduled for execution</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Case Management</Label>
              <Select
                value={routing.raiseCase ? "yes" : "no"}
                onValueChange={(v) => setRouting((r) => ({ ...r, raiseCase: v === "yes" }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="no">Don&apos;t raise a case</SelectItem>
                  <SelectItem value="yes">Raise a case on breach</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {routing.raiseCase && <CaseRoutingEditor value={routing} onChange={setRouting} />}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!valid} onClick={submit}>
            Create rule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
// ---------------------------------------------------------------------------
// The two-table comparison editor, for categories where the rule compares
// datasets rather than inspecting one (see COMPARISON_CATEGORIES).
//
// Single  — one table checked against itself: attributes only, no keys.
// Multiple — two tables: paired metrics AND paired join keys, both repeatable.
//
// Attributes are chosen from the selected table's real columns, so a rule can
// never reference a column that doesn't exist.
// ---------------------------------------------------------------------------

function ComparisonEditor({
  value,
  onChange,
}: {
  value: RuleComparison;
  onChange: (next: RuleComparison) => void;
}) {
  const isMultiple = value.mode === "Multiple";
  const leftCols = tableColumns(value.table1);
  const rightCols = tableColumns(value.table2);

  const set = (patch: Partial<RuleComparison>) => onChange({ ...value, ...patch });

  const setPair = (field: "metrics" | "keys", i: number, patch: Partial<AttrPair>) =>
    set({ [field]: value[field].map((p, j) => (j === i ? { ...p, ...patch } : p)) } as Partial<RuleComparison>);

  const addPair = (field: "metrics" | "keys") =>
    set({ [field]: [...value[field], { left: "", right: "" }] } as Partial<RuleComparison>);

  const removePair = (field: "metrics" | "keys", i: number) =>
    set({
      [field]: value[field].length > 1 ? value[field].filter((_, j) => j !== i) : value[field],
    } as Partial<RuleComparison>);

  const pairRows = (field: "metrics" | "keys", hint: string) => (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {field === "metrics" ? "Comparison metric" : "Comparison keys"}
        </p>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      </div>

      {value[field].map((pair, i) => (
        <div key={`${field}-${i}`} className="flex items-end gap-2">
          <div className="min-w-0 flex-1 space-y-1.5">
            {i === 0 && <Label className="text-xs">{isMultiple ? "Attribute 1" : "Attributes"}</Label>}
            <ColumnSelect
              columns={leftCols}
              value={pair.left}
              onChange={(v) => setPair(field, i, { left: v })}
            />
          </div>

          {isMultiple && (
            <div className="min-w-0 flex-1 space-y-1.5">
              {i === 0 && <Label className="text-xs">Attribute 2</Label>}
              <ColumnSelect
                columns={rightCols}
                value={pair.right}
                onChange={(v) => setPair(field, i, { right: v })}
              />
            </div>
          )}

          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Remove pair"
            disabled={value[field].length === 1}
            onClick={() => removePair(field, i)}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}

      <Button
        type="button"
        variant="outline"
        size="sm"
        className="w-full border-dashed"
        onClick={() => addPair(field)}
      >
        <Plus className="mr-1.5 size-3.5" />
        Add pair
      </Button>
    </div>
  );

  return (
    <div className="space-y-4 rounded-md border border-border p-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label>Type</Label>
          <Select
            value={value.mode}
            onValueChange={(v) => set({ mode: v as RuleComparison["mode"] })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Single">Single — one table</SelectItem>
              <SelectItem value="Multiple">Multiple — two tables</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label>Table 1</Label>
          <TableSelect value={value.table1} onChange={(v) => set({ table1: v })} />
        </div>

        {isMultiple && (
          <div className="space-y-1.5">
            <Label>Table 2</Label>
            <TableSelect value={value.table2} onChange={(v) => set({ table2: v })} />
          </div>
        )}
      </div>

      {!isMultiple && (
        <p className="text-[11px] text-muted-foreground">
          Table 2 and comparison keys aren&apos;t saved for Single rules.
        </p>
      )}

      {pairRows(
        "metrics",
        isMultiple
          ? "The measured values compared between the two tables."
          : "The attributes checked within the table.",
      )}

      {isMultiple && pairRows("keys", "The attributes used to join records across the two tables.")}
    </div>
  );
}

function TableSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger>
        <SelectValue placeholder="Select a table…" />
      </SelectTrigger>
      <SelectContent>
        {RULE_TABLES.map((t) => (
          <SelectItem key={t.id} value={t.id}>
            {t.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ColumnSelect({
  columns,
  value,
  onChange,
}: {
  columns: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={columns.length === 0}>
      <SelectTrigger>
        <SelectValue placeholder={columns.length ? "Select an attribute…" : "Select a table first"} />
      </SelectTrigger>
      <SelectContent>
        {columns.map((c) => (
          <SelectItem key={c} value={c}>
            {c}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ---------------------------------------------------------------------------
// Case routing — what Case Management should do when this rule breaches.
//
// Nothing evaluates a CustomRule, so this is policy, not behaviour: the Controls
// table reads it for its manual "Raise case" action, and a real evaluator would
// read exactly the same block. Every vocabulary here comes from casesDemo so a
// rule-raised case is indistinguishable from a hand-raised one.
// ---------------------------------------------------------------------------

function CaseRoutingEditor({
  value,
  onChange,
}: Readonly<{
  value: CaseRouting;
  onChange: (next: CaseRouting) => void;
}>) {
  const set = (patch: Partial<CaseRouting>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Case routing
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Priority</Label>
          <Select
            value={value.priority}
            onValueChange={(v) => set({ priority: v as CaseRouting["priority"] })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CASE_SEVERITIES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="case-owner">Assign to</Label>
          <Input
            id="case-owner"
            value={value.owner}
            onChange={(e) => set({ owner: e.target.value })}
            placeholder="Leave blank to raise unassigned"
          />
        </div>

        <div className="space-y-1.5">
          <Label>Finding type</Label>
          <Select value={value.findingType} onValueChange={(v) => set({ findingType: v })}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FINDING_TYPES.map((f) => (
                <SelectItem key={f.key} value={f.key}>
                  {f.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label>Stream</Label>
          <Select value={value.stream} onValueChange={(v) => set({ stream: v })}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STREAMS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Cases open in Assurance Cases under Operations. No evaluator runs this rule yet — raise one
        from the Controls table to see it end to end.
      </p>
    </div>
  );
}
