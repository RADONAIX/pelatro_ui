import { api } from "@/lib/api";
import type { Role } from "@/lib/auth";

async function safeGet<T>(path: string, fallback: T, config?: any): Promise<T> {
  try {
    const { data } = await api.get<T>(path, config);
    return data;
  } catch {
    return fallback;
  }
}

async function safePost<T>(path: string, body: unknown, fallback: T): Promise<T> {
  try {
    const { data } = await api.post<T>(path, body);
    return data;
  } catch {
    return fallback;
  }
}

const DEMO_ACCOUNTS: Record<string, { id: string; name: string; role: Role; roleLabel: string; avatar: string; department: string }> = {
  "admin@radonaix.io": { id: "u-000", name: "Daniel Okafor", role: "admin", roleLabel: "Administrator", avatar: "DO", department: "Platform Ops" },
  "aarav.mehta@radonaix.io": { id: "u-001", name: "Aarav Mehta", role: "ra_lead", roleLabel: "RA Manager", avatar: "AM", department: "Finance Operations" },
  "priya.shah@radonaix.io": { id: "u-002", name: "Priya Shah", role: "analyst", roleLabel: "RA Analyst", avatar: "PS", department: "Assurance" },
  "viewer@radonaix.io": { id: "u-003", name: "Mei Tanaka", role: "viewer", roleLabel: "Report Viewer", avatar: "MT", department: "Compliance" },
};

const AUTH_BASE = (import.meta as any).env?.VITE_AUTH_API_BASE ?? "http://localhost:8000";

