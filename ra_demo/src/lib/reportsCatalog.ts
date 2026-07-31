// Report catalog — mirrors the backend REPORTS registry. (The backend has no
// /catalog list endpoint, so the catalog is declared here.) available:false = stub.
// Shared by the Reports page (drill-down table) and the Sidebar (nav dropdown).

export interface ReportEntry {
  key: string;
  title: string;
  group: string;
  available: boolean;
  // Short 2-3 line explanation shown via the (i) info hint on the Reports page.
  description?: string;
  // Expected column headers, used to render the table head even when the
  // backend returns zero rows (so the table is never blank). Mirrors the
  // backend report SQL / source-table schema.
  columns?: string[];
}

export const REPORTS: ReportEntry[] = [
  { key: "record_sequence_check", title: "Record Sequence Check", group: "Files", available: true,
    description: "Sequence-gap check across AIR and SDP, raw and processed record streams. Each row flags a missing sequence range between two adjacent files.",
    columns: ["source", "stream", "record_date", "node_id", "previous_filename", "previous_sequence_number", "previous_origin_timestamp", "next_filename", "next_sequence_number", "next_origin_timestamp", "missing_sequence_count"] },
  { key: "file_sequence_check", title: "File Sequence Check", group: "Files", available: true,
    description: "Verifies expected files arrive in sequence across AIR and SDP sources. Flags missing or out-of-order files, with the observed vs expected file and a probable-node-restart indicator.",
    columns: ["source", "stream", "file_date", "file_node_id", "file_type", "event_type", "sequence_cycle", "file_sequence", "expected_file", "actual_file", "status", "current_status", "probable_node_restart_flag"] },
  { key: "file_exception", title: "File Exception Report", group: "Files", available: true,
    description: "Lists files that failed processing or arrived in an abnormal state across AIR and SDP.",
    columns: ["source", "stream", "batch_date", "file_date", "file_status", "filename"] },
  { key: "file_summary", title: "File Summary Report", group: "Files", available: true,
    description: "Per-batch summary of AIR and SDP file processing — files loaded, plus duplicate, zero-KB and corrupt file counts.",
    columns: ["source", "stream", "file_date", "total_files_loaded", "duplicate_file_count", "zero_kb_file_count", "corrupt_file_count"] },
  { key: "air_reconciliation", title: "AIR Reconciliation Report", group: "Reconciliation", available: true,
    description: "Reconciles raw versus processed AIR transactions and account balances per subscriber. Highlights amount or balance mismatches that may indicate revenue leakage.",
    columns: ["reconciliation_status", "record_type", "txn_id", "node_id", "subscriber_num", "raw_tran_amt", "proc_tran_amt", "raw_acc_balance", "proc_acc_balance", "filename", "created_time"] },
  { key: "sdp_reconciliation", title: "SDP Reconciliation Report", group: "Reconciliation", available: true,
    description: "Reconciles raw versus processed SDP transactions and account balances per subscriber. Highlights amount or balance mismatches that may indicate revenue leakage.",
    columns: ["reconciliation_status", "record_type", "txn_id", "node_id", "subscriber_num", "raw_tran_amt", "proc_tran_amt", "raw_acc_balance", "proc_acc_balance", "filename", "created_time"] },
  { key: "msc_reconciliation", title: "MSC Reconciliation Report", group: "Reconciliation", available: false,
    description: "Reconciles MSC call detail records between the source and processed data to detect mismatches. (Coming soon.)" },
  // { key: "air_sdp_cross_correlation", title: "AIR vs SDP Cross Correlation", group: "Correlation", available: false },
  { key: "report_batch_log", title: "Report Batch Log", group: "Operations", available: true,
    description: "Execution log of AIR and SDP report-generation batches in one table — each run's process, start and end time, status and any error. Filter by Source (AIR/SDP).",
    columns: ["source", "report_batch_id", "process_name", "start_time", "end_time", "status", "error_message"] },
];

export const GROUPS = ["Files", "Reconciliation", "Correlation", "Operations"];

export const AVAILABLE_REPORTS = REPORTS.filter((r) => r.available);

export const DEFAULT_REPORT_KEY = AVAILABLE_REPORTS[0]?.key ?? "";

// Resolve a (possibly user-supplied) report key to a valid, available one.
export function resolveReportKey(key: string | undefined): string {
  if (key && REPORTS.some((r) => r.key === key && r.available)) return key;
  return DEFAULT_REPORT_KEY;
}
