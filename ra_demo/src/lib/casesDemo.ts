// DEMO-ONLY. Case management data, persisted to localStorage; no backend calls.
// The /cases router is commented out server-side (assurance/router.py), so the
// API 404s today — and even enabled, the Case model has no origin / findingType /
// action / evidence columns. Where the shapes DO overlap the real model
// (severity, status, owner, linkedTxnId, comments) this uses the backend's
// vocabulary so a future wiring is a small step rather than a rewrite.

const STORAGE_KEY = "radonaix_cases_v5";

// Matches backend CaseStatus / CaseSeverity (assurance/schemas.py).
export const STATUSES = ["Open", "In Progress", "Resolved", "Closed", "Cancelled"] as const;
export const SEVERITIES = ["low", "medium", "high", "critical"] as const;
export type CaseStatus = (typeof STATUSES)[number];
export type CaseSeverity = (typeof SEVERITIES)[number];

// How the case came to exist. Demo-only concept.
export const ORIGINS = ["auto_detected", "analyst_raised"] as const;

// Remediation taken. Demo-only concept.
export const ACTIONS = ["NA", "Escalated to carrier", "Adjusted & rebilled", "Waived", "Config fix raised"] as const;

// The finding that raised the case — keyed to the real report catalog so a case
// always traces back to a report the platform actually produces.
export const FINDING_TYPES = [
  { key: "air_reconciliation", label: "AIR Recon (pre vs post)" },
  { key: "sdp_reconciliation", label: "SDP Recon (pre vs post)" },
  { key: "air_sdp_cross", label: "AIR vs SDP Cross Recon" },
  { key: "file_sequence_check", label: "Missing File Sequence" },
  { key: "record_sequence_check", label: "Record Sequence Gap" },
  { key: "file_exception", label: "File Exception" },
] as const;

export const findingLabel = (key: string) => FINDING_TYPES.find((f) => f.key === key)?.label ?? key;

export const STREAMS = ["AIR", "SDP", "MSC"] as const;

// Mirrors backend ReconRecord (assurance/schemas.py) so the trace table shows
// the same columns an analyst sees in the reconciliation report.
export interface TraceRecord {
  txnId: string;
  nodeId: string;
  subscriberNum: string;
  rawAmount: number | null;
  procAmount: number | null;
  status: "MATCHED" | "AMOUNT_MISMATCH" | "RAW_ONLY" | "PROC_ONLY";
}

export interface CaseComment {
  id: string;
  author: string;
  body: string;
  createdAt: string;
}

export interface AssuranceCase {
  id: string;
  reference: string;
  title: string;
  description: string;
  origin: string;
  findingType: string;
  severity: string;
  status: string;
  action: string;
  owner: string;
  stream: string;
  nodeId: string;
  linkedTxnId: string;
  linkedBatch: string;
  estimatedImpact: number;
  // Total flagged records behind the finding. `trace` below is only a small
  // reviewed sample of this population — the modal shows "sample of N of M".
  affectedCount: number;
  // Real timestamps — the previous version stored a pre-rendered "2h ago"
  // string, which a sortable Date column would order wrongly.
  createdAt: string;
  updatedAt: string;
  evidence: { name: string; kind: string; size: string } | null;
  trace: TraceRecord[];
  comments: CaseComment[];
  savedInsights: { id: string; body: string; at: string }[];
}

// The signed-in demo analyst. Cases owned by this name populate "Self Assigned".
export const CURRENT_ANALYST = "super_admin";

const iso = (daysAgo: number, hour = 9) => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
};

function trace(prefix: string, n: number, status: TraceRecord["status"]): TraceRecord[] {
  return Array.from({ length: n }, (_, i) => {
    const raw = 12.5 + i * 3.25;
    return {
      txnId: `${prefix}${String(100234 + i * 17)}`,
      nodeId: `NODE-${(i % 4) + 1}`,
      subscriberNum: `2547${String(10045678 + i * 311)}`,
      rawAmount: status === "PROC_ONLY" ? null : Number(raw.toFixed(2)),
      procAmount: status === "RAW_ONLY" ? null : Number((status === "AMOUNT_MISMATCH" ? raw - 1.75 : raw).toFixed(2)),
      status,
    };
  });
}

