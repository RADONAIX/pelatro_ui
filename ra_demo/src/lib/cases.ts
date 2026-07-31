// Case management client — talks to the FastAPI backend (app/routers/cases.py).
//
// Cases are raised two ways and both land in the same store: the rule engine
// posts to /api/cases/ingest when a control fails, and analysts create them
// from the Add Case dialog. Every case therefore carries the assurance, the
// sub-module, the rule that fired and its issue type — which is exactly what
// the filters below are built on.

// Same convention as the pipelines client: its own base so the cases service can
// live on a different host/port. Empty (as in .env.production) means same-origin,
// where nginx proxies /api.
//
// 127.0.0.1, not "localhost": on this machine localhost resolves to ::1 first,
// and the API binds IPv4, so a browser's first connection attempt is refused and
// the request shows up as a failed fetch with provisional headers. Naming the
// IPv4 address removes the ambiguity.
const API_BASE: string = import.meta.env.VITE_CASES_API_BASE ?? "http://127.0.0.1:8001";

// The signed-in demo analyst. Cases owned by this name populate "Self Assigned"
// and are what "Assign to me" writes.
export const CURRENT_ANALYST = "super_admin";

// ---------------------------------------------------------------------------
// Types — mirror the backend schemas (app/schemas.py), which serialise camelCase
// ---------------------------------------------------------------------------

export const STATUSES = ["Open", "In Progress", "Resolved", "Closed", "Cancelled"] as const;
export const SEVERITIES = ["low", "medium", "high", "critical"] as const;
export const ORIGINS = ["auto_detected", "analyst_raised"] as const;
export const ACTIONS = [
  "NA", "Escalated to carrier", "Adjusted & rebilled", "Waived",
  "Config fix raised", "Re-ingest requested", "Under review",
] as const;

export type CaseStatus = (typeof STATUSES)[number];
export type CaseSeverity = (typeof SEVERITIES)[number];

export interface CaseMismatch {
  id: string;
  position: number;
  recordRef: string;
  entity: string;
  subscriber: string;
  field: string;
  expectedValue: string | null;
  actualValue: string | null;
  delta: string | null;
  status: string;
  occurredAt: string | null;
  details: Record<string, unknown>;
}

export interface CaseComment {
  id: string;
  author: string;
  body: string;
  createdAt: string;
}

/** A file attached to a case as evidence. PDF only, enforced server-side. */
export interface CaseAttachment {
  id: string;
  caseId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  checksumSha256: string;
  uploadedBy: string;
  createdAt: string;
  /** Server-built path; join with the API base to download. */
  downloadUrl: string;
}

export interface CaseActivity {
  id: string;
  actor: string;
  action: string;
  field: string | null;
  fromValue: string | null;
  toValue: string | null;
  note: string | null;
  createdAt: string;
}

export interface AssuranceCase {
  id: string;
  reference: string;
  title: string;
  description: string;

  // Which assurance raised it, and where inside that assurance.
  assuranceCode: string;
  assuranceName: string;
  assuranceGroup: string;
  module: string;
  subModule: string;

  // The control that fired. Null for analyst-raised cases.
  ruleId: string | null;
  ruleName: string | null;
  ruleCategory: string;   // issue type: Completeness, Reconciliation, …
  ruleRunId: string | null;

  origin: string;
  severity: string;
  status: string;
  action: string;
  owner: string;          // "" means unassigned

  stream: string;
  nodeId: string;
  sourceFeed: string;
  targetFeed: string;
  linkedBatch: string;
  linkedTxnId: string;

  // What the control measured — the headline mismatch.
  expectedValue: string | null;
  actualValue: string | null;
  variance: string | null;
  variancePct: number | null;
  threshold: string | null;
  estimatedImpact: number;
  affectedCount: number;

  evidence: { name: string; kind: string; size: string } | null;
  tags: string[];
  savedInsights: { id: string; body: string; at: string }[];
  details: Record<string, unknown>;

  detectedAt: string;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;

  mismatchCount: number;

  // Present on the detail response only.
  mismatches?: CaseMismatch[];
  comments?: CaseComment[];
  activities?: CaseActivity[];
  attachments?: CaseAttachment[];
}

export interface CaseListResponse {
  items: AssuranceCase[];
  total: number;
  page: number;
  pageSize: number;
  pageCount: number;
}

