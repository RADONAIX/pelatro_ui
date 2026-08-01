import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { AppMetadata, RuleCategory } from "@/lib/assurance/platform-metadata";
import { RULE_CATEGORIES, ruleCategories } from "@/lib/assurance/platform-metadata";
import {
  CATEGORY_PARAMS,
  COMPARISON_CATEGORIES,
  DEFAULT_EXECUTION_TIME,
  SINGLE_TABLE_CATEGORIES,
  emptyCaseRouting,
  emptyComparison,
  type AttrPair,
  type CaseRouting,
  type CustomRule,
  type RuleComparison,
} from "@/lib/assurance/rule-authoring";
import {
  fetchAssuranceTables,
  fetchFileLogColumns,
  fetchFileLogs,
  fetchTableColumns,
  type AssuranceTable,
} from "@/lib/assurance/metadata-api";
// Case priority vocabulary, aliased — this file already has its own SEVERITIES
// for the rule's own severity, which is a narrower set than a case's.
import { FALLBACK_CATALOG, SEVERITIES as CASE_SEVERITIES, fetchCatalog } from "@/lib/cases";
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
import { SearchableSelect } from "@/components/ui-kit/SearchableSelect";
import { cn } from "@/lib/utils";

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
  const [entity, setEntity] = useState("");
  /**
   * Entity scope IS the module a raised case is filed under, so the options
   * have to be the modules the case service knows for THIS assurance.
   *
   * platform-metadata's `entities` are a different vocabulary written for the
   * Controls table, and most of them are not modules: Charging offered "OCS
   * Account", "Reservation" and "Top-up", none of which the catalog recognises,
   * so a case raised from such a rule arrived with no module at all. Seeded
   * with the app's entities so the control is never empty, then replaced by the
   * catalog's list — per assurance — as soon as it answers.
   */
  const [modules, setModules] = useState<string[]>(app.entities);
  const [severity, setSeverity] = useState<CustomRule["severity"]>("high");
  const [frequency, setFrequency] = useState<CustomRule["frequency"]>("Daily");
  const [executionTime, setExecutionTime] = useState(DEFAULT_EXECUTION_TIME);
  const [state, setState] = useState<CustomRule["state"]>("Draft");
  const [params, setParams] = useState<Record<string, string>>({});
  // Kept mounted across category changes so switching away and back — or
  // toggling Single/Multiple — doesn't discard a half-built comparison.
  const [comparison, setComparison] = useState<RuleComparison>(emptyComparison);
  const [routing, setRouting] = useState<CaseRouting>(() => emptyCaseRouting("high"));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Falls back to the bundled catalog when the case service is unreachable, so
  // the options stay correct offline rather than reverting to the entities.
  useEffect(() => {
    let cancelled = false;
    fetchCatalog()
      .catch(() => FALLBACK_CATALOG)
      .then((catalog) => {
        if (cancelled) return;
        const known = catalog.assurances.find((a) => a.name === app.name)?.modules;
        if (known?.length) setModules(known);
      });
    return () => {
      cancelled = true;
    };
  }, [app.name]);

  /**
   * A rule authored before this list was catalog-driven can carry an entity the
   * catalog no longer offers. Keep it as an option while editing: silently
   * re-pointing someone's rule at a different module on open would be worse
   * than showing the value they chose.
   */
  const entityOptions = useMemo(
    () =>
      rule?.entity && !modules.includes(rule.entity) ? [rule.entity, ...modules] : modules,
    [modules, rule?.entity],
  );

  /**
   * The categories offered, for the same reason and by the same rule as
   * entityOptions above.
   *
   * Every category used to be listed, with the ones this assurance doesn't
   * author suffixed "· out of scope" — a dozen unselectable-in-practice options
   * in front of the author for no gain, when the Rule Explorer already names
   * them under "Hidden by metadata". A rule saved under a category that has
   * since left scope keeps it while editing.
   */
  const categoryOptions = useMemo(
    () =>
      rule?.category && !scoped.includes(rule.category) ? [rule.category, ...scoped] : scoped,
    [scoped, rule?.category],
  );

  // The catalog answers after the first render, so the seeded value can be one
  // this assurance doesn't offer. Correct it to the first real option — but
  // never overwrite a choice that IS valid, including the edited rule's own.
  useEffect(() => {
    if (!entityOptions.length) return;
    setEntity((current) => (current && entityOptions.includes(current) ? current : entityOptions[0]));
  }, [entityOptions]);

  const fields = CATEGORY_PARAMS[category];
  const isComparison = COMPARISON_CATEGORIES.has(category);
  // Sequence and Duplicate run over ONE table — a file log — rather than
  // comparing two, so they get the file-log picker instead of the free-text
  // parameter boxes. The backend compiles them from the same three values.
  const isFileLogRule = FILE_LOG_CATEGORIES.has(category);
  // Threshold runs over ONE source table, so it gets a table + attribute picker
  // instead of free-text boxes (see SINGLE_TABLE_CATEGORIES).
  const isSingleTable = SINGLE_TABLE_CATEGORIES.has(category);
  const isMultiple = comparison.mode === "Multiple";
  // Both dedicated editors above supply the whole parameter set themselves, so
  // the generic box has nothing left to render — and an empty bordered panel
  // headed "Threshold parameters" reads as a section that failed to load.
  const flatFields = isFileLogRule || isSingleTable ? [] : fields;

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

  // A file-log rule needs the table and the attribute carrying the counter;
  // "Partition by" is genuinely optional (without it the series is derived from
  // the value itself), so it is not required here.
  const fileLogValid =
    !isFileLogRule || (!!params.table && !!params.sequenceField);

  // A frequency is always chosen, so a time is always required with it — an
  // empty or malformed one would silently become midnight on the server.
  const executionTimeValid = /^([01]\d|2[0-3]):[0-5]\d$/.test(executionTime);

  // Only meaningful when a case is actually being raised; a rule that raises
  // nothing is not blocked by a threshold it will never consult.
  const breachThresholdValid =
    !routing.raiseCase ||
    (Number.isInteger(routing.breachThreshold) && routing.breachThreshold >= 1);

  // A threshold needs all three: which table, which attribute of it, and the
  // limit that attribute is judged against. None of them has a sensible default.
  const singleTableValid =
    !isSingleTable || (!!params.table && !!params.attribute && !!params.value?.trim());

  // The server requires an entity, so guard the brief window before the catalog
  // answers rather than letting the save come back 422.
  const valid =
    name.trim().length > 1 &&
    !!entity &&
    comparisonValid &&
    fileLogValid &&
    singleTableValid &&
    executionTimeValid &&
    breachThresholdValid;

  // Seeds every field from the rule under edit, or back to defaults when
  // authoring. Runs on close as well as on open, so a cancelled edit leaves no
  // residue in the next dialog.
  const reset = useCallback(() => {
    setName(rule?.name ?? "");
    setDescription(rule?.description ?? "");
    setCategory(rule?.category ?? scoped[0] ?? RULE_CATEGORIES[0]);
    // "" rather than a guess: the effect above fills in the first module the
    // catalog offers for this assurance.
    setEntity(rule?.entity ?? "");
    setSeverity(rule?.severity ?? "high");
    setFrequency(rule?.frequency ?? "Daily");
    setExecutionTime(rule?.executionTime || DEFAULT_EXECUTION_TIME);
    setState(rule?.state ?? "Draft");
    setParams(rule?.params ?? {});
    // A rule saved before the comparison shape existed, or one of a
    // non-comparison category, has no block to restore — fall back to an empty
    // one rather than leaving the previous rule's tables on screen.
    setComparison(rule?.comparison ?? emptyComparison());
    setRouting(rule?.caseRouting ?? emptyCaseRouting(rule?.severity ?? "high"));
    setSaveError(null);
  }, [rule, scoped]);

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
        executionTime,
        state,
        // A rule edited from Threshold's old measure/operator/limit shape — or
        // switched here from another category — still carries those keys in
        // state. Save only what this category declares, so the stored rule
        // matches what the author actually filled in.
        params: isSingleTable
          ? Object.fromEntries(fields.map((f) => [f.key, params[f.key] ?? ""]))
          : params,
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
                  {categoryOptions.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
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

          <div className="grid gap-3 sm:grid-cols-2">
            <div >
              <Label>Entity scope</Label>
              <Select value={entity} onValueChange={setEntity}>
                <SelectTrigger>
                  <SelectValue placeholder="Loading modules…" />
                </SelectTrigger>
                <SelectContent>
                  {entityOptions.map((e) => (
                    <SelectItem key={e} value={e}>
                      {e}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div >
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
            {/* Frequency and time are one setting read together — "Daily at
                02:00" — so they share the third cell rather than the time
                wrapping onto a row of its own. */}
            
          </div>
<div>
  <div className="grid grid-cols-2 gap-2">
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
              <div className="space-y-1.5">
                <Label htmlFor="rule-exec-time">Execution time</Label>
                <Input
                  id="rule-exec-time"
                  type="time"
                  step={60}
                  value={executionTime}
                  onChange={(e) => setExecutionTime(e.target.value)}
                  aria-invalid={!executionTimeValid}
                  className={cn(!executionTimeValid && "border-destructive")}
                />
              </div>
            </div>
</div>
          {/* Real-time polls continuously, so a clock time would be a setting
              that does nothing — say so rather than leaving the field looking
              effective. */}
          {frequency === "Real-time" && (
            <p className="-mt-1 text-xs text-muted-foreground">
              Real-time rules poll continuously; execution time is not used.
            </p>
          )}

          {isComparison && (
            <ComparisonEditor assurance={app.id} value={comparison} onChange={setComparison} />
          )}

          {isFileLogRule && (
            <FileLogEditor category={category} value={params} onChange={setParams} />
          )}

          {isSingleTable && (
            <SingleTableEditor
              assurance={app.id}
              category={category}
              fields={fields}
              value={params}
              onChange={setParams}
            />
          )}

          {flatFields.length > 0 && (
            <div className="rounded-md border border-border p-3">
              <p className="mb-3 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                {category} parameters
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                {flatFields.map((f) => (
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
          )}

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

// ---------------------------------------------------------------------------
// Single-table editor — the authoring surface for Threshold (see
// SINGLE_TABLE_CATEGORIES).
//
// A threshold is "this attribute of this table, against this limit". The table
// and attribute come from the assurance's live metadata, so a rule can only
// ever name a column that exists; the limit is the one genuinely free value.
// ---------------------------------------------------------------------------

function SingleTableEditor({
  assurance,
  category,
  fields,
  value,
  onChange,
}: {
  assurance: string;
  category: RuleCategory;
  /** The category's declared parameters — supplies the labels and hints. */
  fields: { key: string; label: string; placeholder: string }[];
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const [tables, setTables] = useState<AssuranceTable[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [tablesLoading, setTablesLoading] = useState(true);
  const [columnsLoading, setColumnsLoading] = useState(false);
  const [metadataError, setMetadataError] = useState("");

  const table = value.table ?? "";
  const hint = (key: string) => fields.find((f) => f.key === key)?.placeholder ?? "";

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

  // Attributes are the selected table's real columns, refetched whenever the
  // table changes.
  useEffect(() => {
    let active = true;
    setColumns([]);
    setColumnsLoading(!!table);
    if (table) {
      fetchTableColumns(assurance, table)
        .then((next) => {
          if (active) setColumns(next);
        })
        .catch(() => {
          if (active) setMetadataError("Could not load the columns for the selected table.");
        })
        .finally(() => {
          if (active) setColumnsLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [assurance, table]);

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {category} source
      </p>

      <div className="space-y-1.5">
        <Label>Table</Label>
        <TableSelect
          tables={tables}
          loading={tablesLoading}
          value={table}
          // The attribute belongs to the old table, so it is cleared rather
          // than left pointing at a column the new one may not have.
          onChange={(v) => onChange({ ...value, table: v, attribute: "" })}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Attribute</Label>
          <ColumnSelect
            columns={columns}
            loading={columnsLoading}
            value={value.attribute ?? ""}
            onChange={(v) => onChange({ ...value, attribute: v })}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="single-table-value">Value</Label>
          <Input
            id="single-table-value"
            value={value.value ?? ""}
            onChange={(e) => onChange({ ...value, value: e.target.value })}
            placeholder={hint("value")}
          />
        </div>
      </div>

      {metadataError && <p className="text-xs text-destructive">{metadataError}</p>}

      <p className="text-xs leading-relaxed text-muted-foreground">
        Flags rows where the selected attribute breaches the value — e.g.{" "}
        {hint("attribute") || "the attribute"} on {hint("table") || "the table"}.
      </p>
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
  // Grouping by database is kept from the Select this replaced — the same
  // schema name exists in more than one database, so the heading is what tells
  // two identically-named tables apart.
  const options = useMemo(
    () =>
      tables.map((table) => ({
        value: table.id,
        label: table.label,
        group: table.database_name,
        // Lets a search hit the schema and the database, not just the label.
        keywords: `${table.database_name} ${table.schema_name} ${table.table_name}`,
      })),
    [tables],
  );

  return (
    <SearchableSelect
      options={options}
      value={value}
      onChange={onChange}
      disabled={loading || tables.length === 0}
      placeholder={loading ? "Loading tables…" : "Select a table…"}
      searchPlaceholder="Search tables…"
      emptyLabel="No tables found"
    />
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
  // Columns arrive only after a table is chosen, so the empty state stays
  // "Select a table first" rather than "no results".
  const options = useMemo(() => columns.map((c) => ({ value: c, label: c })), [columns]);

  return (
    <SearchableSelect
      options={options}
      value={value}
      onChange={onChange}
      disabled={loading || columns.length === 0}
      placeholder={
        loading
          ? "Loading attributes…"
          : columns.length
            ? "Select an attribute…"
            : "Select a table first"
      }
      searchPlaceholder="Search attributes…"
      emptyLabel="No attributes found"
    />
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

  const thresholdValid =
    Number.isInteger(value.breachThreshold) && value.breachThreshold >= 1;
  const displayThreshold = thresholdValid ? value.breachThreshold : 1;

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
          <Label htmlFor="case-threshold">Breach threshold</Label>
          <Input
            id="case-threshold"
            type="number"
            min={1}
            step={1}
            value={Number.isFinite(value.breachThreshold) ? value.breachThreshold : ""}
            onChange={(e) => {
              // Kept as a number so the parent's Number.isInteger check is
              // meaningful; an empty box parses to NaN, which fails validation
              // and shows the error rather than silently becoming 1.
              const next = e.target.value === "" ? Number.NaN : Number(e.target.value);
              set({ breachThreshold: next });
            }}
            aria-invalid={!thresholdValid}
            className={cn(!thresholdValid && "border-destructive")}
          />
        </div>
      </div>

      {!thresholdValid && (
        <p className="text-[11px] text-destructive">
          Breach threshold must be a whole number of 1 or more.
        </p>
      )}

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        A case is raised only when a run produces at least {displayThreshold} breached row
        {displayThreshold === 1 ? "" : "s"} — a mismatch, a record missing from either side, or a
        sequence gap. Assurance, module and issue type come from this rule and its app.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// File-log editor — the authoring surface for Sequence and Duplicate rules.
//
// These run over ONE table: an AIR/SDP raw or processed file log. The author
// picks the log, the attribute whose values carry the counter, and optionally
// what that counter runs within.
//
// The counter usually isn't a column of its own — it sits inside a filename
// among a stream id, a node number and a timestamp. Which of those is the
// counter is inferred by the backend from the real values at compile time, so
// nothing here has to ask for a regex or an offset.
// ---------------------------------------------------------------------------

/** Categories authored against a file log rather than a pair of tables. */
export const FILE_LOG_CATEGORIES: ReadonlySet<RuleCategory> = new Set<RuleCategory>([
  "Sequence",
  "Duplicate",
]);

function FileLogEditor({
  category,
  value,
  onChange,
}: {
  category: RuleCategory;
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const [tables, setTables] = useState<AssuranceTable[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [loadingColumns, setLoadingColumns] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchFileLogs()
      .then((t) => !cancelled && setTables(t))
      .catch(() => !cancelled && setTables([]));
    return () => {
      cancelled = true;
    };
  }, []);

  const table = value.table ?? "";

  // Attributes come from the selected log, so a rule can never name a column
  // that isn't there.
  useEffect(() => {
    if (!table) {
      setColumns([]);
      return;
    }
    let cancelled = false;
    setLoadingColumns(true);
    fetchFileLogColumns(table)
      .then((c) => !cancelled && setColumns(c))
      .catch(() => !cancelled && setColumns([]))
      .finally(() => !cancelled && setLoadingColumns(false));
    return () => {
      cancelled = true;
    };
  }, [table]);

  const set = (key: string, next: string) => onChange({ ...value, [key]: next });

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {category === "Duplicate" ? "Duplicate source" : "Sequence source"}
      </p>

      <div className="space-y-1.5">
        <Label>File log</Label>
        <SearchableSelect
          options={tables.map((t) => ({ value: t.id, label: t.label }))}
          value={table}
          onChange={(v) =>
            // Changing the log invalidates the attributes chosen from the old
            // one, so they are cleared rather than left pointing at columns
            // that may not exist here.
            onChange({ ...value, table: v, sequenceField: "", partitionBy: "" })
          }
          placeholder="Pick an AIR or SDP file log"
          searchPlaceholder="Search file logs…"
          emptyLabel="No file logs found"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>
            {category === "Duplicate" ? "Attribute to check" : "Sequence attribute"}
          </Label>
          <SearchableSelect
            options={columns.map((c) => ({ value: c, label: c }))}
            value={value.sequenceField ?? ""}
            onChange={(v) => set("sequenceField", v)}
            disabled={!table || loadingColumns}
            placeholder={
              !table ? "Pick a file log first" : loadingColumns ? "Loading…" : "e.g. filename"
            }
            searchPlaceholder="Search attributes…"
            emptyLabel="No attributes found"
          />
        </div>

        <div className="space-y-1.5">
          <Label>Partition by (optional)</Label>
          <SearchableSelect
            options={[
              { value: NONE_VALUE, label: "Derive from the value itself" },
              ...columns.map((c) => ({ value: c, label: c })),
            ]}
            value={value.partitionBy || NONE_VALUE}
            onChange={(v) => set("partitionBy", v === NONE_VALUE ? "" : v)}
            disabled={!table || loadingColumns}
            placeholder="Derive from the value itself"
            searchPlaceholder="Search attributes…"
            emptyLabel="No attributes found"
          />
        </div>
      </div>

      <p className="text-xs leading-relaxed text-muted-foreground">
        {category === "Duplicate"
          ? "Reports every value that appears more than once in the selected log."
          : "Reports each value in the series and flags where it jumps — " +
            "…_0000_, …_0001_, …_0003_ marks 0002 missing. The counter is found " +
            "inside the value automatically; set Partition by when one log holds " +
            "several independent series (per node, for example)."}
      </p>
    </div>
  );
}

/** Radix Select forbids an empty item value, so "no partition" needs a token. */
const NONE_VALUE = "__none__";
