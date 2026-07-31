import { useMemo } from "react";
import { Select } from "@/components/ui-kit/Select";
import { InfoHint } from "@/components/ui-kit/InfoHint";
import { useT } from "@/lib/i18n";
import type { CatalogEntity } from "@/lib/rating/types";
import {
  useCatalogList,
  useRatingEnums,
  useRuleSets,
} from "@/lib/rating/hooks";
import {
  ActionBuilder,
  type ActionRow,
} from "@/components/rating/ActionBuilder";
import {
  ConditionBuilder,
  type ConditionRow,
} from "@/components/rating/ConditionBuilder";

// Shared "basic details" form + the two builders. Used by Create Rule and by the
// edit mode of Rule Detail so the two can never diverge.

export interface RuleFormState {
  rule_key: string;
  name: string;
  description: string;
  rule_type: string;
  service_type: string;
  category: string;
  rule_set_id: string;
  product_id: string;
  offer_id: string;
  tariff_plan_id: string;
  priority: number;
  stacking_policy: string;
  conflict_group: string;
  condition_logic: string;
  effective_from: string;
  effective_to: string;
  currency_code: string;
  owner: string;
  change_comment: string;
}

export function emptyRuleForm(): RuleFormState {
  return {
    rule_key: "",
    name: "",
    description: "",
    rule_type: "BASE_TARIFF",
    service_type: "VOICE",
    category: "",
    rule_set_id: "",
    product_id: "",
    offer_id: "",
    tariff_plan_id: "",
    priority: 100,
    stacking_policy: "EXCLUSIVE",
    conflict_group: "",
    condition_logic: "AND",
    effective_from: new Date().toISOString().slice(0, 10),
    effective_to: "",
    currency_code: "",
    owner: "",
    change_comment: "",
  };
}

/** Strip empty strings so the API receives `null`, not `""`, for optional FKs. */
export function toRulePayload(
  form: RuleFormState,
  conditions: ConditionRow[],
  actions: ActionRow[],
) {
  const blankToNull = (v: string) => (v.trim() === "" ? null : v.trim());
  return {
    rule_key: blankToNull(form.rule_key) ?? undefined,
    name: form.name.trim(),
    description: form.description,
    rule_type: form.rule_type,
    service_type: form.service_type,
    category: form.category,
    rule_set_id: blankToNull(form.rule_set_id),
    product_id: blankToNull(form.product_id),
    offer_id: blankToNull(form.offer_id),
    tariff_plan_id: blankToNull(form.tariff_plan_id),
    priority: Number(form.priority) || 0,
    stacking_policy: form.stacking_policy,
    conflict_group: blankToNull(form.conflict_group),
    condition_logic: form.condition_logic,
    effective_from: form.effective_from,
    effective_to: blankToNull(form.effective_to),
    currency_code: blankToNull(form.currency_code),
    owner: blankToNull(form.owner),
    change_comment: form.change_comment,
    conditions: conditions.map(({ _key, id, sequence, ...c }) => {
      void _key;
      void id;
      void sequence;
      return c;
    }),
    actions: actions.map(({ _key, id, sequence, ...a }) => {
      void _key;
      void id;
      void sequence;
      return a;
    }),
  };
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <div>
      <div className="flex items-center gap-1 mb-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {t(label)}
          {required && <span className="text-destructive ml-0.5">*</span>}
        </label>
        {hint && <InfoHint text={t(hint)} />}
      </div>
      {children}
    </div>
  );
}

const inputCls =
  "h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary";

