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
import { Tooltip } from "@/components/ui-kit/Tooltip";
import { useT } from "@/lib/i18n";
import { ratingError } from "@/lib/rating/api";
import {
  useCanEditRules,
  useChangeRuleStatus,
  useCloneRule,
  useDeleteRule,
  useNewRuleVersion,
  useRatingEnums,
  useRatingMe,
  useRule,
  useRuleAudit,
  useRuleVersions,
  useUpdateRule,
  useValidateRule,
} from "@/lib/rating/hooks";
import { RatingError, RatingLoading } from "@/components/rating/RatingState";
import { RuleStatusBadge } from "@/components/rating/RuleStatusBadge";
import { RuleLogicView } from "@/components/rating/RuleLogicView";
import {
  ValidationPanel,
  errorPathsOf,
} from "@/components/rating/ValidationPanel";
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
import type { RuleDetail, ValidationReport } from "@/lib/rating/types";

export const Route = createFileRoute("/rating/rules/$ruleId")({
  component: RuleDetailPage,
});

// Mirrors EDITABLE_STATUSES in the backend. Anything else is frozen and a change
// means a new version.
const EDITABLE = new Set(["DRAFT", "VALIDATED"]);

type Tab = "logic" | "versions" | "history";

function formToRule(rule: RuleDetail): RuleFormState {
  return {
    ...emptyRuleForm(),
    rule_key: rule.rule_key,
    name: rule.name,
    description: rule.description,
    rule_type: rule.rule_type,
    service_type: rule.service_type,
    category: rule.category,
    rule_set_id: rule.rule_set_id ?? "",
    product_id: rule.product_id ?? "",
    offer_id: rule.offer_id ?? "",
    tariff_plan_id: rule.tariff_plan_id ?? "",
    priority: rule.priority,
    stacking_policy: rule.stacking_policy,
    conflict_group: rule.conflict_group ?? "",
    condition_logic: rule.condition_logic,
    effective_from: rule.effective_from,
    effective_to: rule.effective_to ?? "",
    currency_code: rule.currency_code ?? "",
    owner: rule.owner ?? "",
    change_comment: "",
  };
}

