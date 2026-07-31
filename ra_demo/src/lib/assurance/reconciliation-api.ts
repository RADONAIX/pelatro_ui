import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Generated reconciliation reports.
//
// One report per compiled Reconciliation rule. The list is fetched per
// assurance rather than declared anywhere, which is what lets a rule created a
// minute ago appear in the Reports menu without a deploy.
// ---------------------------------------------------------------------------

export const RECON_STATUSES = [
  "MATCH",
  "MISMATCH",
  "RAW_MISSING",
  "PROCESSED_MISSING",
] as const;

export type ReconStatus = (typeof RECON_STATUSES)[number];

export interface ReconReport {
  key: string;
  ruleId: string;
  title: string;
  group: string;
  assurance: string;
  /** false while the rule has never completed a run — rendered as "soon". */
  available: boolean;
  status: "Pending" | "Ready" | "Failed";
  description: string;
  outputTable: string;
  frequency: string;
  lastRunAt: string | null;
  nextRunAt: string | null;
  lastError: string | null;
}

export interface ReconPage {
  key: string;
  title: string;
  ruleId: string;
  /** Selected comparison keys, then metrics, then "status". Nothing else. */
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
  executionId: string | null;
  executedAt: string | null;
  statusFilter?: ReconStatus | null;
  note?: string;
}

export interface ReconExecution {
  execution_id: string;
  rule_id: string;
  trigger_source: string;
  status: "Running" | "Succeeded" | "Failed";
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  rows_total: number;
  rows_match: number;
  rows_mismatch: number;
  rows_raw_missing: number;
  rows_processed_missing: number;
  error: string | null;
  triggered_by: string;
}

/** Recon report keys are prefixed by the backend, so the Reports page can tell
 *  a generated report from a platform one without another lookup. */
export const RECON_KEY_PREFIX = "recon_";

export function isReconReportKey(key: string | undefined): boolean {
  return !!key && key.startsWith(RECON_KEY_PREFIX);
}

export async function fetchReconReports(assurance?: string): Promise<ReconReport[]> {
  const { data } = await api.get<ReconReport[]>("/reconciliation/reports", {
    params: assurance ? { assurance } : undefined,
  });
  return data;
}

export async function fetchReconPage(
  key: string,
  opts: { status?: ReconStatus | null; limit: number; offset: number },
): Promise<ReconPage> {
  const { data } = await api.get<ReconPage>(
    `/reconciliation/reports/${encodeURIComponent(key)}`,
    {
      params: {
        limit: opts.limit,
        offset: opts.offset,
        ...(opts.status ? { status: opts.status } : {}),
      },
    },
  );
  return data;
}

export async function fetchReconExecutions(ruleId: string): Promise<ReconExecution[]> {
  const { data } = await api.get<ReconExecution[]>(
    `/reconciliation/rules/${encodeURIComponent(ruleId)}/executions`,
  );
  return data;
}

export async function runReconNow(ruleId: string): Promise<{
  executionId: string;
  status: string;
  durationMs: number;
  counts: Record<string, number>;
}> {
  const { data } = await api.post(
    `/reconciliation/rules/${encodeURIComponent(ruleId)}/run`,
  );
  return data;
}
