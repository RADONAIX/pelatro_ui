import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft, FileText, Loader2, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useCanEditRules,
  useCreateRule,
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
  const create = useCreateRule();
  const { data: templates = [] } = useRuleTemplates();

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
        toRulePayload(form, conditions, actions),
      );
      toast.success(t("Rule created"), {
        description: `${rule.rule_key} v${rule.version} — ${t("saved as a draft")}`,
      });
      navigate({ to: "/rating/rules/$ruleId", params: { ruleId: rule.id } });
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
