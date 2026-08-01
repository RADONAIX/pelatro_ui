import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Copy,
  GitBranch,
  History,
  Loader2,
  Pencil,
  Save,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { RuleLogicView } from "@/components/rating/RuleLogicView";
import { RuleStatusBadge } from "@/components/rating/RuleStatusBadge";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";
import {
  RuleEditor,
  emptyRuleForm,
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
import {
  ValidationPanel,
  errorPathsOf,
} from "@/components/rating/ValidationPanel";
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useCanEditRules,
  useChangeCanonicalRuleStatus,
  useCloneCanonicalRule,
  useCanonicalRule,
  useCanonicalRuleAudit,
  useCanonicalRuleVersions,
  useDeleteCanonicalRule,
  useNewCanonicalRuleVersion,
  useRatingEnums,
  useRatingMe,
  useUpdateCanonicalRule,
  useValidateCanonicalRule,
} from "@/lib/rating/hooks";
import type {
  Action,
  CanonicalAction,
  CanonicalCondition,
  CanonicalConditionGroup,
  CanonicalRuleDetail,
  Condition,
  ValidationReport,
} from "@/lib/rating/types";

export const Route = createFileRoute("/rating/rules/$ruleId")({
  component: RuleDetailPage,
});

type Tab = "logic" | "versions" | "history";
const EDITABLE = new Set(["DRAFT", "VALIDATED"]);