// Kept deliberately small: 5 cases — 1 unassigned and 4 owned by
// CURRENT_ANALYST (so the Self Assigned tab shows 4). Statuses span In Progress
// and Closed so every count tile above the list has a non-zero value.
// An empty `owner` is the marker for unassigned.
export const SEED: AssuranceCase[] = [
  {
    id: "c-2031", reference: "CASE-2031",
    title: "AIR raw vs processed amount variance — batch AIR_0620_03",
    description: "9,100 AIR records were under-rated — the processed amount fell below the raw charged amount after mediation. Concentrated on NODE-2 between 02:00 and 04:00, avg under-charge $3.80/record.",
    origin: "auto_detected", findingType: "air_reconciliation", severity: "critical", status: "Open", action: "NA",
    owner: "", stream: "AIR", nodeId: "NODE-2", linkedTxnId: "AIR100234", linkedBatch: "AIR_20250620_03",
    estimatedImpact: 34600, affectedCount: 9100, createdAt: iso(0, 2), updatedAt: iso(0, 6),
    evidence: { name: "air_variance_0620.csv", kind: "CSV extract", size: "84 KB" },
    trace: trace("AIR", 6, "AMOUNT_MISMATCH"), comments: [], savedInsights: [],
  },
  {
    id: "c-2030", reference: "CASE-2030",
    title: "Missing file sequence — SDP 20250619 (seq 41–43)",
    description: "Three consecutive SDP CDR files absent from the ingest window (≈1,220 records). Upstream mediation reported successful export for all three.",
    origin: "auto_detected", findingType: "file_sequence_check", severity: "high", status: "In Progress", action: "Escalated to carrier",
    owner: "super_admin", stream: "SDP", nodeId: "NODE-1", linkedTxnId: "SDP100251", linkedBatch: "SDP_20250619_41",
    estimatedImpact: 22300, affectedCount: 1220, createdAt: iso(1, 3), updatedAt: iso(0, 11),
    evidence: { name: "sdp_seq_gap.png", kind: "Screenshot", size: "212 KB" },
    trace: trace("SDP", 4, "RAW_ONLY"),
    comments: [{ id: "cm1", author: "Priya Shah", body: "Raised with the mediation team — they are re-exporting 41–43.", createdAt: iso(0, 10) }],
    savedInsights: [],
  },
  {
    id: "c-2029", reference: "CASE-2029",
    title: "AIR vs SDP cross-recon mismatch — 2,180 unmatched",
    description: "Cross correlation between AIR and SDP shows 2,180 transactions present in AIR with no SDP counterpart for the 18 Jun window — potentially unbilled roaming usage.",
    origin: "auto_detected", findingType: "air_sdp_cross", severity: "critical", status: "Open", action: "NA",
    owner: "super_admin", stream: "AIR", nodeId: "NODE-3", linkedTxnId: "AIR100268", linkedBatch: "AIR_20250618_11",
    estimatedImpact: 39800, affectedCount: 2180, createdAt: iso(2, 4), updatedAt: iso(1, 9),
    evidence: null, trace: trace("AIR", 8, "RAW_ONLY"), comments: [], savedInsights: [],
  },
  {
    id: "c-2028", reference: "CASE-2028",
    title: "Record sequence gap — AIR NODE-4 (515 records)",
    description: "Record sequence check found a contiguous 515-record gap on NODE-4. Suspected collector restart during the 17 Jun maintenance window; records recovered on re-pull.",
    origin: "auto_detected", findingType: "record_sequence_check", severity: "medium", status: "Closed", action: "Config fix raised",
    owner: "super_admin", stream: "AIR", nodeId: "NODE-4", linkedTxnId: "AIR100302", linkedBatch: "AIR_20250617_22",
    estimatedImpact: 9400, affectedCount: 515, createdAt: iso(3, 8), updatedAt: iso(1, 14),
    evidence: { name: "node4_seq_report.csv", kind: "CSV extract", size: "41 KB" },
    trace: trace("AIR", 3, "RAW_ONLY"),
    comments: [{ id: "cm2", author: "super_admin", body: "Collector restart confirmed in the ops log. Config change raised as CHG-4471.", createdAt: iso(1, 13) }],
    savedInsights: [],
  },
  {
    id: "c-2027", reference: "CASE-2027",
    title: "File exception — malformed header on SDP_20250617_08",
    description: "Decoder rejected the file on a malformed header record. File quarantined; 0 records ingested, 860 pending re-delivery.",
    origin: "auto_detected", findingType: "file_exception", severity: "high", status: "In Progress", action: "NA",
    owner: "super_admin", stream: "SDP", nodeId: "NODE-1", linkedTxnId: "SDP100318", linkedBatch: "SDP_20250617_08",
    estimatedImpact: 15700, affectedCount: 860, createdAt: iso(3, 5), updatedAt: iso(0, 8),
    evidence: { name: "decoder_reject.log", kind: "Log excerpt", size: "6 KB" },
    trace: [], comments: [], savedInsights: [],
  },
];

export function loadCases(): AssuranceCase[] {
  if (typeof window === "undefined") return SEED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as AssuranceCase[]) : null;
    if (Array.isArray(parsed) && parsed.length) return parsed;
  } catch {
    /* ignore malformed storage */
  }
  return SEED;
}

// Called from explicit user actions only — never a reactive effect, which would
// race the mount load and clobber saved data under StrictMode.
export function saveCases(next: AssuranceCase[]) {
  if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `c-${Math.floor(Math.random() * 1e9)}`;
}

export const fmtMoney = (n: number) => `$${new Intl.NumberFormat("en-US").format(Math.round(n))}`;

export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

// "2h ago" — derived at render from a real timestamp, never stored.
export function relative(isoStr: string): string {
  const ms = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
