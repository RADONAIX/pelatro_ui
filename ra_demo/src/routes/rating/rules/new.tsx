import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft, FileText, Loader2, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useActionSpecs,
  useCanEditRules,
  useCatalogList,
  useCreateCanonicalRule,
  useRuleSets,
  useRuleTemplates,
} from "@/lib/rating/hooks";
import {
  RuleEditor,
  emptyRuleForm,
  toRulePayload,
  type RuleFormState,
} from "@/components/rating/RuleEditor";
import {
  newConditionRow,
  type ConditionRow,
} from "@/components/rating/ConditionBuilder";
import {
  newActionRow,
  type ActionRow,
} from "@/components/rating/ActionBuilder";
import { RatingEmpty } from "@/components/rating/RatingState";

export const Route = createFileRoute("/rating/rules/new")({
  component: CreateRulePage,
});

function CreateRulePage() {
  const t = useT();
  const navigate = useNavigate();
  const canEdit = useCanEditRules();
  const create = useCreateCanonicalRule();
  const { data: templates = [] } = useRuleTemplates();
  const { data: actionSpecs = [] } = useActionSpecs();
  const { data: ruleSets = [] } = useRuleSets();
  const { data: products = [] } = useCatalogList("products", {
    status: "ACTIVE",
  });
  const { data: offers = [] } = useCatalogList("offers", {
    status: "ACTIVE",
  });
  const { data: tariffPlans = [] } = useCatalogList("tariff-plans", {
    status: "ACTIVE",
  });

  const [form, setForm] = useState<RuleFormState>(emptyRuleForm);
  const [conditions, setConditions] = useState<ConditionRow[]>([]);
  const [actions, setActions] = useState<ActionRow[]>([newActionRow()]);
  const [appliedTemplate, setAppliedTemplate] = useState<string>("");

  const patch = (p: Partial<RuleFormState>) => setForm((f) => ({ ...f, ...p }));

  const applyTemplate = (code: string) => {
    const tpl = templates.find((x) => x.code === code);
    if (!tpl) return;
    setAppliedTemplate(code);
    patch({
      name: form.name || tpl.name,
      description: form.description || tpl.description,
      rule_type: tpl.rule_type,
      service_type:
        tpl.service_type === "ANY" ? form.service_type : tpl.service_type,
    });
    setConditions(
      (tpl.payload.conditions ?? []).map((c) => newConditionRow(c)),
    );
    setActions((tpl.payload.actions ?? []).map((a) => newActionRow(a)));
    toast.success(t("Template applied"), { description: tpl.name });
  };

  const canSave = form.name.trim().length > 0 && form.effective_from.length > 0;

  const submit = async () => {
    if (!canSave) return;
    try {
      const rule = await create.mutateAsync(
        toCanonicalCreatePayload(form, conditions, actions, {
          actionSpecs,
          ruleSets,
          products,
          offers,
          tariffPlans,
        }),
      );
      toast.success(t("Rule created"), {
        description: `${rule.rule.rule_key} v${rule.rule.version_number ?? 1} — ${t("saved as a draft")}`,
      });
      navigate({
        to: "/rating/rules/$ruleId",
        params: { ruleId: rule.rule.rule_id },
      });
    } catch (err) {
      toast.error(t("Couldn't create the rule"), {
        description: ratingError(err),
      });
    }
  };

  if (!canEdit) {
    return (
      <AppShell>
        <PageHeader title={t("Create Rule")} />
        <RatingEmpty
          icon={FileText}
          title="You don't have rule-authoring rights"
          description="Your role can view the rule catalogue but not create or edit rules. Ask an administrator for the rule-creator role."
          action={
            <Link
              to="/rating/rules"
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition"
            >
              <ArrowLeft className="h-4 w-4" /> {t("Back to the catalogue")}
            </Link>
          }
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title={t("Create Rule")}
        description={t(
          "Author a canonical tariff rule. It is saved as a draft — validate it, then submit it for review.",
        )}
        info={t(
          "The attributes, operators and actions offered here are served by the rating engine itself, so this form can only build rules the engine can execute.",
        )}
        actions={
          <div className="flex items-center gap-2">
            <Link
              to="/rating/rules"
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
            >
              <ArrowLeft className="h-4 w-4" /> {t("Cancel")}
            </Link>
            <button
              onClick={submit}
              disabled={!canSave || create.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {create.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {t("Save draft")}
            </button>
          </div>
        }
      />

      {templates.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-4 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold text-foreground">
              {t("Start from a template")}
            </span>
            <span className="text-xs text-muted-foreground">
              {t(
                "Fills the conditions and actions — you can still change everything.",
              )}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {templates.map((tpl) => (
              <button
                key={tpl.code}
                type="button"
                onClick={() => applyTemplate(tpl.code)}
                title={tpl.description}
                className={`rounded-lg border px-3 py-1.5 text-left transition ${
                  appliedTemplate === tpl.code
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-muted"
                }`}
              >
                <div className="text-[13px] font-medium text-foreground">
                  {tpl.name}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {tpl.service_type} · {tpl.rule_type}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      <RuleEditor
        form={form}
        onForm={patch}
        conditions={conditions}
        onConditions={setConditions}
        actions={actions}
        onActions={setActions}
      />

      <div className="flex items-center justify-end gap-2 mt-6">
        <Link
          to="/rating/rules"
          className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
        >
          {t("Cancel")}
        </Link>
        <button
          onClick={submit}
          disabled={!canSave || create.isPending}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {create.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {t("Save draft")}
        </button>
      </div>
    </AppShell>
  );
}

const MONEY_PARAMETERS = new Set([
  "SET_RATE:rate",
  "SET_MINIMUM_CHARGE:amount",
  "SET_MAXIMUM_CHARGE:amount",
  "APPLY_FIXED_DISCOUNT:amount",
  "APPLY_FIXED_SURCHARGE:amount",
  "APPLY_FIXED_TAX:amount",
]);

function toCanonicalCreatePayload(
  form: RuleFormState,
  conditions: ConditionRow[],
  actions: ActionRow[],
  lookups: {
    actionSpecs: ReturnType<typeof useActionSpecs>["data"];
    ruleSets: ReturnType<typeof useRuleSets>["data"];
    products: ReturnType<typeof useCatalogList>["data"];
    offers: ReturnType<typeof useCatalogList>["data"];
    tariffPlans: ReturnType<typeof useCatalogList>["data"];
  },
) {
  const legacy = toRulePayload(form, conditions, actions);
  const codeFor = (
    rows: NonNullable<ReturnType<typeof useCatalogList>["data"]>,
    id: string | null,
  ) => rows.find((row) => row.id === id)?.code ?? null;
  const specByAction = new Map(
    (lookups.actionSpecs ?? []).map((spec) => [spec.type, spec]),
  );

  return {
    rule_key: legacy.rule_key,
    rule_name: legacy.name,
    description: legacy.description,
    charging_mode: "BOTH",
    rule_type_code: legacy.rule_type,
    service_type: legacy.service_type,
    validity: {
      effective_from: legacy.effective_from,
      effective_to: legacy.effective_to,
      currency_code: legacy.currency_code,
    },
    conditions: {
      logic: legacy.condition_logic,
      negated: false,
      label: "",
      conditions: legacy.conditions.map((condition) => ({
        attribute: condition.attribute,
        operator: condition.operator,
        values: condition.values,
        negated: condition.negate,
        unit: null,
        currency: null,
      })),
      children: [],
    },
    actions: legacy.actions.map((action, actionIndex) => {
      const spec = specByAction.get(action.action_type);
      const paramTypes = new Map(
        (spec?.params ?? []).map((parameter) => [
          parameter.key,
          parameter.data_type,
        ]),
      );
      return {
        action_type: action.action_type,
        target_attribute: null,
        sequence: actionIndex + 1,
        parameters: Object.entries(action.params)
          .filter(([, value]) => value !== undefined && value !== "")
          .map(([name, value], parameterIndex) => {
            const monetary = MONEY_PARAMETERS.has(
              `${action.action_type}:${name}`,
            );
            return {
              name,
              value,
              value_type: monetary
                ? "MONEY"
                : (paramTypes.get(name) ?? inferValueType(value)),
              currency: monetary ? legacy.currency_code : null,
              unit: null,
              sequence: parameterIndex + 1,
            };
          }),
      };
    }),
    behaviour: {
      priority: legacy.priority,
      stacking_policy: legacy.stacking_policy,
      conflict_group: legacy.conflict_group,
      fallback_policy: "FALLBACK_CHAIN",
      stop_processing: false,
      execution_mode: "BOTH",
      condition_logic: legacy.condition_logic,
    },
    targets: {
      product: codeFor(lookups.products ?? [], legacy.product_id),
      offer: codeFor(lookups.offers ?? [], legacy.offer_id),
      tariff_plan: codeFor(lookups.tariffPlans ?? [], legacy.tariff_plan_id),
    },
    set_codes: legacy.rule_set_id
      ? [
          (lookups.ruleSets ?? []).find(
            (ruleSet) => ruleSet.id === legacy.rule_set_id,
          )?.code,
        ].filter((code): code is string => !!code)
      : [],
    owner: legacy.owner,
    change_reason: legacy.change_comment,
  };
}

function inferValueType(value: unknown): string {
  if (typeof value === "number") return "NUMBER";
  if (typeof value === "boolean") return "BOOLEAN";
  if (Array.isArray(value)) return "LIST";
  return "STRING";
}