function RuleDetailPage() {
  const { ruleId } = Route.useParams();
  const t = useT();
  const navigate = useNavigate();
  const canEdit = useCanEditRules();
  const { data: me } = useRatingMe();
  const canApprove = !!me?.permissions?.ratingApprovals?.edit;
  const { data: enums } = useRatingEnums();

  const { data: rule, isLoading, error, refetch } = useRule(ruleId);
  const { data: versions = [] } = useRuleVersions(ruleId);
  const { data: audit = [] } = useRuleAudit(ruleId);

  const update = useUpdateRule(ruleId);
  const validate = useValidateRule(ruleId);
  const changeStatus = useChangeRuleStatus(ruleId);
  const newVersion = useNewRuleVersion(ruleId);
  const clone = useCloneRule(ruleId);
  const remove = useDeleteRule();

  const [tab, setTab] = useState<Tab>("logic");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<RuleFormState>(emptyRuleForm);
  const [conditions, setConditions] = useState<ConditionRow[]>([]);
  const [actions, setActions] = useState<ActionRow[]>([]);
  const [report, setReport] = useState<ValidationReport | undefined>();

  // Reload the editable copy whenever we switch rule (or a save returns a new
  // server state) — never while the user is mid-edit, which would discard input.
  useEffect(() => {
    if (!rule || editing) return;
    setForm(formToRule(rule));
    setConditions(rule.conditions.map((c) => newConditionRow(c)));
    setActions(rule.actions.map((a) => newActionRow(a)));
  }, [rule, editing]);

  const errorPaths = useMemo(() => errorPathsOf(report), [report]);
  const frozen = !rule || !EDITABLE.has(rule.status);
  const nextStatuses = enums?.allowed_transitions?.[rule?.status ?? ""] ?? [];

  const save = async () => {
    try {
      await update.mutateAsync(toRulePayload(form, conditions, actions));
      setEditing(false);
      setReport(undefined);
      toast.success(t("Rule updated"));
    } catch (err) {
      toast.error(t("Couldn't save"), { description: ratingError(err) });
    }
  };

  const runValidation = async () => {
    try {
      const r = await validate.mutateAsync();
      setReport(r);
      if (r.valid) {
        toast.success(t("Validation passed"), {
          description: r.warning_count
            ? `${r.warning_count} ${t("warnings — these don't block submission.")}`
            : undefined,
        });
      } else {
        toast.error(`${r.error_count} ${t("validation errors")}`);
      }
    } catch (err) {
      toast.error(t("Validation failed to run"), {
        description: ratingError(err),
      });
    }
  };

  const move = async (status: string) => {
    try {
      await changeStatus.mutateAsync({ status });
      toast.success(`${t("Rule moved to")} ${status}`);
      setReport(undefined);
    } catch (err) {
      toast.error(t("Transition refused"), { description: ratingError(err) });
    }
  };

  const cutVersion = async () => {
    try {
      const draft = await newVersion.mutateAsync({ change_comment: "" });
      toast.success(`${t("Created version")} ${draft.version}`);
      navigate({ to: "/rating/rules/$ruleId", params: { ruleId: draft.id } });
    } catch (err) {
      toast.error(t("Couldn't create a new version"), {
        description: ratingError(err),
      });
    }
  };

  const duplicate = async () => {
    if (!rule) return;
    try {
      const copy = await clone.mutateAsync({ name: `${rule.name} (copy)` });
      toast.success(t("Rule cloned"), { description: copy.rule_key });
      navigate({ to: "/rating/rules/$ruleId", params: { ruleId: copy.id } });
    } catch (err) {
      toast.error(t("Couldn't clone the rule"), {
        description: ratingError(err),
      });
    }
  };

  const discard = async () => {
    if (!rule) return;
    try {
      await remove.mutateAsync(rule.id);
      toast.success(t("Draft deleted"));
      navigate({ to: "/rating/rules" });
    } catch (err) {
      toast.error(t("Couldn't delete"), { description: ratingError(err) });
    }
  };

  return (
    <AppShell>
      {isLoading && <RatingLoading label="Loading rule…" />}
      {error && <RatingError error={error} onRetry={() => refetch()} />}

      {rule && (
        <>
          <PageHeader
            title={rule.name}
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
                      <GitBranch className="h-4 w-4" /> {t("New version")}
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

          {/* --- Identity strip ---------------------------------------------- */}
          <div className="bg-card border border-border rounded-xl px-5 py-4 mb-6 flex flex-wrap items-center gap-x-8 gap-y-3">
            <Meta label={t("Key")} value={rule.rule_key} mono />
            <Meta label={t("Version")} value={`v${rule.version}`} />
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
                {t("Status")}
              </div>
              <RuleStatusBadge status={rule.status} />
            </div>
            <Meta label={t("Type")} value={rule.rule_type} />
            <Meta label={t("Stage")} value={rule.execution_stage} />
            <Meta label={t("Service")} value={rule.service_type} />
            <Meta label={t("Priority")} value={String(rule.priority)} />
            <Meta
              label={t("Specificity")}
              value={String(rule.specificity)}
              hint={t(
                "Scored from the conditions. Breaks a tie before priority does.",
              )}
            />
            <Meta
              label={t("Effective")}
              value={`${rule.effective_from}${rule.effective_to ? ` → ${rule.effective_to}` : ""}`}
            />
          </div>

          {/* --- Lifecycle --------------------------------------------------- */}
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
                // Maker-checker: submitting for review and approving both need
                // the approvals permission, which an analyst does not have.
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

              {rule.status === "DRAFT" && rule.version === 1 && (
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

          {/* --- Tabs -------------------------------------------------------- */}
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
                onForm={(p) => setForm((f) => ({ ...f, ...p }))}
                conditions={conditions}
                onConditions={setConditions}
                actions={actions}
                onActions={setActions}
                errorPaths={errorPaths}
                lockIdentity
              />
            ) : (
              <div className="bg-card border border-border rounded-xl p-5">
                <RuleLogicView
                  conditions={rule.conditions}
                  actions={rule.actions}
                  conditionLogic={rule.condition_logic}
                />
              </div>
            ))}

          {tab === "versions" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40 text-left">
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Version")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Name")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Status")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Effective")}
                    </th>
                    <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("Updated")}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {versions.map((v) => (
                    <tr
                      key={v.id}
                      className={`hover:bg-muted/30 transition-colors ${v.id === rule.id ? "bg-primary/5" : ""}`}
                    >
                      <td className="px-4 py-3">
                        <Link
                          to="/rating/rules/$ruleId"
                          params={{ ruleId: v.id }}
                          className="font-medium text-foreground hover:text-primary"
                        >
                          v{v.version}
                        </Link>
                        {v.id === rule.id && (
                          <span className="ml-2 text-[10px] uppercase tracking-wide text-primary">
                            {t("viewing")}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {v.name}
                      </td>
                      <td className="px-4 py-3">
                        <RuleStatusBadge status={v.status} />
                      </td>
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                        {v.effective_from}
                        {v.effective_to ? ` → ${v.effective_to}` : ""}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                        {new Date(v.updated_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tab === "history" && (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              {audit.length === 0 ? (
                <p className="px-5 py-6 text-sm text-muted-foreground">
                  {t("No history yet.")}
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {audit.map((entry) => (
                    <li
                      key={entry.id}
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
                          {entry.version !== null && (
                            <span className="text-[11px] text-muted-foreground">
                              v{entry.version}
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
                        {entry.actor_name ?? "—"} ·{" "}
                        {new Date(entry.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}

function Meta({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  const body = (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">
        {label}
      </div>
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