export interface CaseSummary {
  total: number;
  unassigned: number;
  assigned: number;
  byStatus: Record<string, number>;
  bySeverity: Record<string, number>;
  byAssurance: Record<string, number>;
  byCategory: Record<string, number>;
  openEstimatedImpact: number;
  affectedRecords: number;
}

export interface FacetValue {
  value: string;
  label: string;
  count: number;
}

export interface CaseFacets {
  assurances: FacetValue[];
  groups: FacetValue[];
  modules: FacetValue[];
  categories: FacetValue[];
  statuses: FacetValue[];
  severities: FacetValue[];
  origins: FacetValue[];
  actions: FacetValue[];
  owners: FacetValue[];
  rules: FacetValue[];
}

export interface CatalogAssurance {
  code: string;
  name: string;
  group: string;
  modules: string[];
}

export interface CatalogMeta {
  assurances: CatalogAssurance[];
  groups: string[];
  modules: string[];
  ruleCategories: string[];
  primaryRuleCategories?: string[];
  hiddenRuleCategories?: string[];
  categoryParameters?: Record<string, string[]>;
  frequencies?: string[];
  lifecycleStates?: string[];
  ruleStatuses?: string[];
  statuses: string[];
  severities: string[];
  origins: string[];
  actions: string[];
}

// Compiled-in copy of the backend catalog (app/catalog.py). It is what the
// filter and authoring dropdowns fall back to when /api/catalog/meta cannot be
// reached — an unreachable API should degrade to "no cases found", never to
// empty dropdowns the user cannot act on.
export const FALLBACK_CATALOG: CatalogMeta = {
  assurances: [
    { code: "RA", name: "Rating Assurance", group: "Commercial Assurance",
      modules: ["Rating", "Tariff", "Discount", "Usage Events", "Subscriber"] },
    { code: "PA", name: "Partner Assurance", group: "Commercial Assurance",
      modules: ["Partner", "Settlement", "Interconnect", "Roaming", "Invoice"] },
    { code: "MA", name: "Migration Assurance", group: "Customer Assurance",
      modules: ["Subscriber", "Account", "Balance", "Product Catalog", "Legacy Feed"] },
    { code: "UA", name: "Usage Assurance", group: "Revenue Assurance",
      modules: ["Usage Events", "Subscriber", "MSC", "CDR", "Mediation", "Rating", "Billing"] },
    { code: "BA", name: "Billing Assurance", group: "Revenue Assurance",
      modules: ["Invoice", "Bill Run", "Account", "Charge", "Adjustment", "Tax"] },
    { code: "CA", name: "Charging Assurance", group: "Revenue Assurance",
      modules: ["Balance", "Session", "OCS", "Voucher", "Subscriber", "Bundle"] },
    { code: "NA", name: "Network Assurance", group: "Operations Assurance",
      modules: ["Node", "MSC", "SGSN", "Probe", "Element Feed"] },
    { code: "CL", name: "Collection Assurance", group: "Financial Assurance",
      modules: ["Payment", "Receivable", "Dunning", "Account", "Bank Feed"] },
    { code: "ME", name: "Mediation Assurance", group: "Operations Assurance",
      modules: ["AIR", "SDP", "MSC", "CDR", "File Feed", "Mediation"] },
  ],
  groups: [
    "Commercial Assurance", "Customer Assurance", "Financial Assurance",
    "Operations Assurance", "Revenue Assurance",
  ],
  modules: [],  // derived from the assurances above where needed
  ruleCategories: [
    "Completeness", "Reconciliation", "Aggregation", "Threshold", "Sequence",
    "Duplicate", "Existence", "Statistical", "Temporal", "Comparison",
    "Calculation", "Referential Integrity", "Pattern Matching", "ML Prediction",
    "Graph Relationship", "Manual Investigation",
  ],
  frequencies: ["Real-time", "Hourly", "Cycle", "Daily", "Weekly", "Monthly"],
  lifecycleStates: ["Draft", "Active", "Paused", "Retired"],
  ruleStatuses: ["PASS", "FAIL", "WARNING", "NOT_RUN"],
  statuses: [...STATUSES],
  severities: [...SEVERITIES],
  origins: [...ORIGINS],
  actions: [...ACTIONS],
};

