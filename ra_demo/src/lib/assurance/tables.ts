// ---------------------------------------------------------------------------
// The staging tables a rule can be authored against.
//
// Attributes are picked from the selected table's columns rather than typed
// free-hand, so a rule can never reference a column that doesn't exist — the
// same guarantee the recon workflow builder gives.
//
// DEMO-ONLY: this is a static catalogue. Once the Metadata Catalogue exposes
// real schemas, this module is the single place to swap for that source.
// ---------------------------------------------------------------------------

export interface TableDef {
  id: string;
  label: string;
  columns: string[];
}

export const RULE_TABLES: TableDef[] = [
  // AIR raw vs processed — the canonical reconciliation pairing. Column names
  // mirror the AIR Reconciliation Report (src/lib/reportsCatalog.ts), so a rule
  // authored here lines up with the report that already exists.
  {
    id: "stg_air_raw",
    label: "AIR Raw",
    columns: [
      "txn_id",
      "node_id",
      "subscriber_num",
      "tran_amt",
      "acc_balance",
      "record_type",
      "filename",
      "created_time",
    ],
  },
  {
    id: "stg_air_processed",
    label: "AIR Processed",
    columns: [
      "txn_id",
      "node_id",
      "subscriber_num",
      "tran_amt",
      "acc_balance",
      "record_type",
      "filename",
      "created_time",
    ],
  },
  {
    id: "stg_sdp_raw",
    label: "SDP Raw",
    columns: ["txn_id", "node_id", "subscriber_num", "tran_amt", "acc_balance", "filename", "created_time"],
  },
  {
    id: "stg_sdp_processed",
    label: "SDP Processed",
    columns: ["txn_id", "node_id", "subscriber_num", "tran_amt", "acc_balance", "filename", "created_time"],
  },
  {
    id: "stg_air_cdr",
    label: "AIR CDR",
    columns: ["subscriber_id", "service", "transaction_datetime", "amount_used", "node_id", "txn_id"],
  },
  {
    id: "stg_sdp_cdr",
    label: "SDP CDR",
    columns: ["subscriber_id", "service", "transaction_datetime", "amount_used", "node_id", "txn_id"],
  },
  {
    id: "stg_msc_cdr",
    label: "MSC CDR",
    columns: ["subscriber_id", "service", "transaction_datetime", "call_duration", "amount_used"],
  },
  {
    id: "stg_air_cdr_records",
    label: "AIR CDR RECORDS",
    columns: ["record_id", "file_name", "load_datetime", "record_count"],
  },
  {
    id: "stg_sdp_cdr_files",
    label: "SDP CDR FILES",
    columns: ["file_id", "file_name", "file_seq", "load_datetime"],
  },
];

export const tableLabel = (id: string) => RULE_TABLES.find((t) => t.id === id)?.label ?? id;

export const tableColumns = (id: string) => RULE_TABLES.find((t) => t.id === id)?.columns ?? [];

/** Attributes are stored as bare column names and qualified only for display. */
export const qualify = (tableId: string, column: string) =>
  column ? `${tableLabel(tableId)}.${column}` : "—";
