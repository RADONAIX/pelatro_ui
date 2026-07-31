import { useMemo } from "react";
import { GitBranch, Plus, Trash2, X } from "lucide-react";
import { Select } from "@/components/ui-kit/Select";
import { MultiSelect } from "@/components/ui-kit/MultiSelect";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import {
  useReferenceOptions,
  useRuleAttributes,
  useOperators,
} from "@/lib/rating/hooks";
import type {
  Condition,
  OperatorSpec,
  RuleAttribute,
} from "@/lib/rating/types";

// ---------------------------------------------------------------------------
// Visual condition builder.
//
// Everything it offers comes from the backend's rule vocabulary
// (/meta/attributes, /meta/operators): the attribute list, which operators each
// attribute accepts, how many values each operator takes, and which catalogue
// supplies a REFERENCE attribute's options. That is deliberate — the builder can
// never present a predicate the rating engine cannot execute.
// ---------------------------------------------------------------------------

export interface ConditionRow extends Condition {
  /** Stable client key; conditions have no id until they're saved. */
  _key: string;
}

export function newConditionRow(seed?: Partial<Condition>): ConditionRow {
  return {
    _key:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `c-${Math.random().toString(36).slice(2)}`,
    attribute: seed?.attribute ?? "",
    operator: seed?.operator ?? "EQUALS",
    values: seed?.values ?? [],
    group_index: seed?.group_index ?? 0,
    negate: seed?.negate ?? false,
  };
}

