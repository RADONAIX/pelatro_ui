import { useCallback, useEffect, useState } from "react";
import type { RuleCategory } from "./platform-metadata";

/** One attribute mapping. For a Single comparison only `left` is used. */
export type AttrPair = { left: string; right: string };

export type ComparisonMode = "Single" | "Multiple";

/**
 * The two-table shape, for categories that inherently compare datasets rather
 * than inspect one.
 *
 * A flat Record<string,string> cannot express this: reconciling AIR Raw against
 * AIR Processed needs a join on possibly several keys AND several measured
 * values (tran_amt and acc_balance), and each is a pair of columns from two
 * different tables. That is what produces the four outcomes the AIR
 * Reconciliation Report reports — RAW_ONLY / PROC_ONLY come from the key join
 * failing, AMOUNT_MISMATCH from a metric pair differing on a row that joined.
 *
 * Single keeps the same container so toggling modes mid-edit doesn't discard
 * work; table2 and keys are simply ignored while mode is "Single".
 */
export type RuleComparison = {
  mode: ComparisonMode;
  table1: string;
  table2: string;
  /** Measured values compared between the tables. */
  metrics: AttrPair[];
  /** Attributes used to join records across the tables. Multiple only. */
  keys: AttrPair[];
};

/**
 * Categories authored with the two-table comparison shape instead of flat
 * params. Add a category here and the builder switches it over — nothing else
 * needs to change.
 */
export const COMPARISON_CATEGORIES: ReadonlySet<RuleCategory> = new Set<RuleCategory>([
  "Reconciliation",
]);

/**
 * What should happen in Case Management when this rule breaches.
 *
 * Policy only — nothing evaluates a CustomRule, so no case is raised
 * automatically. The Controls table exposes a manual "Raise case" action that
 * reads this, and createCaseFromRule() in src/lib/casesDemo.ts is the seam a
 * real evaluator would call with exactly the same input.
 *
 * Vocabularies are deliberately the ones cases already use (SEVERITIES,
 * FINDING_TYPES, STREAMS in casesDemo.ts) rather than parallel enums — that is
 * what makes a rule-raised case indistinguishable from a hand-raised one.
 */
export type CaseRouting = {
  raiseCase: boolean;
  /** Case priority. Seeded from the rule's severity; cases also allow "low". */
  priority: "low" | "medium" | "high" | "critical";
  /** Free text, "" = unassigned. Matches every other assignment control here. */
  owner: string;
  /** A FINDING_TYPES key. */
  findingType: string;
  /** A STREAMS value. */
  stream: string;
};

export const emptyCaseRouting = (severity: CustomRule["severity"]): CaseRouting => ({
  raiseCase: false,
  priority: severity,
  owner: "",
  findingType: "control_rule",
  stream: "AIR",
});

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
  /**
   * Present only when the author opted in. Optional so rules already in
   * localStorage from before this shape existed still parse.
   */
  caseRouting?: CaseRouting;
  /**
   * Present only for COMPARISON_CATEGORIES. Optional so rules already in
   * localStorage from before this shape existed still parse.
   */
  comparison?: RuleComparison;
  createdAt: string;
};

export const emptyComparison = (): RuleComparison => ({
  mode: "Multiple",
  table1: "",
  table2: "",
  metrics: [{ left: "", right: "" }],
  keys: [{ left: "", right: "" }],
});

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
  // Datasets, join keys and compared values now come from the comparison block
  // (see COMPARISON_CATEGORIES) — free-text feed names and a single match key
  // could not express a composite join or more than one measured value.
  Reconciliation: [{ key: "tolerance", label: "Tolerance %", placeholder: "0.1" }],
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