import { useCallback, useEffect, useState } from "react";
import type { RuleCategory } from "./platform-metadata";

export type CustomRule = {
  id: string;
  appId: string;
  name: string;
  description: string;
  category: RuleCategory;
  entity: string;
  severity: "critical" | "high" | "medium";
  frequency: "Real-time" | "Hourly" | "Daily" | "Cycle";
  state: "Draft" | "Active";
  params: Record<string, string>;
  createdAt: string;
};

/**
 * Parameter contract per primitive rule category. The rule engine is universal;
 * the category selects which parameters the author must supply.
 */
export const CATEGORY_PARAMS: Record<
  RuleCategory,
  { key: string; label: string; placeholder: string }[]
> = {
  Completeness: [
    { key: "sourceFeed", label: "Source feed", placeholder: "MSC switch export" },
    { key: "targetFeed", label: "Target feed", placeholder: "Mediation output" },
    { key: "tolerance", label: "Tolerance %", placeholder: "0.5" },
  ],
  Reconciliation: [
    { key: "sourceFeed", label: "Left dataset", placeholder: "Mediation output" },
    { key: "targetFeed", label: "Right dataset", placeholder: "Billing input" },
    { key: "matchKey", label: "Match key", placeholder: "cdr_id" },
    { key: "tolerance", label: "Tolerance %", placeholder: "0.1" },
  ],
  Comparison: [
    { key: "leftField", label: "Left field", placeholder: "rated_amount" },
    { key: "operator", label: "Operator", placeholder: "!=" },
    { key: "rightField", label: "Right field", placeholder: "billed_amount" },
  ],
  Aggregation: [
    { key: "measure", label: "Measure", placeholder: "duration_sec" },
    { key: "groupBy", label: "Group by", placeholder: "msc_id, service_day" },
    { key: "aggregate", label: "Aggregate", placeholder: "SUM" },
  ],
  Calculation: [
    { key: "formula", label: "Expected formula", placeholder: "units * rate - discount" },
    { key: "compareField", label: "Compare against", placeholder: "charged_amount" },
    { key: "tolerance", label: "Tolerance", placeholder: "0.01" },
  ],
  Threshold: [
    { key: "measure", label: "Measure", placeholder: "zero_duration_ratio" },
    { key: "operator", label: "Operator", placeholder: ">" },
    { key: "limit", label: "Limit", placeholder: "2" },
  ],
  Sequence: [
    { key: "sequenceField", label: "Sequence field", placeholder: "file_seq_no" },
    { key: "partitionBy", label: "Partition by", placeholder: "msc_id" },
  ],
  Duplicate: [
    { key: "matchKey", label: "Duplicate key", placeholder: "imsi, start_time, duration" },
    { key: "window", label: "Window", placeholder: "24h" },
  ],
  Existence: [
    { key: "field", label: "Required field", placeholder: "rate_plan_id" },
    { key: "scope", label: "Scope filter", placeholder: "event_type = VOICE" },
  ],
  "Referential Integrity": [
    { key: "childField", label: "Child field", placeholder: "subscriber_id" },
    { key: "parentEntity", label: "Parent entity", placeholder: "Subscriber" },
  ],
  "Pattern Matching": [
    { key: "field", label: "Field", placeholder: "msisdn" },
    { key: "pattern", label: "Pattern", placeholder: "^\\\\+?[1-9][0-9]{7,14}$" },
  ],
  Statistical: [
    { key: "measure", label: "Measure", placeholder: "daily_volume" },
    { key: "baseline", label: "Baseline window", placeholder: "28d" },
    { key: "sigma", label: "Deviation (sigma)", placeholder: "3" },
  ],
  "ML Prediction": [
    { key: "model", label: "Model", placeholder: "leakage-anomaly-v3" },
    { key: "scoreThreshold", label: "Score threshold", placeholder: "0.85" },
  ],
  Temporal: [
    { key: "startField", label: "Start event", placeholder: "switch_write_ts" },
    { key: "endField", label: "End event", placeholder: "mediation_load_ts" },
    { key: "maxLag", label: "Max lag", placeholder: "4h" },
  ],
  "Graph Relationship": [
    { key: "nodeEntity", label: "Node entity", placeholder: "Partner" },
    { key: "edgeRule", label: "Edge condition", placeholder: "settles_with" },
    { key: "maxDepth", label: "Max depth", placeholder: "3" },
  ],
};

const KEY = "radonaix_assura_rules_v1";

function read(): CustomRule[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as CustomRule[]) : [];
  } catch {
    return [];
  }
}

export function useCustomRules(appId: string) {
  const [rules, setRules] = useState<CustomRule[]>([]);

  useEffect(() => {
    setRules(read().filter((r) => r.appId === appId));
  }, [appId]);

  const persist = useCallback(
    (next: CustomRule[]) => {
      const others = read().filter((r) => r.appId !== appId);
      window.localStorage.setItem(KEY, JSON.stringify([...others, ...next]));
      setRules(next);
    },
    [appId],
  );

  const addRule = useCallback(
    (rule: Omit<CustomRule, "id" | "appId" | "createdAt">, prefix: string) => {
      const seq = read().filter((r) => r.appId === appId).length + 901;
      const created: CustomRule = {
        ...rule,
        id: `${prefix}${seq}`,
        appId,
        createdAt: new Date().toISOString(),
      };
      persist([...rules, created]);
      return created;
    },
    [appId, persist, rules],
  );

  const removeRule = useCallback(
    (id: string) => persist(rules.filter((r) => r.id !== id)),
    [persist, rules],
  );

  const toggleState = useCallback(
    (id: string) =>
      persist(
        rules.map((r) =>
          r.id === id ? { ...r, state: r.state === "Active" ? "Draft" : "Active" } : r,
        ),
      ),
    [persist, rules],
  );

  return { rules, addRule, removeRule, toggleState };
}