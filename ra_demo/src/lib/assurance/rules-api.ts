import { api } from "@/lib/api";
import type { CustomRule } from "./rule-authoring";

// ---------------------------------------------------------------------------
// Authored rules, served by RA_Backend from
// application_schema.assurance_rule in rafms_rating.
//
// `assurance` is on every list and create because the server requires it — the
// screen you are on decides which rules exist, so Billing Assurance can only
// ever read billing rules. There is no "all assurances" call to reach for by
// accident.
//
// The wire shape IS CustomRule (the API returns camelCase and appId), so
// nothing is mapped here beyond dropping the server-owned timestamps the UI
// does not render.
// ---------------------------------------------------------------------------

/** The fields an author supplies. Everything else is assigned by the server. */
export type RuleDraft = Omit<CustomRule, "id" | "appId" | "createdAt">;

function toBody(draft: RuleDraft) {
  return {
    name: draft.name,
    description: draft.description,
    category: draft.category,
    entity: draft.entity,
    severity: draft.severity,
    frequency: draft.frequency,
    state: draft.state,
    params: draft.params ?? {},
    caseRouting: draft.caseRouting ?? null,
    comparison: draft.comparison ?? null,
  };
}

export async function fetchRules(assurance: string): Promise<CustomRule[]> {
  const { data } = await api.get<CustomRule[]>("/assurance-rules", {
    params: { assurance },
  });
  return data;
}

export async function createRule(
  assurance: string,
  prefix: string,
  draft: RuleDraft,
): Promise<CustomRule> {
  const { data } = await api.post<CustomRule>("/assurance-rules", toBody(draft), {
    params: { assurance, prefix },
  });
  return data;
}

export async function updateRule(id: string, draft: RuleDraft): Promise<CustomRule> {
  const { data } = await api.put<CustomRule>(
    `/assurance-rules/${encodeURIComponent(id)}`,
    toBody(draft),
  );
  return data;
}

export async function setRuleState(
  id: string,
  state: CustomRule["state"],
): Promise<CustomRule> {
  const { data } = await api.post<CustomRule>(
    `/assurance-rules/${encodeURIComponent(id)}/state`,
    null,
    { params: { state } },
  );
  return data;
}

export async function deleteRule(id: string): Promise<void> {
  await api.delete(`/assurance-rules/${encodeURIComponent(id)}`);
}