export function RuleEditor({
  form,
  onForm,
  conditions,
  onConditions,
  actions,
  onActions,
  errorPaths,
  lockIdentity = false,
}: {
  form: RuleFormState;
  onForm: (patch: Partial<RuleFormState>) => void;
  conditions: ConditionRow[];
  onConditions: (next: ConditionRow[]) => void;
  actions: ActionRow[];
  onActions: (next: ActionRow[]) => void;
  errorPaths?: Set<string>;
  /** Rule key can't change once the rule exists — it identifies every version. */
  lockIdentity?: boolean;
}) {
  const t = useT();
  const { data: enums } = useRatingEnums();
  const { data: ruleSets = [] } = useRuleSets();
  const { data: products = [] } = useCatalogList("products", {
    status: "ACTIVE",
  });
  const { data: offers = [] } = useCatalogList("offers", { status: "ACTIVE" });
  const { data: plans = [] } = useCatalogList("tariff-plans", {
    status: "ACTIVE",
  });
  const { data: currencies = [] } = useCatalogList("currencies", {
    status: "ACTIVE",
  });

  // Rules link to catalogue entities by id, but a code is what an author
  // recognises — so the option value is the id and the label leads with the code.
  const opt = (rows: CatalogEntity[]) => [
    { value: "", label: t("Any") },
    ...rows.map((r) => ({ value: r.id, label: `${r.code} — ${r.name}` })),
  ];

  // Offers belong to a product; offering every offer once a product is chosen
  // would let an author build a rule that can never match.
  const offerOptions = useMemo(
    () =>
      opt(
        form.product_id
          ? offers.filter((o) => o.product_id === form.product_id)
          : offers,
      ),
    // `opt` closes over `t` only, and is recreated every render by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [offers, form.product_id],
  );

  const stage = enums?.rule_type_stage?.[form.rule_type];

  return (
    <div className="space-y-6">
      {/* --- Basic details -------------------------------------------------- */}
      <section className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-sm font-semibold text-foreground mb-4">
          {t("Basic details")}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Rule name" required>
            <input
              value={form.name}
              onChange={(e) => onForm({ name: e.target.value })}
              placeholder={t("e.g. Prepaid A — on-net peak voice rate")}
              className={inputCls}
            />
          </Field>

          <Field
            label="Rule key"
            hint="Stable identifier shared by every version of this rule. Leave blank to derive it from the name."
          >
            <input
              value={form.rule_key}
              onChange={(e) =>
                onForm({ rule_key: e.target.value.toUpperCase() })
              }
              placeholder={t("Derived from the name")}
              disabled={lockIdentity}
              className={`${inputCls} ${lockIdentity ? "opacity-60 cursor-not-allowed" : ""}`}
            />
          </Field>

          <Field label="Category">
            <input
              value={form.category}
              onChange={(e) => onForm({ category: e.target.value })}
              placeholder={t("e.g. Voice tariff")}
              className={inputCls}
            />
          </Field>

          <Field label="Service type" required>
            <Select
              value={form.service_type}
              onChange={(v) => onForm({ service_type: v })}
              options={(enums?.service_type ?? []).map((v) => ({
                value: v,
                label: v,
              }))}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field
            label="Rule type"
            required
            hint="Decides where the rule runs in the charging sequence."
          >
            <div>
              <Select
                value={form.rule_type}
                onChange={(v) => onForm({ rule_type: v })}
                options={(enums?.rule_type ?? []).map((v) => ({
                  value: v,
                  label: v,
                }))}
                minWidth={0}
                className="w-full"
              />
              {stage && (
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {t("Runs at stage")}:{" "}
                  <span className="font-medium text-foreground">{stage}</span>
                </div>
              )}
            </div>
          </Field>

          <Field label="Rule set">
            <Select
              value={form.rule_set_id}
              onChange={(v) => onForm({ rule_set_id: v })}
              options={[
                { value: "", label: t("None") },
                ...ruleSets.map((r) => ({
                  value: r.id,
                  label: `${r.code} — ${r.name}`,
                })),
              ]}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field label="Product">
            <Select
              value={form.product_id}
              onChange={(v) => onForm({ product_id: v, offer_id: "" })}
              options={opt(products)}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field label="Offer">
            <Select
              value={form.offer_id}
              onChange={(v) => onForm({ offer_id: v })}
              options={offerOptions}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field label="Tariff plan">
            <Select
              value={form.tariff_plan_id}
              onChange={(v) => onForm({ tariff_plan_id: v })}
              options={opt(plans)}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field
            label="Priority"
            hint="Higher wins when two equally specific rules match. Specificity is scored automatically from the conditions and breaks the tie first."
          >
            <input
              type="number"
              value={form.priority}
              onChange={(e) => onForm({ priority: Number(e.target.value) })}
              className={inputCls}
            />
          </Field>

          <Field
            label="Stacking policy"
            hint="EXCLUSIVE: only the winner in the conflict group applies. STACKABLE: every match applies. OVERRIDE: replaces what a lower-priority rule set."
          >
            <Select
              value={form.stacking_policy}
              onChange={(v) => onForm({ stacking_policy: v })}
              options={(enums?.stacking_policy ?? []).map((v) => ({
                value: v,
                label: v,
              }))}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field
            label="Conflict group"
            hint="Rules sharing a group are mutually exclusive; the winner is resolved once per rating context."
          >
            <input
              value={form.conflict_group}
              onChange={(e) => onForm({ conflict_group: e.target.value })}
              placeholder={t("Optional")}
              className={inputCls}
            />
          </Field>

          <Field label="Effective from" required>
            <input
              type="date"
              value={form.effective_from}
              onChange={(e) => onForm({ effective_from: e.target.value })}
              className={inputCls}
            />
          </Field>

          <Field
            label="Effective to"
            hint="Leave blank for an open-ended rule."
          >
            <input
              type="date"
              value={form.effective_to}
              onChange={(e) => onForm({ effective_to: e.target.value })}
              className={inputCls}
            />
          </Field>

          <Field label="Currency">
            <Select
              value={form.currency_code}
              onChange={(v) => onForm({ currency_code: v })}
              options={[
                { value: "", label: t("Not set") },
                ...currencies.map((c) => ({
                  value: c.code,
                  label: `${c.code} — ${c.name}`,
                })),
              ]}
              minWidth={0}
              className="w-full"
            />
          </Field>

          <Field label="Owner">
            <input
              value={form.owner}
              onChange={(e) => onForm({ owner: e.target.value })}
              placeholder={t("Team or person accountable")}
              className={inputCls}
            />
          </Field>

          <div className="md:col-span-2 lg:col-span-3">
            <Field label="Description">
              <textarea
                value={form.description}
                onChange={(e) => onForm({ description: e.target.value })}
                rows={2}
                placeholder={t("What this rule charges, and why it exists.")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary resize-y"
              />
            </Field>
          </div>
        </div>
      </section>

      {/* --- Conditions ----------------------------------------------------- */}
      <section className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-1">
          <h2 className="text-sm font-semibold text-foreground">
            {t("Conditions")}
          </h2>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">
              {t("Combine groups with")}
            </span>
            <Select
              value={form.condition_logic}
              onChange={(v) => onForm({ condition_logic: v })}
              options={(enums?.condition_logic ?? ["AND", "OR"]).map((v) => ({
                value: v,
                label: v,
              }))}
              minWidth={80}
              size="sm"
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground mb-4">
          {t(
            "Conditions inside a group are ANDed. Add a second group to express \u201Cthis OR that\u201D \u2014 the rule applies when the groups combine to true.",
          )}
        </p>
        <ConditionBuilder
          conditions={conditions}
          onChange={onConditions}
          errorPaths={errorPaths}
          groupLogic={form.condition_logic}
        />
      </section>

      {/* --- Actions -------------------------------------------------------- */}
      <section className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-sm font-semibold text-foreground mb-1">
          {t("Actions")}
        </h2>
        <p className="text-xs text-muted-foreground mb-4">
          {t("What the rule does to the expected charge when it matches.")}
        </p>
        <ActionBuilder
          actions={actions}
          onChange={onActions}
          errorPaths={errorPaths}
        />
      </section>
    </div>
  );
}