export const authService = {
  login: async (email: string, password: string) => {
    // Real backend login. No demo-token fallback and no stale localStorage
    // "disabled" pre-check — the backend is the auth authority and returns the
    // correct message (disabled account / invalid credentials / lockout), which
    // we surface verbatim.
    const res = await fetch(`${AUTH_BASE}/api/auth/login`, {
      method: "POST",
      headers: { accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      let message = `Login failed (${res.status})`;
      try { message = (await res.json())?.error?.message || message; } catch { /* non-JSON body */ }
      throw new Error(message);
    }
    return res.json() as Promise<{ token: string; refreshToken?: string; user: any }>;
  },
  // SSO: the backend completes the OAuth exchange server-side and redirects
  // back with ?token=<jwt>. We resolve the signed-in user from that token.
  ssoLoginUrl: (provider: "google" | "microsoft") =>
    `${AUTH_BASE}/api/auth/oauth/${provider}/login`,
  me: async (token: string) => {
    const res = await fetch(`${AUTH_BASE}/api/auth/me`, {
      headers: { accept: "application/json", Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error(`Could not resolve session (${res.status})`);
    return res.json();
  },
  logout: () => {
    if (typeof window !== "undefined") {
      sessionStorage.removeItem("radonaix_token");
      sessionStorage.removeItem("radonaix_user");
    }
  },
  profile: () =>
    safeGet("/auth/me", {
      id: "u-001", name: "Aarav Mehta", email: "aarav.mehta@radonaix.io",
      role: "ra_lead", roleLabel: "RA Manager", department: "Finance Operations",
      lastLogin: "2026-06-02T08:14:00Z", status: "Active", avatar: "AM",
    }),
  // Change the signed-in user's own password (POST /auth/change-password).
  changePassword: (currentPassword: string, newPassword: string) =>
    api.post("/auth/change-password", { currentPassword, newPassword }).then((r) => r.data),

  // --- Forgot / reset password (unauthenticated, like login) ---------------
  // NOTE: these hit endpoints that DO NOT YET EXIST on the backend. They are
  // wired here so the UI is complete; the backend must add them (see the flow
  // spec handed to the team). Contract:
  //   POST /api/auth/forgot-password  { email }            -> { ok: true }
  //     Always 200 (even for unknown emails) to avoid account enumeration;
  //     when the email exists the backend emails a time-limited reset link
  //     pointing at  <app>/reset-password?token=<token>.
  //   POST /api/auth/reset-password   { token, newPassword } -> { ok: true }
  //     Validates the token, sets the new password, and (recommended) leaves
  //     mustResetPassword cleared so the user logs straight in afterwards.
  requestPasswordReset: async (email: string) => {
    const res = await fetch(`${AUTH_BASE}/api/auth/forgot-password`, {
      method: "POST",
      headers: { accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (!res.ok) {
      let message = `Request failed (${res.status})`;
      try { message = (await res.json())?.error?.message || message; } catch { /* non-JSON */ }
      throw new Error(message);
    }
    return res.json().catch(() => ({ ok: true })) as Promise<{ ok: boolean }>;
  },
  resetPassword: async (token: string, newPassword: string) => {
    const res = await fetch(`${AUTH_BASE}/api/auth/reset-password`, {
      method: "POST",
      headers: { accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ token, newPassword }),
    });
    if (!res.ok) {
      let message = `Reset failed (${res.status})`;
      try { message = (await res.json())?.error?.message || message; } catch { /* non-JSON */ }
      throw new Error(message);
    }
    return res.json().catch(() => ({ ok: true })) as Promise<{ ok: boolean }>;
  },
};

// Admin-module reads return an empty list when the backend has no data or is
// unreachable — the screens render a "No data found" state rather than seeded
// demo rows.
export const userService = {
  list: () => safeGet<any[]>("/users", []),
  listFull: () => safeGet<any[]>("/users", []),
  // Writes hit the backend so changes persist in administration.users (the DB).
  create: (payload: {
    fullName: string; email: string; password: string; role: string;
    phone?: string; department?: string; status?: string; mustResetPassword?: boolean;
  }) => api.post("/users", payload).then((r) => r.data),
  update: (id: string, payload: Partial<{
    fullName: string; email: string; password: string; role: string;
    phone: string; department: string; status: string;
  }>) => api.patch(`/users/${id}`, payload).then((r) => r.data),
  remove: (id: string) => api.delete(`/users/${id}`).then((r) => r.data),
};

export const roleService = {
  list: () => safeGet<any[]>("/roles", []),
  // Writes hit the backend so changes persist in administration.roles.
  upsert: (payload: { id?: string; name: string; description?: string; status?: string; permissions?: any }) =>
    api.post("/roles", payload).then((r) => r.data),
  update: (id: string, payload: Partial<{ name: string; description: string; status: string }>) =>
    api.patch(`/roles/${id}`, payload).then((r) => r.data),
  updatePermissions: (id: string, permissions: any) =>
    api.put(`/roles/${id}/permissions`, { permissions }).then((r) => r.data),
};

// One entry of the audit Module filter — mirrors backend rbac.AuditModule.
export interface AuditModuleOption {
  key: string;    // e.g. "userManagement"
  label: string;  // e.g. "User Management"
}

export const auditService = {
  list: (params?: {
    actor?: string;
    action?: string;
    module?: string;      // exact AuditModule key; filtered server-side
    date_from?: string;
    date_to?: string;
    limit?: number;
  }) => safeGet<any[]>("/audit-logs", [], { params }),
  // Canonical module list for the filter dropdown (not derived from the loaded
  // rows, so a module with no recent events is still selectable).
  modules: () => safeGet<AuditModuleOption[]>("/audit-logs/modules", []),
};

// --- Exports (async Download Center jobs) ----------------------------------
export interface ExportJobRow {
  id: string;
  reference: string;             // EXP-XXXXXXXXXX
  reportKey: string;
  status: string;                // Queued | Running | Completed | Failed | Cancelled
  progressPct: number;
  processedRows: number;
  totalRows?: number | null;
  fileSizeBytes?: number | null;
  requestedBy?: string | null;
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  expiresAt?: string | null;
  error?: string | null;
  params?: {
    date_from?: string | null;
    date_to?: string | null;
    filters?: { search?: string | null; categories?: Record<string, string[]>; dateColumn?: string | null };
  };
}

export interface CreateExportPayload {
  reportKey: string;
  dateFrom?: string;             // "YYYY-MM-DD" (omit = whole report)
  dateTo?: string;
  filters: { search?: string; categories: Record<string, string[]>; dateColumn?: string };
}

// Full job record (GET /exports/{id}) — extends the list row with the fields the
// worker records on completion: summary KPIs, integrity checksum and file format.
export interface ExportJobDetail extends ExportJobRow {
  kpis?: Record<string, unknown> | null;
  checksumSha256?: string | null;
  fileFormat?: string;
}

// KPI preview (POST /exports/kpis) — the KPIs a would-be export will summarise
// for the current filters, without enqueuing a job.
export interface ExportKpiPreview {
  reportKey: string;
  dateFrom?: string | null;
  dateTo?: string | null;
  kpis?: Record<string, unknown> | null;
}

export const exportsService = {
  // Enqueue an async export; returns the created job (status "Queued").
  create: (payload: CreateExportPayload) =>
    api.post<ExportJobRow>("/exports", payload).then((r) => r.data),
  // Persistent list (own jobs; admins see all). Throws on error so the caller
  // can tell a 503 (exports_disabled → feature off) apart from transient errors.
  list: () => api.get<ExportJobRow[]>("/exports").then((r) => r.data),
  // Full detail incl. KPIs / checksum / file format (recorded by the worker).
  get: (id: string) => api.get<ExportJobDetail>(`/exports/${id}`).then((r) => r.data),
  // Stop a running/queued export (marks Cancelled; row stays in the list).
  cancel: (id: string) => api.post(`/exports/${id}/cancel`).then((r) => r.data),
  // Permanently delete a finished export (row + file) from the server.
  remove: (id: string) => api.delete(`/exports/${id}`).then((r) => r.data),
  // Download the finished artifact. The server streams the file (or, in prod,
  // hands it to nginx via X-Accel-Redirect). We honour the server's
  // Content-Disposition filename (the artifact is a .zip) and guard against an
  // empty body — a 0-byte response means X-Accel-Redirect is on but no nginx is
  // in front to fill it (local dev); saving that would produce a broken file.
  download: async (id: string, reference: string) => {
    const res = await api.get(`/exports/${id}/download`, { responseType: "blob" });
    const blob = res.data as Blob;
    if (!blob || blob.size === 0) {
      const err = new Error("The server returned an empty file.") as Error & { code?: string };
      err.code = "empty_download";
      throw err;
    }
    const cd = (res.headers?.["content-disposition"] as string | undefined) ?? "";
    const filename = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)?.[1] || `${reference}.zip`;
    downloadBlob(blob, decodeURIComponent(filename));
  },
  // Preview the KPIs for the current filters before enqueuing an export.
  previewKpis: (payload: CreateExportPayload) =>
    api.post<ExportKpiPreview>("/exports/kpis", payload).then((r) => r.data),
};

// One monitored server. `id` matches the Prometheus `server` scrape label and the
// Grafana System dashboard's $server template variable.
export type MonitoredServer = {
  id: string;
  label: string;
  role?: string | null; // app | db | report | archive
  tier?: string | null; // master | slave
  feed?: string | null; // air | sdp | svc | all
  isApi: boolean; // runs the RADONAIX API → the API-health view applies
  up: boolean; // node_exporter currently scraping OK
};

export const monitoringService = {
  // The fleet is derived from Prometheus' live scrape targets, so appending one
  // object to deploy/prometheus/targets/nodes.json is the ONLY step to add a
  // server here — no rebuild, no env var, no drift.
  servers: () => safeGet<MonitoredServer[]>("/monitoring/servers", []),
};

export const systemConfigService = {
  get: () =>
    safeGet("/system/config", {
      environment: "production", retentionDays: 365, slaMinutes: 15,
      alertEmail: "ops-alerts@radonaix.io", maintenanceMode: false,
    }),
  // Persists to administration.system_config via PUT /system/config.
  update: (payload: Partial<{
    environment: string; retentionDays: number; slaMinutes: number;
    alertEmail: string; maintenanceMode: boolean;
  }>) => api.put("/system/config", payload).then((r) => r.data),
};

// --- Reports (RA report catalog) -------------------------------------------
export interface ReportDetail {
  key: string;
  title: string;
  count: number | null; // true total finding count (null when source unavailable)
  columns: string[];
  rows: any[][]; // last reports_detail_limit rows of the filtered set, newest first
  // Optional server note explaining why the report can't be previewed
  // interactively (e.g. too large) — shown as an info banner instead of a
  // blank table. Callers should offer the export as the fallback.
  note?: string | null;
}

// Trigger a browser download for an in-memory blob.
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Serialize a {columns, rows} table to CSV text with RFC-4180 quoting.
function toCsv(columns: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map(esc).join(",")];
  for (const row of rows) lines.push(row.map(esc).join(","));
  return lines.join("\r\n");
}

// Filtered drill-down request (POST /reports/{key}/detail). Same shape as the
// export/KPI request so the on-screen preview and the bulk export apply the
// exact same filters. Empty = the unfiltered last-N default.
export interface ReportDetailRequest {
  dateFrom?: string;             // "YYYY-MM-DD" inclusive (omit = whole report)
  dateTo?: string;
  filters: { search?: string; categories: Record<string, string[]>; dateColumn?: string };
}

const EMPTY_DETAIL_BODY: ReportDetailRequest = { filters: { categories: {} } };

export const reportService = {
  // Drill-down for one report key: the last reports_detail_limit rows of the
  // filtered set (newest first); `count` is the filtered total. Filters are
  // applied server-side. `signal` lets a superseded fetch be aborted so a slow
  // earlier response can't overwrite a newer one. A cancel is re-thrown so the
  // caller can distinguish it from a real failure (which falls back to empty).
  detail: async (
    key: string,
    body: ReportDetailRequest = EMPTY_DETAIL_BODY,
    signal?: AbortSignal,
  ): Promise<ReportDetail> => {
    try {
      const { data } = await api.post<ReportDetail>(`/reports/${key}/detail`, body, { signal });
      return data;
    } catch (e: any) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError") throw e;
      return { key, title: key, count: null, columns: [], rows: [] };
    }
  },
  // Download the full (uncapped) report as CSV — uses the bearer token via `api`.
  // `onProgress` receives 0..100 as bytes arrive (when the server sends a size);
  // `signal` lets the Download Center's Stop action abort the request.
  exportCsv: async (key: string, opts?: { onProgress?: (pct: number) => void; signal?: AbortSignal }) => {
    const { data } = await api.get(`/reports/${key}/export`, {
      responseType: "blob",
      signal: opts?.signal,
      onDownloadProgress: (e) => {
        if (opts?.onProgress && e.total) opts.onProgress((e.loaded / e.total) * 100);
      },
    });
    const blob = data as Blob;
    downloadBlob(blob, `${key}.csv`);
    return { blob, sizeBytes: blob.size };
  },
  // Download the currently-filtered rows as CSV, built client-side so the export
  // matches exactly what the user has filtered down to in the table.
  exportRowsCsv: (key: string, columns: string[], rows: unknown[][]) => {
    const csv = toCsv(columns, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    downloadBlob(blob, `${key}.csv`);
    return { blob, sizeBytes: blob.size };
  },
};

export const pipelineService = {
  stages: () =>
    safeGet("/pipelines/stages", [
      { key: "collection", name: "File Collection", status: "ok", duration: "1m 12s", metric: "1,284 files" },
      { key: "decoding", name: "Decoding", status: "ok", duration: "3m 47s", metric: "8.4M records" },
      { key: "validation", name: "Validation", status: "warning", duration: "2m 03s", metric: "412 warnings" },
      { key: "reconciliation", name: "Reconciliation", status: "ok", duration: "4m 21s", metric: "99.92% match" },
      { key: "reporting", name: "Report Generation", status: "ok", duration: "0m 58s", metric: "37 reports" },
    ]),
  kpis: () =>
    safeGet("/pipelines/kpis", { throughput: "8.4M / hr", avgLatency: "12m 21s", failed24h: 14, slaBreaches: 2 }),
  runs: () =>
    safeGet("/pipelines/runs", [
      { id: "RUN-90112", source: "MSC-EU-1", batch: "BATCH-441A", start: "2026-06-02T09:02:00Z", end: "2026-06-02T09:18:00Z", status: "Completed", records: 1284322, failed: 12 },
      { id: "RUN-90111", source: "MSC-EU-2", batch: "BATCH-441B", start: "2026-06-02T08:45:00Z", end: "2026-06-02T09:01:00Z", status: "Completed", records: 984212, failed: 0 },
      { id: "RUN-90110", source: "BSS-CRM", batch: "BATCH-441C", start: "2026-06-02T08:30:00Z", end: "2026-06-02T08:51:00Z", status: "Failed", records: 312044, failed: 4012 },
      { id: "RUN-90109", source: "MSC-APAC", batch: "BATCH-440Z", start: "2026-06-02T08:11:00Z", end: "2026-06-02T08:29:00Z", status: "Completed", records: 1782914, failed: 8 },
      { id: "RUN-90108", source: "Mediation-2", batch: "BATCH-440Y", start: "2026-06-02T07:55:00Z", end: "2026-06-02T08:09:00Z", status: "Running", records: 421334, failed: 0 },
    ]),
  alerts: () =>
    safeGet("/pipelines/alerts", [
      { id: "ALT-2231", severity: "high", stage: "Validation", message: "Schema drift on field call_duration", createdAt: "2026-06-02T09:42:00Z", status: "Open" },
      { id: "ALT-2230", severity: "medium", stage: "Reconciliation", message: "Latency above SLA (18m vs 15m)", createdAt: "2026-06-02T09:18:00Z", status: "Acknowledged" },
      { id: "ALT-2229", severity: "low", stage: "Decoding", message: "Throughput degraded 4% on DEC-ASN1-v3", createdAt: "2026-06-02T08:51:00Z", status: "Open" },
      { id: "ALT-2228", severity: "critical", stage: "File Collection", message: "Source MSC-EU-3 unreachable", createdAt: "2026-06-02T08:12:00Z", status: "Open" },
    ]),
  retries: () =>
    safeGet("/pipelines/retries", [
      { id: "JOB-7781", batch: "BATCH-441C", stage: "Reconciliation", error: "Mismatch threshold exceeded (3.4%)", retryCount: 1 },
      { id: "JOB-7780", batch: "BATCH-440X", stage: "Decoding", error: "Malformed ASN.1 segment at offset 18421", retryCount: 0 },
      { id: "JOB-7779", batch: "BATCH-440W", stage: "Validation", error: "Null required field msisdn", retryCount: 2 },
    ]),
  retry: (id: string) => safePost(`/pipelines/jobs/${id}/retry`, {}, { ok: true }),
  replay: (id: string) => safePost(`/pipelines/jobs/${id}/replay`, {}, { ok: true }),
  acknowledge: (id: string) => safePost(`/pipelines/alerts/${id}/ack`, {}, { ok: true }),
};