/** Every filter the list, summary and CSV export accept. */
export interface CaseQuery {
  q?: string;
  assurance?: string[];
  group?: string[];
  module?: string[];
  category?: string[];
  status?: string[];
  severity?: string[];
  origin?: string[];
  action?: string[];
  owner?: string[];
  ruleId?: string[];
  stream?: string[];
  assignment?: "all" | "assigned" | "unassigned" | "mine";
  me?: string;
  dateFrom?: string;
  dateTo?: string;
  dateField?: "createdAt" | "detectedAt" | "updatedAt";
  openOnly?: boolean;
  page?: number;
  pageSize?: number;
  sortBy?: string;
  sortDir?: "asc" | "desc";
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

export function buildQuery(params: CaseQuery): URLSearchParams {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    // Array filters repeat the key — FastAPI reads them as a list.
    if (Array.isArray(value)) {
      for (const v of value) if (v !== "") search.append(key, String(v));
    } else {
      search.append(key, String(value));
    }
  }
  return search;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData must set its own Content-Type: the browser appends the multipart
  // boundary, and overriding it here would make the body unparseable.
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body && !isForm ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    // FastAPI puts the reason in `detail` — surface it rather than a bare code.
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const detail = body?.detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail) && detail[0]?.msg) message = detail[0].msg;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const listCases = (query: CaseQuery = {}) =>
  request<CaseListResponse>(`/api/cases?${buildQuery(query)}`);

export const fetchSummary = (query: CaseQuery = {}) =>
  request<CaseSummary>(`/api/cases/summary?${buildQuery(query)}`);

export const fetchFacets = () => request<CaseFacets>("/api/cases/facets");

export const fetchCatalog = () => request<CatalogMeta>("/api/catalog/meta");

/** One control rule as authored in the rules module. */
export interface ControlRule {
  id: string;               // UA001
  name: string;
  intent: string;
  assuranceCode: string;
  assuranceName: string;
  assuranceGroup: string;
  entityScope: string;      // the module the rule is bound to
  primitiveCategory: string;
  severity: string;
  frequency: string;
  sourceFeed: string;
  targetFeed: string;
  tolerancePct: number | null;
  params: Record<string, unknown>;
  lifecycleState: string;
  lastStatus: string;
  lastRunAt: string | null;
  casesRaised: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  openCases: number;
}

/** The provisioned controls — used to label the Rule filter and to trace a
 *  case back to the control that raised it. */
export const fetchRules = (params: {
  assurance?: string[]; category?: string[]; q?: string; pageSize?: number;
} = {}) =>
  request<{ items: ControlRule[]; total: number; page: number; pageSize: number; pageCount: number }>(
    `/api/rules?${buildQuery(params as CaseQuery)}`,
  );

/** One case with its mismatch rows, notes and audit trail. */
export const fetchCase = (id: string) =>
  request<AssuranceCase>(`/api/cases/${encodeURIComponent(id)}`);

export const fetchMismatches = (id: string, page = 1, pageSize = 100) =>
  request<{ items: CaseMismatch[]; total: number; page: number; pageSize: number; affectedCount: number }>(
    `/api/cases/${encodeURIComponent(id)}/mismatches?page=${page}&pageSize=${pageSize}`,
  );

/** URL for the server-side CSV export of the current filter set. */
export const exportCsvUrl = (query: CaseQuery = {}) =>
  `${API_BASE}/api/cases/export.csv?${buildQuery(query)}`;

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

/** Payload for the Add Case dialog. `assurance` takes a code or a full name. */
export interface CreateCasePayload {
  title: string;
  description?: string;
  assurance: string;
  module?: string;
  subModule?: string;
  ruleId?: string | null;
  ruleName?: string | null;
  ruleCategory?: string;
  severity?: string;
  status?: string;
  action?: string;
  owner?: string;
  stream?: string;
  nodeId?: string;
  sourceFeed?: string;
  targetFeed?: string;
  linkedBatch?: string;
  linkedTxnId?: string;
  expectedValue?: string | null;
  actualValue?: string | null;
  variance?: string | null;
  threshold?: string | null;
  estimatedImpact?: number;
  affectedCount?: number;
  createdBy?: string;
  mismatches?: Partial<CaseMismatch>[];
}

