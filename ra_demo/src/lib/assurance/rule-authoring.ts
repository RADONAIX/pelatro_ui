import { useCallback, useEffect, useState } from "react";
import type { RuleCategory } from "./platform-metadata";
import {
  createRule,
  deleteRule,
  fetchRules,
  setRuleState,
  updateRule,
  type RuleDraft,
} from "./rules-api";

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
 * POSTs to the case service; the real evaluator posts to /api/cases/ingest with
 * the same rule identity plus the breach figures.
 *
 * Deliberately small: everything else the case service wants is already on the
 * rule or its app — assurance from the app, module from the rule's entity,
 * issue type from its category — so asking the author again would be a second
 * place for the same fact to be wrong. Only priority and owner are choices the
 * rule cannot answer for itself.
 */
export type CaseRouting = {
  raiseCase: boolean;
  /** Case priority. Seeded from the rule's severity; cases also allow "low". */
  priority: "low" | "medium" | "high" | "critical";
  /** Free text, "" = unassigned. Matches every other assignment control here. */
  owner: string;
};

export const emptyCaseRouting = (severity: CustomRule["severity"]): CaseRouting => ({
  raiseCase: false,
  priority: severity,
  owner: "",
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

/**
 * The authored rules for ONE assurance, read from and written to the backend.
 *
 * Rules used to live in localStorage, which meant they were invisible to
 * everyone but the browser that wrote them and gone with a cleared cache. They
 * are now rows in application_schema.assurance_rule (rafms_rating), fetched per
 * assurance — `appId` is the filter the server applies, so switching the
 * Assurance Scope re-fetches rather than re-filtering a shared list.
 *
 * Every mutation returns the stored row and folds THAT into state, so what the
 * table shows is what the database holds — no optimistic guess at the id the
 * server assigns or the timestamps it stamps.
 */
export function useCustomRules(appId: string) {
  const [rules, setRules] = useState<CustomRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setRules(await fetchRules(appId));
      setError(null);
    } catch (e) {
      // Leave `rules` alone: a failed refresh should not blank a table the user
      // is reading. The message drives an inline banner instead.
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [appId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchRules(appId)
      .then((next) => {
        if (cancelled) return;
        setRules(next);
        setError(null);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        // A different assurance's rules must never be left on screen, so this
        // one DOES clear — unlike reload above, there is nothing valid to keep.
        setRules([]);
        setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [appId]);

  const addRule = useCallback(
    async (rule: RuleDraft, prefix: string) => {
      const created = await createRule(appId, prefix, rule);
      setRules((prev) => [created, ...prev]);
      return created;
    },
    [appId],
  );

  const editRule = useCallback(async (id: string, rule: RuleDraft) => {
    const saved = await updateRule(id, rule);
    setRules((prev) => prev.map((r) => (r.id === id ? saved : r)));
    return saved;
  }, []);

  const removeRule = useCallback(async (id: string) => {
    await deleteRule(id);
    setRules((prev) => prev.filter((r) => r.id !== id));
  }, []);

  const toggleState = useCallback(
    async (id: string) => {
      const current = rules.find((r) => r.id === id);
      if (!current) return;
      const saved = await setRuleState(id, current.state === "Active" ? "Draft" : "Active");
      setRules((prev) => prev.map((r) => (r.id === id ? saved : r)));
    },
    [rules],
  );

  return { rules, loading, error, reload, addRule, editRule, removeRule, toggleState };
}