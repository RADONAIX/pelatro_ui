import { useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import { useActionSpecs, useReferenceOptions } from "@/lib/rating/hooks";
import type { Action, ActionParamSpec, ActionSpec } from "@/lib/rating/types";

const HIDDEN_AUTHORING_PARAMS = new Set([
  "per_units",
  "subsequent_seconds",
  "decimals",
  "consume_order",
]);

// ---------------------------------------------------------------------------
// Visual action builder. Like the condition builder, every action and every
// parameter it renders comes from the backend's ACTION_SPECS — so the form for
// "Apply discount" always matches what the validator will accept.
// ---------------------------------------------------------------------------

export interface ActionRow extends Action {
  _key: string;
}

export function newActionRow(seed?: Partial<Action>): ActionRow {
  return {
    _key:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `a-${Math.random().toString(36).slice(2)}`,
    action_type: seed?.action_type ?? "",
    params: seed?.params ?? {},
  };
}

function ParamField({
  param,
  value,
  onChange,
  invalid,
}: {
  param: ActionParamSpec;
  value: unknown;
  onChange: (value: unknown) => void;
  invalid?: boolean;
}) {
  const t = useT();
  const { options: refOptions, isLoading } = useReferenceOptions(
    param.data_type === "REFERENCE" ? param.reference : null,
  );
  const border = invalid ? "!border-destructive" : "border-border";

  const label = (
    <div className="flex items-center gap-1 mb-1">
      <span className="text-[11px] font-medium text-muted-foreground">
        {t(param.label)}
        {param.required && <span className="text-destructive ml-0.5">*</span>}
      </span>
      {param.description && <InfoHint text={param.description} />}
    </div>
  );

  if (param.data_type === "REFERENCE" || param.data_type === "ENUM") {
    const options =
      param.data_type === "REFERENCE"
        ? refOptions
        : param.values.map((v) => ({ value: v, label: v }));
    return (
      <div>
        {label}
        <Select
          value={value === undefined || value === null ? "" : String(value)}
          onChange={onChange}
          options={options}
          placeholder={isLoading ? "Loading…" : "Select…"}
          minWidth={0}
          className={`w-full ${invalid ? "!border-destructive" : ""}`}
        />
      </div>
    );
  }

  if (param.data_type === "BOOLEAN") {
    return (
      <div>
        {label}
        <Select
          value={value === undefined ? "" : String(value)}
          onChange={(v) => onChange(v === "true")}
          options={[
            { value: "true", label: t("Yes") },
            { value: "false", label: t("No") },
          ]}
          minWidth={0}
          className="w-full"
        />
      </div>
    );
  }

  return (
    <div>
      {label}
      <input
        type={param.data_type === "NUMBER" ? "number" : "text"}
        step="any"
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(undefined);
          onChange(param.data_type === "NUMBER" ? Number(raw) : raw);
        }}
        className={`h-9 w-full rounded-lg border ${border} bg-background px-3 text-sm outline-none focus:border-primary`}
      />
    </div>
  );
}

export function ActionBuilder({
  actions,
  onChange,
  errorPaths,
}: {
  actions: ActionRow[];
  onChange: (next: ActionRow[]) => void;
  errorPaths?: Set<string>;
}) {
  const t = useT();
  const { data: specs = [] } = useActionSpecs();

  const specByType = useMemo(
    () => new Map<string, ActionSpec>(specs.map((s) => [s.type, s])),
    [specs],
  );
  // Grouped by charging stage so the list reads in execution order rather than
  // alphabetically — an author picking "Apply tax" can see it runs after
  // discount without opening the docs.
  const options = useMemo(
    () =>
      specs.map((s) => ({ value: s.type, label: `${s.stage} · ${s.label}` })),
    [specs],
  );

  const update = (index: number, patch: Partial<ActionRow>) =>
    onChange(actions.map((a, i) => (i === index ? { ...a, ...patch } : a)));

  const hasError = (i: number, key?: string) => {
    if (!errorPaths) return false;
    const prefix = key ? `actions[${i}].params.${key}` : `actions[${i}]`;
    return [...errorPaths].some(
      (p) => p === prefix || p.startsWith(`${prefix}.`),
    );
  };

  if (actions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border p-6 text-center">
        <p className="text-sm text-muted-foreground max-w-md mx-auto leading-relaxed">
          {t(
            "No actions yet. Without one the rule can match a CDR but won't change the expected charge.",
          )}
        </p>
        <button
          type="button"
          onClick={() => onChange([newActionRow()])}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
        >
          <Plus className="h-4 w-4" /> {t("Add action")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {actions.map((action, i) => {
        const spec = specByType.get(action.action_type);
        return (
          <div
            key={action._key}
            className={`rounded-xl border bg-card p-3 ${
              hasError(i) ? "border-destructive/50" : "border-border"
            }`}
          >
            <div className="flex items-start gap-2">
              <div className="flex md:h-9 items-center text-[11px] font-semibold uppercase tracking-wide text-muted-foreground w-12 shrink-0">
                {t("Then")}
              </div>
              <div className="flex-1 min-w-0">
                <Select
                  value={action.action_type}
                  onChange={(type) =>
                    // Parameters are per-action-type, so switching action must
                    // drop the old params rather than carry incompatible keys.
                    update(i, { action_type: type, params: {} })
                  }
                  options={options}
                  placeholder="Action"
                  minWidth={0}
                  className="w-full"
                />
                {spec?.description && (
                  <p className="mt-1.5 text-[11px] text-muted-foreground leading-relaxed">
                    {spec.description}
                  </p>
                )}
              </div>
              <button
                type="button"
                aria-label={t("Remove action")}
                onClick={() => onChange(actions.filter((_, j) => j !== i))}
                className="h-9 w-9 shrink-0 rounded-lg flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>

            {spec && spec.params.length > 0 && (
              <div className="mt-3 ml-0 md:ml-14 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {spec.params
                  .filter((p) => !HIDDEN_AUTHORING_PARAMS.has(p.key))
                  .map((p) => (
                    <ParamField
                      key={p.key}
                      param={p}
                      value={action.params[p.key]}
                      invalid={hasError(i, p.key)}
                      onChange={(v) => {
                        const params = { ...action.params };
                        if (v === undefined || v === "") delete params[p.key];
                        else params[p.key] = v;
                        update(i, { params });
                      }}
                    />
                  ))}
              </div>
            )}
            {spec && spec.params.length === 0 && (
              <p className="mt-2 ml-0 md:ml-14 text-[11px] text-muted-foreground">
                {t("This action takes no parameters.")}
              </p>
            )}
          </div>
        );
      })}

      <button
        type="button"
        onClick={() => onChange([...actions, newActionRow()])}
        className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
      >
        <Plus className="h-4 w-4" /> {t("Add action")}
      </button>
    </div>
  );
}