export const createCase = (payload: CreateCasePayload) =>
  request<AssuranceCase>("/api/cases", { method: "POST", body: JSON.stringify(payload) });

export const updateCase = (
  id: string,
  payload: Partial<{
    title: string; description: string; status: string; severity: string;
    action: string; owner: string; module: string; subModule: string;
    tags: string[]; actor: string;
  }>,
) =>
  request<AssuranceCase>(`/api/cases/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ actor: CURRENT_ANALYST, ...payload }),
  });

export const assignCase = (id: string, owner: string) =>
  request<AssuranceCase>(`/api/cases/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    body: JSON.stringify({ owner, actor: CURRENT_ANALYST }),
  });

export const setCaseStatus = (id: string, status: string, action?: string, note?: string) =>
  request<AssuranceCase>(`/api/cases/${encodeURIComponent(id)}/status`, {
    method: "POST",
    body: JSON.stringify({ status, action, note, actor: CURRENT_ANALYST }),
  });

export const addComment = (id: string, body: string, author = CURRENT_ANALYST) =>
  request<CaseComment>(`/api/cases/${encodeURIComponent(id)}/comments`, {
    method: "POST",
    body: JSON.stringify({ author, body }),
  });

export const addInsight = (id: string, body: string) =>
  request<AssuranceCase>(`/api/cases/${encodeURIComponent(id)}/insights`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });

export const deleteCase = (id: string) =>
  request<{ ok: boolean; message: string }>(`/api/cases/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

// ---------------------------------------------------------------------------
// Attachments
// ---------------------------------------------------------------------------

/** Only PDFs are accepted; the server re-checks both the type and the bytes. */
export const ACCEPTED_UPLOAD_TYPES = ["application/pdf"];
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;   // mirrors MAX_UPLOAD_MB

/** Client-side pre-check, so an obvious mistake doesn't need a round trip. */
export function validateUploadFile(file: File): string | null {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) return "Only PDF files can be attached.";
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return `File is larger than the ${MAX_UPLOAD_BYTES / 1024 / 1024} MB limit.`;
  return null;
}

export const uploadAttachments = async (
  caseId: string, files: File[], uploadedBy = CURRENT_ANALYST,
): Promise<CaseAttachment[]> => {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  form.append("uploadedBy", uploadedBy);
  // No Content-Type header: the browser has to set the multipart boundary.
  return request<CaseAttachment[]>(`/api/cases/${encodeURIComponent(caseId)}/attachments`, {
    method: "POST",
    body: form,
  });
};

export const listAttachments = (caseId: string) =>
  request<CaseAttachment[]>(`/api/cases/${encodeURIComponent(caseId)}/attachments`);

export const deleteAttachment = (caseId: string, attachmentId: string) =>
  request<{ ok: boolean; message: string }>(
    `/api/cases/${encodeURIComponent(caseId)}/attachments/${encodeURIComponent(attachmentId)}?actor=${encodeURIComponent(CURRENT_ANALYST)}`,
    { method: "DELETE" },
  );

/** Absolute URL for an attachment — the API returns a relative path. */
export const attachmentUrl = (attachment: CaseAttachment) => `${API_BASE}${attachment.downloadUrl}`;

export const fmtBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export const isUnassigned = (c: AssuranceCase) => !c.owner.trim();
export const ownerLabel = (c: AssuranceCase) => (isUnassigned(c) ? "Unassigned" : c.owner);

/** "UA001 · Missing CDR", or a dash for an analyst-raised case. */
export const ruleLabel = (c: AssuranceCase) =>
  c.ruleId ? `${c.ruleId}${c.ruleName ? ` · ${c.ruleName}` : ""}` : "—";

/** The headline mismatch, e.g. "1,284,322 → 1,281,904 (-2,418)". */
export function mismatchLabel(c: AssuranceCase): string {
  if (c.expectedValue == null && c.actualValue == null) return "—";
  const arrow = `${c.expectedValue ?? "—"} → ${c.actualValue ?? "—"}`;
  return c.variance ? `${arrow} (${c.variance})` : arrow;
}

export const fmtMoney = (n: number) => `$${new Intl.NumberFormat("en-US").format(Math.round(n))}`;

export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });

export const fmtDay = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });

/** "2h ago" — derived at render from a real timestamp, never stored. */
export function relative(isoStr: string): string {
  const ms = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