function RuleDetailPage() {
  const { ruleId } = Route.useParams();
  const t = useT();
  const navigate = useNavigate();
  const canEdit = useCanEditRules();
  const { data: me } = useRatingMe();
  const { data: enums } = useRatingEnums();
  const canApprove = !!me?.permissions?.ratingApprovals?.edit;
  const [tab, setTab] = useState<Tab>("logic");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<RuleFormState>(emptyRuleForm);
  const [conditionRows, setConditionRows] = useState<ConditionRow[]>([]);
  const [actionRows, setActionRows] = useState<ActionRow[]>([]);
  const [report, setReport] = useState<ValidationReport>();
  const ruleQuery = useCanonicalRule(ruleId);
  const versionsQuery = useCanonicalRuleVersions(ruleId);
  const auditQuery = useCanonicalRuleAudit(ruleId);
  const rule = ruleQuery.data;
  const versions = versionsQuery.data ?? [];
  const audit = auditQuery.data ?? [];
  const conditions = rule?.conditions ? flattenConditions(rule.conditions) : [];
  const actions = rule?.actions.map(toLegacyAction) ?? [];
  const update = useUpdateCanonicalRule(ruleId);
  const validate = useValidateCanonicalRule(ruleId);
  const changeStatus = useChangeCanonicalRuleStatus(ruleId);
  const newVersion = useNewCanonicalRuleVersion(ruleId);
  const clone = useCloneCanonicalRule(ruleId);
  const remove = useDeleteCanonicalRule();
  const errorPaths = useMemo(() => errorPathsOf(report), [report]);
  const frozen = !rule || !EDITABLE.has(rule.status);
  const nextStatuses = enums?.allowed_transitions?.[rule?.status ?? ""] ?? [];

  useEffect(() => {
    if (!rule || editing) return;
    const flat = rule.conditions ? flattenConditions(rule.conditions) : [];
    setForm(canonicalToForm(rule));
    setConditionRows(flat.map((condition) => newConditionRow(condition)));
    setActionRows(
      rule.actions.map((action) => newActionRow(toLegacyAction(action))),
    );
  }, [rule, editing]);

  const save = async () => {
    if (!rule) return;
    try {
      await update.mutateAsync(
        canonicalPayload(rule, form, conditionRows, actionRows),
      );
      setEditing(false);
      setReport(undefined);
      toast.success(t("Rule updated"));
    } catch (error) {
      toast.error(t("Couldn't save"), { description: ratingError(error) });
    }
  };

  const runValidation = async () => {
    try {
      const result = await validate.mutateAsync();
      setReport(result);
      if (result.valid) toast.success(t("Validation passed"));
      else toast.error(`${result.error_count} ${t("validation errors")}`);
    } catch (error) {
      toast.error(t("Validation failed to run"), {
        description: ratingError(error),
      });
    }
  };

  const move = async (status: string) => {
    try {
      await changeStatus.mutateAsync({ status });
      setReport(undefined);
      toast.success(`${t("Rule moved to")} ${status}`);
    } catch (error) {
      toast.error(t("Transition refused"), {
        description: ratingError(error),
      });
    }
  };

  const cutVersion = async () => {
    if (!rule) return;
    try {
      const next = canonicalPayload(
        rule,
        canonicalToForm(rule),
        conditions.map((condition) => newConditionRow(condition)),
        actions.map((action) => newActionRow(action)),
      );
      next.change_reason = `New version of ${rule.rule_key}`;
      const response = await newVersion.mutateAsync(next);
      toast.success(`${t("Created version")} ${response.rule.version_number}`);
      setTab("logic");
    } catch (error) {
      toast.error(t("Couldn't create a new version"), {
        description: ratingError(error),
      });
    }
  };

  const duplicate = async () => {
    if (!rule) return;
    try {
      const response = await clone.mutateAsync({
        rule_name: cloneRuleName(rule.rule_name),
      });
      toast.success(t("Rule cloned"), {
        description: response.rule.rule_key,
      });
      navigate({
        to: "/rating/rules/$ruleId",
        params: { ruleId: response.rule.rule_id },
      });
    } catch (error) {
      toast.error(t("Couldn't clone the rule"), {
        description: ratingError(error),
      });
    }
  };

  const discard = async () => {
    if (!rule) return;
    try {
      await remove.mutateAsync(rule.rule_id);
      toast.success(t("Draft deleted"));
      navigate({ to: "/rating/rules" });
    } catch (error) {
      toast.error(t("Couldn't delete"), { description: ratingError(error) });
    }
  };

  return (
    <AppShell>
      {ruleQuery.isLoading && <RatingLoading label="Loading rule…" />}
      {ruleQuery.error && (
        <RatingError
          error={ruleQuery.error}
          onRetry={() => void ruleQuery.refetch()}
        />
      )}

      {rule && (
        <>
          <PageHeader
            title={rule.rule_name}
            description={rule.description || undefined}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to="/rating/rules"
                  className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
                >
                  <ArrowLeft className="h-4 w-4" /> {t("Catalogue")}
                </Link>
                {canEdit && !frozen && !editing && (
                  <button
                    onClick={() => setEditing(true)}
                    className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
                  >
                    <Pencil className="h-4 w-4" /> {t("Edit")}
                  </button>
                )}
                {canEdit && frozen && (
                  <Tooltip
                    label={t(
                      "Approved rules are immutable — a change becomes version N+1.",
                    )}
                    side="bottom"
                  >
                    <button
                      onClick={cutVersion}
                      disabled={newVersion.isPending}
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
                    >
                      {newVersion.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <GitBranch className="h-4 w-4" />
                      )}
                      {t("New version")}
                    </button>
                  </Tooltip>
                )}
                {canEdit && (
                  <button
                    onClick={duplicate}
                    disabled={clone.isPending}
                    className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
                  >
                    <Copy className="h-4 w-4" /> {t("Clone")}
                  </button>
                )}
                {editing && (
                  <>
                    <button
                      onClick={() => {
                        setEditing(false);
                        setReport(undefined);
                      }}
                      className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted transition"
                    >
                      <X className="h-4 w-4" /> {t("Cancel")}
                    </button>
                    <button
                      onClick={save}
                      disabled={update.isPending}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90 disabled:opacity-50"
                    >
                      {update.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="h-4 w-4" />
                      )}
                      {t("Save")}
                    </button>
                  </>
                )}
              </div>
            }
          />

          {/* Keep the established detail-page layout while reading canonical data. */}
          <div className="bg-card border border-border rounded-xl px-5 py-4 mb-6 flex flex-wrap items-center gap-x-8 gap-y-3">
            <Meta label={t("Key")} value={rule.rule_key} mono />
            <Meta label={t("Version")} value={`v${rule.version_number ?? 1}`} />
            <div>
              <Label>{t("Status")}</Label>
              <RuleStatusBadge status={rule.status} />
            </div>
            <Meta label={t("Type")} value={rule.rule_type_code} />
            <Meta label={t("Stage")} value={rule.stage_code || "—"} />
            <Meta label={t("Service")} value={rule.service_type} />
            <Meta label={t("Priority")} value={String(rule.priority ?? "—")} />
            <Meta
              label={t("Specificity")}
              value={String(rule.specificity_score ?? "—")}
              hint={t(
                "Scored from the conditions. Breaks a tie before priority does.",
              )}
            />
            <Meta
              label={t("Effective")}
              value={`${rule.effective_from ?? "—"}${rule.effective_to ? ` → ${rule.effective_to}` : ""}`}
            />
          </div>

          {canEdit && (
            <div className="bg-card border border-border rounded-xl p-4 mb-6 flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-foreground mr-2">
                {t("Lifecycle")}
              </span>
              <button
                onClick={runValidation}
                disabled={validate.isPending}
                className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
              >
                {validate.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ShieldCheck className="h-4 w-4" />
                )}
                {t("Validate")}
              </button>
              {nextStatuses.map((status) => {
                const needsApprover =
                  status === "REVIEWED" || status === "APPROVED";
                if (needsApprover && !canApprove) return null;
                return (
                  <button
                    key={status}
                    onClick={() => move(status)}
                    disabled={changeStatus.isPending}
                    className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted transition disabled:opacity-50"
                  >
                    {t("Move to")} {status}
                  </button>
                );
              })}
              {rule.status === "DRAFT" && versions.length === 1 && (
                <button
                  onClick={discard}
                  disabled={remove.isPending}
                  className="ml-auto inline-flex items-center gap-2 rounded-lg border border-destructive/30 text-destructive px-3 py-1.5 text-sm font-medium hover:bg-destructive/10 transition disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" /> {t("Delete draft")}
                </button>
              )}
            </div>
          )}

          {report && (
            <div className="mb-6">
              <ValidationPanel report={report} />
            </div>
          )}

          {rule.issues.length > 0 && (
            <section className="bg-card border border-destructive/20 rounded-xl p-4 mb-6">
              <h2 className="text-sm font-semibold text-foreground">
                {t("Validation issues")}
              </h2>
              <ul className="mt-2 space-y-1">
                {rule.issues.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className="text-sm text-muted-foreground"
                  >
                    <span className="font-medium text-destructive">
                      {issue.severity} · {issue.code}
                    </span>{" "}
                    — {issue.message}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="flex items-center gap-1 border-b border-border mb-5">
            {(
              [
                ["logic", t("Logic")],
                ["versions", `${t("Versions")} (${versions.length})`],
                ["history", t("Audit trail")],
              ] as [Tab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  tab === key
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "logic" &&
            (editing ? (
              <RuleEditor
                form={form}
                onForm={(patch) =>
                  setForm((current) => ({ ...current, ...patch }))
                }
                conditions={conditionRows}
                onConditions={setConditionRows}
                actions={actionRows}
                onActions={setActionRows}
                errorPaths={errorPaths}
                lockIdentity
              />
            ) : (
              <div className="bg-card border border-border rounded-xl p-5">
                <RuleLogicView
                  conditions={conditions}
                  actions={actions}
                  conditionLogic={rule.condition_logic}
                />
              </div>
            ))}

          {tab === "versions" && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              {versionsQuery.error ? (
                <RatingError
                  error={versionsQuery.error}
                  onRetry={() => void versionsQuery.refetch()}
                />
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="px-4 py-2.5">{t("Version")}</th>
                      <th className="px-4 py-2.5">{t("Name")}</th>
                      <th className="px-4 py-2.5">{t("Status")}</th>
                      <th className="px-4 py-2.5">{t("Effective")}</th>
                      <th className="px-4 py-2.5">{t("Updated")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {versions.map((version) => (
                      <tr key={version.rule_version_id}>
                        <td className="px-4 py-3 font-medium">
                          v{version.version_number}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {rule.rule_name}
                        </td>
                        <td className="px-4 py-3">
                          <RuleStatusBadge status={version.status} />
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {version.effective_from}
                          {version.effective_to
                            ? ` → ${version.effective_to}`
                            : ""}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                          {new Date(version.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}

          {tab === "history" && (
            <section className="bg-card border border-border rounded-xl overflow-hidden">
              {auditQuery.error ? (
                <RatingError
                  error={auditQuery.error}
                  onRetry={() => void auditQuery.refetch()}
                />
              ) : audit.length === 0 ? (
                <Empty text={t("No history yet.")} />
              ) : (
                <ul className="divide-y divide-border">
                  {audit.map((entry, index) => (
                    <li
                      key={`${entry.action}-${entry.created_at}-${index}`}
                      className="px-5 py-3 flex items-start gap-3"
                    >
                      <span className="h-7 w-7 shrink-0 rounded-lg bg-muted flex items-center justify-center text-muted-foreground">
                        <History className="h-3.5 w-3.5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <span className="text-sm font-medium text-foreground">
                            {entry.action}
                          </span>
                          {entry.version_number !== null && (
                            <span className="text-[11px] text-muted-foreground">
                              v{entry.version_number}
                            </span>
                          )}
                          {entry.from_status && entry.to_status && (
                            <span className="text-[11px] text-muted-foreground">
                              {entry.from_status} → {entry.to_status}
                            </span>
                          )}
                        </div>
                        {entry.comment && (
                          <p className="text-[12px] text-muted-foreground mt-0.5">
                            {entry.comment}
                          </p>
                        )}
                      </div>
                      <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                        {entry.actor_name || "—"} ·{" "}
                        {new Date(entry.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </AppShell>
  );
}

function cloneRuleName(name: string): string {
  const base = name.trim().replace(/(?:\s+\(copy\))+$/gi, "");
  return `${base} (copy)`;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
      {children}
    </div>
  );
}

function Meta({
  label,
  value,
  mono = false,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  const body = (
    <div>
      <Label>{label}</Label>
      <div
        className={`text-sm text-foreground ${mono ? "font-mono" : "font-medium"}`}
      >
        {value}
      </div>
    </div>
  );
  return hint ? (
    <Tooltip label={hint} side="bottom">
      {body}
    </Tooltip>
  ) : (
    body
  );
}

function Empty({ text }: { text: string }) {
  return <p className="px-3 py-4 text-sm text-muted-foreground">{text}</p>;
}

function flattenConditions(
  group: CanonicalConditionGroup,
  groupIndex = 0,
): Condition[] {
  const own = group.conditions.map((condition) => ({
    sequence: condition.sequence,
    attribute: condition.attribute,
    operator: condition.operator,
    values: condition.values,
    group_index: groupIndex,
    negate: group.negated || condition.negated,
  }));
  return group.children.reduce<Condition[]>(
    (all, child, index) => [
      ...all,
      ...flattenConditions(child, groupIndex + index + 1),
    ],
    own,
  );
}

function toLegacyAction(action: CanonicalAction): Action {
  return {
    sequence: action.sequence,
    action_type: action.action_type,
    params: Object.fromEntries(
      action.parameters.map((parameter) => [parameter.name, parameter.value]),
    ),
  };
}

function canonicalToForm(rule: CanonicalRuleDetail): RuleFormState {
  return {
    ...emptyRuleForm(),
    rule_key: rule.rule_key,
    name: rule.rule_name,
    description: rule.description,
    rule_type: rule.rule_type_code,
    service_type: rule.service_type,
    priority: rule.priority ?? 100,
    stacking_policy: rule.stacking_policy,
    conflict_group: rule.conflict_group ?? "",
    condition_logic: rule.condition_logic,
    effective_from: rule.effective_from ?? "",
    effective_to: rule.effective_to ?? "",
    currency_code: rule.currency_code ?? "",
    owner: rule.owner ?? "",
    change_comment: rule.change_reason,
  };
}

function canonicalPayload(
  rule: CanonicalRuleDetail,
  form: RuleFormState,
  conditions: ConditionRow[],
  actions: ActionRow[],
) {
  const existingParameterTypes = new Map(
    rule.actions.flatMap((action) =>
      action.parameters.map(
        (parameter) =>
          [`${action.action_type}:${parameter.name}`, parameter] as const,
      ),
    ),
  );
  const conditionTypes = new Map(
    flattenCanonicalConditions(rule.conditions).map((condition) => [
      `${condition.attribute}:${condition.operator}`,
      condition,
    ]),
  );

  return {
    rule_key: rule.rule_key,
    rule_name: form.name.trim(),
    description: form.description,
    charging_mode: rule.charging_mode,
    rule_type_code: form.rule_type,
    service_type: form.service_type,
    validity: {
      effective_from: form.effective_from,
      effective_to: form.effective_to || null,
      currency_code: form.currency_code || null,
    },
    conditions: {
      logic: form.condition_logic,
      negated: false,
      label: "",
      conditions: conditions.map((condition) => {
        const stored = conditionTypes.get(
          `${condition.attribute}:${condition.operator}`,
        );
        return {
          attribute: condition.attribute,
          operator: condition.operator,
          values: condition.values,
          negated: condition.negate,
          unit: stored?.unit_code ?? null,
          currency: stored?.currency_code ?? null,
        };
      }),
      children: [],
    },
    actions: actions.map((action, actionIndex) => ({
      action_type: action.action_type,
      target_attribute:
        rule.actions.find((stored) => stored.action_type === action.action_type)
          ?.target_attribute ?? null,
      sequence: actionIndex + 1,
      parameters: Object.entries(action.params ?? {})
        .filter(([, value]) => value !== undefined && value !== "")
        .map(([name, value], parameterIndex) => {
          const stored = existingParameterTypes.get(
            `${action.action_type}:${name}`,
          );
          return {
            name,
            value,
            value_type: stored?.value_type ?? inferValueType(value),
            currency: stored?.currency_code ?? null,
            unit: stored?.unit_code ?? null,
            sequence: parameterIndex + 1,
          };
        }),
    })),
    behaviour: {
      priority: Number(form.priority) || 0,
      stacking_policy: form.stacking_policy,
      conflict_group: form.conflict_group.trim() || null,
      fallback_policy: rule.fallback_policy,
      stop_processing: rule.stop_processing,
      execution_mode: rule.execution_mode,
      condition_logic: form.condition_logic,
    },
    targets: {
      product: rule.product_code ?? null,
      offer: rule.offer_code ?? null,
      tariff_plan: rule.tariff_plan_code ?? null,
    },
    set_codes: [],
    owner: form.owner.trim() || null,
    change_reason: form.change_comment,
  };
}

// Annotated because it recurses: without a declared return type TypeScript
// cannot infer one from a self-referencing expression, and every caller of the
// flattened list ends up reading `unit_code` off `{}`.
function flattenCanonicalConditions(
  group: CanonicalConditionGroup | null,
): CanonicalCondition[] {
  if (!group) return [];
  return [
    ...group.conditions,
    ...group.children.flatMap((child) => flattenCanonicalConditions(child)),
  ];
}

function inferValueType(value: unknown): string {
  if (typeof value === "number") return "NUMBER";
  if (typeof value === "boolean") return "BOOLEAN";
  if (Array.isArray(value)) return "LIST";
  return "STRING";
}
