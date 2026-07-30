import { useMemo } from "react";
import { useT } from "@/lib/i18n";
import {
  useActionSpecs,
  useOperators,
  useRuleAttributes,
} from "@/lib/rating/hooks";
import type { Action, Condition } from "@/lib/rating/types";

// Read-only rendering of a rule's logic, used wherever the rule is frozen
// (approved or later) and on the preview step. Reads the same vocabulary as the
// builders, so labels stay identical between editing and viewing.

function formatValues(values: unknown[]): string {
  if (values.length === 0) return "";
  if (values.length === 1) return String(values[0]);
  if (values.length === 2) return `${values[0]} … ${values[1]}`;
  return values.map(String).join(", ");
}

export function RuleLogicView({
  conditions,
  actions,
  conditionLogic = "AND",
}: {
  conditions: Condition[];
  actions: Action[];
  conditionLogic?: string;
}) {
  const t = useT();
  const { data: attributes = [] } = useRuleAttributes();
  const { data: operators = [] } = useOperators();
  const { data: specs = [] } = useActionSpecs();

  const attrLabel = useMemo(
    () => new Map(attributes.map((a) => [a.key, a.label])),
    [attributes],
  );
  const opLabel = useMemo(
    () => new Map(operators.map((o) => [o.key, o.label])),
    [operators],
  );
  const actionLabel = useMemo(
    () => new Map(specs.map((s) => [s.type, s])),
    [specs],
  );

  return (
    <div className="space-y-4">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          {t("Conditions")}
        </div>
        {conditions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("None — this rule matches every CDR of its service type.")}
          </p>
        ) : (
          <div className="rounded-lg border border-border bg-muted/30 p-3 font-mono text-[12px] leading-relaxed">
            {conditions.map((c, i) => (
              <div key={c.id ?? i} className="flex flex-wrap gap-x-2">
                {i > 0 && (
                  <span className="text-muted-foreground">
                    {conditionLogic}
                  </span>
                )}
                <span className="text-foreground">
                  {attrLabel.get(c.attribute) ?? c.attribute}
                </span>
                <span className="text-primary">
                  {opLabel.get(c.operator) ?? c.operator}
                </span>
                <span className="text-foreground">
                  {formatValues(c.values)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          {t("Actions")}
        </div>
        {actions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("None.")}</p>
        ) : (
          <div className="space-y-1.5">
            {actions.map((a, i) => {
              const spec = actionLabel.get(a.action_type);
              const params = Object.entries(a.params ?? {});
              return (
                <div
                  key={a.id ?? i}
                  className="rounded-lg border border-border bg-card px-3 py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1"
                >
                  <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    {spec?.stage ?? ""}
                  </span>
                  <span className="text-sm font-medium text-foreground">
                    {spec?.label ?? a.action_type}
                  </span>
                  {params.map(([k, v]) => (
                    <span key={k} className="text-[12px] text-muted-foreground">
                      {k}=
                      <span className="text-foreground font-medium">
                        {String(v)}
                      </span>
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
