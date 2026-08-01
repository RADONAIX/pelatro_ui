import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Generated reconciliation reports.
//
// One report per compiled Reconciliation rule. The list is fetched per
// assurance rather than declared anywhere, which is what lets a rule created a
// minute ago appear in the Reports menu without a deploy.
// ---------------------------------------------------------------------------

// A two-table reconciliation and a single-table sequence answer different
// questions, so they report different statuses. The server tells the view which
// set a given report uses (`statuses` on the page) — these are the union, for
// typing and for colouring.
export const RECON_STATUSES = [
  "MATCH",
  "MISMATCH",
  // Named after the side with no record: TABLE1_MISSING means the key was not
  // found in Table 1. (Formerly PROCESSED_MISSING / RAW_MISSING, which assumed
  // Table 1 is always "processed" and Table 2 always "raw".)
  "TABLE1_MISSING",
  "TABLE2_MISSING",
] as const;

export const SEQUENCE_STATUSES = ["PRESENT", "GAP", "DUPLICATE"] as const;

export type ReconStatus =
  | (typeof RECON_STATUSES)[number]
  | (typeof SEQUENCE_STATUSES)[number];

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
  /**
   * The subset of `columns` that identify a record rather than measure it.
   *
   * Served because the keys/metrics boundary inside `columns` is not otherwise
   * visible, and a client sampling "one row per subject" has to know where it
   * falls. Optional: a report generated before the backend sent this has none.
   */
  keyColumns?: string[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
  executionId: string | null;
  executedAt: string | null;
  statusFilter?: ReconStatus | null;
  /** The free-text filter the server applied, echoed back. */
  search?: string | null;
  /** "reconciliation" | "sequence" | "duplicate". */
  kind?: string;
  /** The statuses THIS report can produce — drives the filter chips. */
  statuses?: ReconStatus[];
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
  rows_table1_missing: number;
  rows_table2_missing: number;
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
  opts: {
    status?: ReconStatus | null;
    /** Matched against every column, server-side — see reportDownloadUrl. */
    search?: string;
    limit: number;
    offset: number;
  },
): Promise<ReconPage> {
  const { data } = await api.get<ReconPage>(
    `/reconciliation/reports/${encodeURIComponent(key)}`,
    {
      params: {
        limit: opts.limit,
        offset: opts.offset,
        ...(opts.status ? { status: opts.status } : {}),
        ...(opts.search ? { search: opts.search } : {}),
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


// ---------------------------------------------------------------------------
// Execution-centric reports: one report per run, with its own summary.
// ---------------------------------------------------------------------------

export interface ExecutionSummary {
  executionId: string;
  reportId: number;
  ruleId: string;
  ruleName: string;
  kind: string;
  status: string;
  executionStart: string | null;
  executionEnd: string | null;
  durationMs: number | null;
  rowsScanned: number;
  rowsReturned: number;
  reportCount: number;
  caseCreated: boolean;
  caseReference: string | null;
  trigger: string;
  triggeredBy: string;
  outputTable: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
}

export interface RuleReportRow {
  id: number;
  execution_id: string;
  rule_id: string;
  report_count: number;
  created_at: string;
  status: string;
  duration_ms: number | null;
  rows_scanned: number;
  rows_returned: number;
  case_raised: boolean;
  case_reference: string | null;
}

export async function fetchRuleReports(ruleId: string): Promise<RuleReportRow[]> {
  const { data } = await api.get<RuleReportRow[]>(
    `/reconciliation/rules/${encodeURIComponent(ruleId)}/reports`,
  );
  return data;
}

export async function fetchExecutionReport(
  executionId: string,
  opts: { limit: number; offset: number },
): Promise<ExecutionSummary> {
  const { data } = await api.get<ExecutionSummary>(
    `/reconciliation/reports/execution/${encodeURIComponent(executionId)}`,
    { params: opts },
  );
  return data;
}

export async function executeRule(ruleId: string) {
  const { data } = await api.post(
    `/reconciliation/rules/${encodeURIComponent(ruleId)}/execute`,
  );
  return data;
}

/**
 * Download URL for a stored report.
 *
 * Built rather than fetched: the file is streamed and can be very large, so it
 * is handed to the browser to download instead of being pulled through axios
 * into memory first.
 */
function downloadParams(
  fmt: "csv" | "excel",
  filters: { status?: ReconStatus | null; search?: string },
): URLSearchParams {
  const params = new URLSearchParams({ fmt });
  // The download carries the SAME filters the screen shows. Downloading a
  // filtered report and receiving every row is a quiet way to hand someone the
  // wrong numbers.
  if (filters.status) params.set("status", filters.status);
  if (filters.search) params.set("search", filters.search);
  return params;
}

/** The filename the server chose, or a sensible fallback. */
function filenameFrom(disposition: unknown, fallback: string): string {
  const match = /filename="?([^";]+)"?/.exec(String(disposition ?? ""));
  return match?.[1] ?? fallback;
}

/**
 * Download a stored report, authenticated.
 *
 * NOT a plain link. The download endpoint requires a bearer token, and a token
 * in sessionStorage is only ever attached by the axios interceptor — a browser
 * navigating to an `<a href>` sends no Authorization header, so every such
 * click came back 401 and nothing was saved. The response is fetched here with
 * the header attached and handed to the browser as a blob.
 *
 * The cost is that the file is buffered in memory rather than streamed to disk,
 * which is why the server still streams its side: the alternative is a
 * short-lived signed URL, worth adding if reports ever outgrow a browser's
 * memory.
 */
export async function downloadReport(
  executionId: string,
  fmt: "csv" | "excel",
  filters: { status?: ReconStatus | null; search?: string } = {},
): Promise<void> {
  const { data, headers } = await api.get<Blob>(
    `/reconciliation/reports/execution/${encodeURIComponent(executionId)}/download?${downloadParams(fmt, filters)}`,
    { responseType: "blob" },
  );

  const url = URL.createObjectURL(data);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filenameFrom(
      headers?.["content-disposition"],
      `${executionId.slice(0, 8)}.${fmt === "excel" ? "xls" : "csv"}`,
    );
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    // Revoked on a tick, not immediately: Safari cancels a download whose
    // object URL is released in the same task as the click.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