function ValueInput({
  attribute,
  operator,
  values,
  onChange,
  invalid,
}: {
  attribute: RuleAttribute | undefined;
  operator: OperatorSpec | undefined;
  values: unknown[];
  onChange: (values: unknown[]) => void;
  invalid?: boolean;
}) {
  const t = useT();
  const { options: refOptions, isLoading: refLoading } = useReferenceOptions(
    attribute?.data_type === "REFERENCE" ? attribute.reference : null,
  );
  const border = invalid ? "border-destructive" : "border-border";

  if (!attribute) {
    return (
      <div className="h-9 flex items-center px-3 text-sm text-muted-foreground/60 border border-dashed border-border rounded-lg">
        {t("Pick an attribute first")}
      </div>
    );
  }

  // EXISTS / NOT_EXISTS take no operand at all.
  if (operator && operator.max_values === 0) {
    return (
      <div className="h-9 flex items-center px-3 text-sm text-muted-foreground border border-dashed border-border rounded-lg">
        {t("No value needed")}
      </div>
    );
  }

  const multi = operator
    ? operator.max_values === null || operator.max_values > 1
    : false;
  const selectOptions =
    attribute.data_type === "REFERENCE"
      ? refOptions
      : attribute.data_type === "ENUM"
        ? attribute.values.map((v) => ({ value: v, label: v }))
        : attribute.data_type === "BOOLEAN"
          ? [
              { value: "true", label: t("Yes") },
              { value: "false", label: t("No") },
            ]
          : null;

  if (selectOptions) {
    if (multi) {
      return (
        <div
          className={invalid ? "rounded-lg ring-1 ring-destructive" : undefined}
        >
          <MultiSelect
            options={selectOptions}
            selected={new Set(values.map(String))}
            onChange={(next) => onChange([...next])}
            placeholder={refLoading ? "Loading…" : "Select one or more"}
            minWidth={0}
            allowEmpty
          />
        </div>
      );
    }
    return (
      <Select
        value={values[0] === undefined ? "" : String(values[0])}
        onChange={(v) =>
          onChange([attribute.data_type === "BOOLEAN" ? v === "true" : v])
        }
        options={selectOptions}
        placeholder={refLoading ? "Loading…" : "Select a value"}
        minWidth={0}
        className={`w-full ${invalid ? "!border-destructive" : ""}`}
      />
    );
  }

  // BETWEEN renders two bounds; everything else a single field.
  if (operator?.min_values === 2 && operator?.max_values === 2) {
    return (
      <div className="flex items-center gap-2">
        {[0, 1].map((i) => (
          <input
            key={i}
            type={attribute.data_type === "NUMBER" ? "number" : "text"}
            value={values[i] === undefined ? "" : String(values[i])}
            onChange={(e) => {
              const next = [values[0] ?? "", values[1] ?? ""];
              next[i] = e.target.value;
              onChange(next);
            }}
            placeholder={i === 0 ? t("From") : t("To")}
            className={`h-9 w-full rounded-lg border ${border} bg-background px-3 text-sm outline-none focus:border-primary`}
          />
        ))}
      </div>
    );
  }

  if (multi) {
    // Free-text set membership (e.g. called_number IN [...]) — comma separated,
    // rendered back as chips so a long list stays readable.
    return (
      <div>
        <input
          type="text"
          value={values.map(String).join(", ")}
          onChange={(e) =>
            onChange(
              e.target.value
                .split(",")
                .map((v) => v.trim())
                .filter(Boolean),
            )
          }
          placeholder={t("Comma-separated values")}
          className={`h-9 w-full rounded-lg border ${border} bg-background px-3 text-sm outline-none focus:border-primary`}
        />
        {values.length > 1 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {values.map((v, i) => (
              <span
                key={`${v}-${i}`}
                className="inline-flex items-center gap-1 text-[11px] rounded-md bg-muted px-1.5 py-0.5 text-muted-foreground"
              >
                {String(v)}
                <button
                  type="button"
                  aria-label={t("Remove")}
                  onClick={() => onChange(values.filter((_, j) => j !== i))}
                  className="hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <input
      type={
        attribute.data_type === "NUMBER"
          ? "number"
          : attribute.data_type === "DATETIME"
            ? "datetime-local"
            : "text"
      }
      value={values[0] === undefined ? "" : String(values[0])}
      onChange={(e) => onChange([e.target.value])}
      placeholder={t("Value")}
      className={`h-9 w-full rounded-lg border ${border} bg-background px-3 text-sm outline-none focus:border-primary`}
    />
  );
}

/**
 * Visual condition builder with two levels of nesting.
 *
 * Conditions sharing a `group_index` are ANDed together; the groups themselves
 * are combined with the rule's `condition_logic` (usually OR). That is exactly
 * what the backend evaluates, so `(A AND B) OR C` is authorable here and means
 * the same thing to the engine.
 *
 * Two levels, not arbitrary nesting: every real tariff we modelled fits, and a
 * deeper tree would stop the compiler flattening a rule into one lookup key —
 * which is what makes 10,000 rules resolvable per rating context.
 */
export function ConditionBuilder({
  conditions,
  onChange,
  errorPaths,
  groupLogic = "OR",
}: {
  conditions: ConditionRow[];
  onChange: (next: ConditionRow[]) => void;
  /** Validation paths like "conditions[2].values" that should be highlighted. */
  errorPaths?: Set<string>;
  /** How the rule combines its groups — shown between them. */
  groupLogic?: string;
}) {
  const t = useT();
  const { data: attributes = [] } = useRuleAttributes();
  const { data: operators = [] } = useOperators();

  const attrByKey = useMemo(
    () => new Map(attributes.map((a) => [a.key, a])),
    [attributes],
  );
  const opByKey = useMemo(
    () => new Map(operators.map((o) => [o.key, o])),
    [operators],
  );

  // Attributes grouped exactly as the backend groups them (Service, Destination,
  // Time, Network, Usage, Subscriber) so a long list stays navigable.
  const attributeOptions = useMemo(() => {
    const groups = new Map<string, RuleAttribute[]>();
    attributes.forEach((a) => {
      const list = groups.get(a.group) ?? [];
      list.push(a);
      groups.set(a.group, list);
    });
    return [...groups.entries()].flatMap(([group, list]) =>
      list.map((a) => ({ value: a.key, label: `${group} · ${a.label}` })),
    );
  }, [attributes]);

  // The builder edits a flat list (that is the wire format) but renders it
  // grouped. Indices are kept alongside so validation paths, which are indexes
  // into the flat list, still line up with the row they belong to.
  const groups = useMemo(() => {
    const byIndex = new Map<
      number,
      { cond: ConditionRow; flatIndex: number }[]
    >();
    conditions.forEach((cond, flatIndex) => {
      const key = cond.group_index ?? 0;
      const list = byIndex.get(key) ?? [];
      list.push({ cond, flatIndex });
      byIndex.set(key, list);
    });
    return [...byIndex.entries()].sort((a, b) => a[0] - b[0]);
  }, [conditions]);

  const update = (flatIndex: number, patch: Partial<ConditionRow>) =>
    onChange(
      conditions.map((c, i) => (i === flatIndex ? { ...c, ...patch } : c)),
    );

  const removeAt = (flatIndex: number) =>
    onChange(conditions.filter((_, i) => i !== flatIndex));

  const addToGroup = (groupIndex: number) =>
    onChange([...conditions, newConditionRow({ group_index: groupIndex })]);

  const addGroup = () => {
    const next = groups.length ? Math.max(...groups.map(([g]) => g)) + 1 : 0;
    onChange([...conditions, newConditionRow({ group_index: next })]);
  };

  const removeGroup = (groupIndex: number) =>
    onChange(conditions.filter((c) => (c.group_index ?? 0) !== groupIndex));

  const hasError = (flatIndex: number, field: string) => {
    if (!errorPaths) return false;
    return (
      errorPaths.has(`conditions[${flatIndex}].${field}`) ||
      [...errorPaths].some((p) =>
        p.startsWith(`conditions[${flatIndex}].${field}[`),
      )
    );
  };

  if (conditions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border p-6 text-center">
        <p className="text-sm text-muted-foreground max-w-md mx-auto leading-relaxed">
          {t(
            "No conditions — this rule will match every CDR of its service type. That is correct for a global default rule; otherwise add a condition.",
          )}
        </p>
        <button
          type="button"
          onClick={() => onChange([newConditionRow()])}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
        >
          <Plus className="h-4 w-4" /> {t("Add condition")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map(([groupIndex, members], groupPosition) => (
        <div key={groupIndex}>
          {groupPosition > 0 && (
            <div className="flex items-center gap-3 my-3">
              <span className="h-px flex-1 bg-border" />
              <span className="text-[11px] font-semibold uppercase tracking-widest text-primary">
                {t(groupLogic)}
              </span>
              <span className="h-px flex-1 bg-border" />
            </div>
          )}

          <div
            className={
              groups.length > 1
                ? "rounded-xl border border-border/70 bg-muted/20 p-3 space-y-2"
                : "space-y-2"
            }
          >
            {groups.length > 1 && (
              <div className="flex items-center justify-between gap-2 px-1">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("Group")} {groupPosition + 1}
                </span>
                <button
                  type="button"
                  onClick={() => removeGroup(groupIndex)}
                  className="text-[11px] text-muted-foreground hover:text-destructive transition"
                >
                  {t("Remove group")}
                </button>
              </div>
            )}

            {members.map(({ cond, flatIndex }, position) => {
              const attr = attrByKey.get(cond.attribute);
              const op = opByKey.get(cond.operator);
              const allowedOperators = (attr?.operators ?? []).map((key) => ({
                value: key,
                label: opByKey.get(key)?.label ?? key,
              }));

              return (
                <div
                  key={cond._key}
                  className="rounded-xl border border-border bg-card p-3 grid grid-cols-1 md:grid-cols-[auto_1fr_1fr_1.4fr_auto] gap-2 md:items-start"
                >
                  <div className="flex md:h-9 items-center text-[11px] font-semibold uppercase tracking-wide text-muted-foreground md:w-12">
                    {position === 0 ? t("Where") : t("And")}
                  </div>

                  <div>
                    <Select
                      value={cond.attribute}
                      onChange={(key) => {
                        const next = attrByKey.get(key);
                        // Switching attribute can invalidate the operator (a
                        // NUMBER attribute has no STARTS_WITH), so fall back to
                        // its first allowed operator and clear the now-
                        // meaningless values.
                        const stillValid = next?.operators.includes(
                          cond.operator,
                        );
                        update(flatIndex, {
                          attribute: key,
                          operator: stillValid
                            ? cond.operator
                            : (next?.operators[0] ?? "EQUALS"),
                          values: [],
                        });
                      }}
                      options={attributeOptions}
                      placeholder="Attribute"
                      minWidth={0}
                      className={`w-full ${hasError(flatIndex, "attribute") ? "!border-destructive" : ""}`}
                    />
                    {attr?.description && (
                      <div className="mt-1 text-[11px] text-muted-foreground flex items-start gap-1">
                        <InfoHint text={attr.description} />
                        <span className="truncate">{attr.description}</span>
                      </div>
                    )}
                  </div>

                  <Select
                    value={cond.operator}
                    onChange={(key) => {
                      const nextOp = opByKey.get(key);
                      // Trim values to what the new operator accepts, so moving
                      // IN → EQUALS doesn't silently keep four operands.
                      const max = nextOp?.max_values;
                      update(flatIndex, {
                        operator: key,
                        values:
                          max === 0
                            ? []
                            : max === null
                              ? cond.values
                              : cond.values.slice(0, max ?? 1),
                      });
                    }}
                    options={allowedOperators}
                    placeholder="Operator"
                    minWidth={0}
                    className={`w-full ${hasError(flatIndex, "operator") ? "!border-destructive" : ""}`}
                  />

                  <ValueInput
                    attribute={attr}
                    operator={op}
                    values={cond.values}
                    onChange={(values) => update(flatIndex, { values })}
                    invalid={hasError(flatIndex, "values")}
                  />

                  <button
                    type="button"
                    aria-label={t("Remove condition")}
                    onClick={() => removeAt(flatIndex)}
                    className="h-9 w-9 rounded-lg flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              );
            })}

            <button
              type="button"
              onClick={() => addToGroup(groupIndex)}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
            >
              <Plus className="h-4 w-4" /> {t("Add condition")}
            </button>
          </div>
        </div>
      ))}

      <div className="pt-1">
        <button
          type="button"
          onClick={addGroup}
          className="inline-flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-1.5 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition"
        >
          <GitBranch className="h-4 w-4" /> {t("Add")} {t(groupLogic)}{" "}
          {t("group")}
        </button>
      </div>
    </div>
  );
}
