import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { AppMetadata, RuleCategory } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES, ruleCategories } from "@/lib/assurance/platform-metadata";
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
import {
  fetchAssuranceTables,
  fetchTableColumns,
  type AssuranceTable,
} from "@/lib/assurance/metadata-api";
// Case priority vocabulary, aliased — this file already has its own SEVERITIES
// for the rule's own severity, which is a narrower set than a case's.
import { SEVERITIES as CASE_SEVERITIES } from "@/lib/cases";
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Draft = Omit<CustomRule, "id" | "appId" | "createdAt">;

const SEVERITIES: CustomRule["severity"][] = ["critical", "high", "medium"];
const FREQUENCIES: CustomRule["frequency"][] = ["Real-time", "Hourly", "Daily", "Cycle"];

export function RuleBuilder({
  app,
  onSubmit,
  rule,
  trigger,
}: {
  app: AppMetadata;
  /**
   * Persist the draft. Async and awaited: the dialog stays open (and the button
   * stays busy) until the write lands, so a rejected save doesn't close over
   * the author's work and lose it.
   */
  onSubmit: (rule: Draft) => Promise<unknown>;
  /**
   * The rule being edited. Absent = authoring a new one. Present = every field
   * is seeded from it, and the dialog titles/labels switch to editing.
   */
  rule?: CustomRule;
  /** Replaces the default "New rule" button — the Controls table passes Edit. */
  trigger?: ReactNode;
}) {
  const editing = !!rule;
  const scoped = useMemo(
    () =>
      RULE_CATEGORIES.filter(
        (c) => app.ruleTypes.includes(c) || app.ruleLibrary.some((r) => ruleCategories(r).includes(c)),
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
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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

  // Seeds every field from the rule under edit, or back to defaults when
  // authoring. Runs on close as well as on open, so a cancelled edit leaves no
  // residue in the next dialog.
  const reset = useCallback(() => {
    setName(rule?.name ?? "");
    setDescription(rule?.description ?? "");
    setCategory(rule?.category ?? scoped[0] ?? RULE_CATEGORIES[0]);
    setEntity(rule?.entity ?? app.entities[0]);
    setSeverity(rule?.severity ?? "high");
    setFrequency(rule?.frequency ?? "Daily");
    setState(rule?.state ?? "Draft");
    setParams(rule?.params ?? {});
    // A rule saved before the comparison shape existed, or one of a
    // non-comparison category, has no block to restore — fall back to an empty
    // one rather than leaving the previous rule's tables on screen.
    setComparison(rule?.comparison ?? emptyComparison());
    setRouting(rule?.caseRouting ?? emptyCaseRouting(rule?.severity ?? "high"));
    setSaveError(null);
  }, [rule, scoped, app.entities]);

  // Re-seed when the dialog is handed a different rule (each table row renders
  // its own builder, but a list refresh replaces the object identity).
  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  async function submit() {
    if (!valid || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onSubmit({
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
                    metrics: comparison.metrics
                      .filter((m) => m.left)
                      .map((m) => ({ ...m, right: "" })),
                    keys: [],
                  },
            }
          : {}),
        // Omitted entirely when not raising, so a rule that routes nowhere
        // carries no policy at all rather than a disabled one.
        ...(routing.raiseCase
          ? { caseRouting: { ...routing, owner: routing.owner.trim() } }
          : {}),
      });
      setOpen(false);
    } catch (e) {
      // Stay open with the author's work intact — the write is what failed, not
      // the input, so re-typing it would be the wrong remedy.
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
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
        {trigger ?? (
          <Button size="sm" className="gap-1.5">
            <Plus className="size-4" />
            New rule
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {editing ? `Edit control rule ${rule.id}` : "Create control rule"}
          </DialogTitle>
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
            <ComparisonEditor assurance={app.id} value={comparison} onChange={setComparison} />
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

        {saveError && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            Could not save this rule: {saveError}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" size="sm" disabled={saving} onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!valid || saving} onClick={submit}>
            {saving ? "Saving…" : editing ? "Save changes" : "Create rule"}
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
  assurance,
  value,
  onChange,
}: {
  assurance: string;
  value: RuleComparison;
  onChange: (next: RuleComparison) => void;
}) {
  const isMultiple = value.mode === "Multiple";
  const [tables, setTables] = useState<AssuranceTable[]>([]);
  const [leftCols, setLeftCols] = useState<string[]>([]);
  const [rightCols, setRightCols] = useState<string[]>([]);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [leftLoading, setLeftLoading] = useState(false);
  const [rightLoading, setRightLoading] = useState(false);
  const [metadataError, setMetadataError] = useState("");

  const set = (patch: Partial<RuleComparison>) => onChange({ ...value, ...patch });

  useEffect(() => {
    let active = true;
    setTables([]);
    setTablesLoading(true);
    setMetadataError("");
    fetchAssuranceTables(assurance)
      .then((next) => {
        if (active) setTables(next);
      })
      .catch(() => {
        if (active) setMetadataError("Could not load tables from RA_Backend.");
      })
      .finally(() => {
        if (active) setTablesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [assurance]);

  useEffect(() => {
    let active = true;
    setLeftCols([]);
    setLeftLoading(!!value.table1);
    if (value.table1) {
      fetchTableColumns(assurance, value.table1)
        .then((next) => {
          if (active) setLeftCols(next);
        })
        .catch(() => {
          if (active) setMetadataError("Could not load the columns for Table 1.");
        })
        .finally(() => {
          if (active) setLeftLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [assurance, value.table1]);

  useEffect(() => {
    let active = true;
    setRightCols([]);
    setRightLoading(!!value.table2);
    if (value.table2) {
      fetchTableColumns(assurance, value.table2)
        .then((next) => {
          if (active) setRightCols(next);
        })
        .catch(() => {
          if (active) setMetadataError("Could not load the columns for Table 2.");
        })
        .finally(() => {
          if (active) setRightLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [assurance, value.table2]);

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
              loading={leftLoading}
              value={pair.left}
              onChange={(v) => setPair(field, i, { left: v })}
            />
          </div>

          {isMultiple && (
            <div className="min-w-0 flex-1 space-y-1.5">
              {i === 0 && <Label className="text-xs">Attribute 2</Label>}
              <ColumnSelect
                columns={rightCols}
                loading={rightLoading}
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
          <TableSelect
            tables={tables}
            loading={tablesLoading}
            value={value.table1}
            onChange={(v) =>
              set({
                table1: v,
                metrics: value.metrics.map((pair) => ({ ...pair, left: "" })),
                keys: value.keys.map((pair) => ({ ...pair, left: "" })),
              })
            }
          />
        </div>

        {isMultiple && (
          <div className="space-y-1.5">
            <Label>Table 2</Label>
            <TableSelect
              tables={tables}
              loading={tablesLoading}
              value={value.table2}
              onChange={(v) =>
                set({
                  table2: v,
                  metrics: value.metrics.map((pair) => ({ ...pair, right: "" })),
                  keys: value.keys.map((pair) => ({ ...pair, right: "" })),
                })
              }
            />
          </div>
        )}
      </div>

      {metadataError && <p className="text-[11px] text-destructive">{metadataError}</p>}

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

function TableSelect({
  tables,
  loading,
  value,
  onChange,
}: {
  tables: AssuranceTable[];
  loading: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  const grouped = tables.reduce<Record<string, AssuranceTable[]>>((groups, table) => {
    (groups[table.database_name] ??= []).push(table);
    return groups;
  }, {});

  return (
    <Select value={value} onValueChange={onChange} disabled={loading || tables.length === 0}>
      <SelectTrigger>
        <SelectValue placeholder={loading ? "Loading tables…" : "Select a table…"} />
      </SelectTrigger>
      <SelectContent>
        {Object.entries(grouped).map(([database, databaseTables]) => (
          <SelectGroup key={database}>
            <SelectLabel className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {database}
            </SelectLabel>
            {databaseTables.map((table) => (
              <SelectItem key={table.id} value={table.id}>
                {table.label}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}

function ColumnSelect({
  columns,
  loading,
  value,
  onChange,
}: {
  columns: string[];
  loading: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={loading || columns.length === 0}>
      <SelectTrigger>
        <SelectValue
          placeholder={
            loading
              ? "Loading attributes…"
              : columns.length
                ? "Select an attribute…"
                : "Select a table first"
          }
        />
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
// read exactly the same block. Priority uses the case service's own severity
// list, so a rule-raised case is indistinguishable from a hand-raised one.
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
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Assurance, module and issue type come from this rule and its app. Nothing evaluates the rule
        yet — raise a case from the Controls table to see it end to end.
      </p>
    </div>
  );
}
