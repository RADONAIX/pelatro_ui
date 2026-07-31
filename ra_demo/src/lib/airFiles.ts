// Synthetic file logs derived deterministically from each batch.
// Mirrors public.air_processed_file_log structure.
import { AIR_PROCESSED_BATCHES, type BatchLog } from "@/lib/airBatches";

export type FileStatus = "SUCCESS" | "FAILED" | "PENDING" | "RUNNING" | "DUPLICATE" | "COMPLETE";
export type IntegrityFlag = "OK" | "ZERO_KB" | "DUPLICATE" | "CORRUPT" | "QUARANTINED";
export type StageStatus = "SUCCESS" | "FAILED" | "PENDING" | "RUNNING" | "SKIPPED" | "DUPLICATE" | "COMPLETE";
export type FileType = "AA" | "RR" | "CDR";

export interface FileLog {
  id: string;
  filename: string;
  batch_id: string;
  node_id: string;
  sequence_number: number;
  file_timestamp: string;
  file_type: FileType;
  file_status: FileStatus;
  integrity_flag: IntegrityFlag;
  archived_at: string;
  archived_path: string;
  watcher_start_time: string;
  watcher_end_time: string;
  watcher_status: StageStatus;
  picker_start_time: string;
  picker_end_time: string;
  picker_status: StageStatus;
  decoder_start_time: string;
  decoder_end_time: string;
  decoder_status: StageStatus;
  csv_creation_start_time: string;
  csv_creation_end_time: string;
  csv_creation_status: StageStatus;
  db_loading_start_time: string;
  db_loading_end_time: string;
  db_loading_status: StageStatus;
  ingestion_start_time: string;
  ingestion_end_time: string;
  ingestion_status: StageStatus;
  expected_record_count: number;
  actual_record_count: number;
  retry_count: number;
  last_error_step: string;
  error_message: string;
  created_at: string;
  quarantined_at: string;
  quarantine_reason: string;
  quarantine_batch_dir: string;
  quarantine_count: number;
  retried_at: string;
}

function addMs(iso: string, ms: number): string {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return iso;
  return new Date(t + ms).toISOString().replace("T", " ").replace("Z", "");
}

const NODES = ["node-a01", "node-a02", "node-b01", "node-b02"];

export function getFileLogsForBatch(b: BatchLog): FileLog[] {
  const n = b.total_files;
  const failedDecode = b.decode_failed_count;
  const failedLoad = b.load_failed_count;
  const zero = b.zero_kb_file_count;
  const dup = b.duplicate_file_count;
  const corrupt = b.corrupt_file_count;
  const quarantined = b.quarantined_file_count;

  // Allocate categories across the n rows (no overlap; deterministic ordering)
  const rows: FileLog[] = [];
  const stamp = b.batch_timestamp.replace(/[-: ]/g, "");

  for (let i = 0; i < n; i++) {
    const seq = i + 1;
    // categorize this row
    let integrity: IntegrityFlag = "OK";
    let fileStatus: FileStatus = "SUCCESS";
    let lastErr = "";
    let errMsg = "";
    let decoderStatus: StageStatus = "SUCCESS";
    let loadStatus: StageStatus = "SUCCESS";
    let quarantinedAt = "";
    let quarantineReason = "";
    let quarantineCount = 0;
    let actual = 130;

    let k = i;
    if (k < corrupt) {
      integrity = "CORRUPT"; fileStatus = "FAILED"; decoderStatus = "FAILED";
      lastErr = "decoder"; errMsg = "Corrupt header: invalid CDR magic bytes"; actual = 0;
    } else if ((k -= corrupt) < zero) {
      integrity = "ZERO_KB"; fileStatus = "FAILED"; decoderStatus = "SKIPPED";
      lastErr = "watcher"; errMsg = "File size is 0 bytes"; actual = 0;
    } else if ((k -= zero) < dup) {
      integrity = "DUPLICATE"; fileStatus = "FAILED"; decoderStatus = "SKIPPED";
      lastErr = "picker"; errMsg = "Duplicate filename detected in batch window"; actual = 0;
    } else if ((k -= dup) < quarantined) {
      integrity = "QUARANTINED"; fileStatus = "FAILED"; decoderStatus = "FAILED";
      lastErr = "validation"; errMsg = "Quarantined: schema validation failed";
      quarantinedAt = b.quarantined_at || b.batch_end_time;
      quarantineReason = b.quarantine_reason || "Validation failure";
      quarantineCount = 1; actual = 0;
    } else if ((k -= quarantined) < Math.max(0, failedDecode - corrupt - quarantined)) {
      integrity = "OK"; fileStatus = "FAILED"; decoderStatus = "FAILED";
      lastErr = "decoder"; errMsg = "Schema mismatch on CDR fields"; actual = 0;
    } else if ((k -= Math.max(0, failedDecode - corrupt - quarantined)) < Math.max(0, failedLoad - failedDecode)) {
      integrity = "OK"; fileStatus = "FAILED"; loadStatus = "FAILED";
      lastErr = "db_loading"; errMsg = "DB constraint violation"; actual = 0;
    } else if (b.batch_status === "RUNNING" && i >= b.decode_complete_count) {
      integrity = "OK"; fileStatus = "PENDING"; decoderStatus = "PENDING"; loadStatus = "PENDING"; actual = 0;
    }

    const fileType: FileType = i % 3 === 0 ? "AA" : i % 3 === 1 ? "RR" : "CDR";
    const node = NODES[i % NODES.length];
    const baseStart = new Date(b.batch_start_time).getTime();
    const offset = i * 150; // ms

    rows.push({
      id: `${b.batch_id}_${String(seq).padStart(4, "0")}`,
      filename: `${fileType}_${stamp}_${String(seq).padStart(5, "0")}_${node}.dat`,
      batch_id: b.batch_id,
      node_id: node,
      sequence_number: seq,
      file_timestamp: addMs(b.batch_start_time, offset),
      file_type: fileType,
      file_status: fileStatus,
      integrity_flag: integrity,
      archived_at: b.archive_status === "SUCCESS" && fileStatus !== "PENDING" ? addMs(b.archive_start_time, offset / 4) : "",
      archived_path: b.archive_status === "SUCCESS" && fileStatus !== "PENDING"
        ? `/archive/air/processed/${stamp.slice(0, 8)}/${node}/${fileType}_${seq}.dat` : "",
      watcher_start_time: addMs(b.watcher_start_time, offset / 8),
      watcher_end_time: addMs(b.watcher_end_time, offset / 8),
      watcher_status: integrity === "ZERO_KB" ? "FAILED" : "SUCCESS",
      picker_start_time: addMs(b.watcher_end_time, offset / 8),
      picker_end_time: addMs(b.watcher_end_time, offset / 8 + 80),
      picker_status: integrity === "DUPLICATE" ? "FAILED" : "SUCCESS",
      decoder_start_time: addMs(b.decoder_start_time, offset),
      decoder_end_time: decoderStatus === "PENDING" ? "" : addMs(b.decoder_start_time, offset + 220),
      decoder_status: decoderStatus,
      csv_creation_start_time: decoderStatus === "SUCCESS" ? addMs(b.decoder_end_time, offset / 6) : "",
      csv_creation_end_time: decoderStatus === "SUCCESS" ? addMs(b.decoder_end_time, offset / 6 + 90) : "",
      csv_creation_status: decoderStatus === "SUCCESS" ? "SUCCESS" : decoderStatus === "PENDING" ? "PENDING" : "SKIPPED",
      db_loading_start_time: loadStatus === "SUCCESS" || loadStatus === "FAILED" ? addMs(b.ingestion_start_time, offset) : "",
      db_loading_end_time: loadStatus === "SUCCESS" ? addMs(b.ingestion_start_time, offset + 160) : "",
      db_loading_status: decoderStatus === "PENDING" ? "PENDING" : loadStatus,
      ingestion_start_time: addMs(b.ingestion_start_time, offset),
      ingestion_end_time: loadStatus === "SUCCESS" ? addMs(b.ingestion_start_time, offset + 320) : "",
      ingestion_status: decoderStatus === "PENDING" ? "PENDING" : loadStatus,
      expected_record_count: 130,
      actual_record_count: actual,
      retry_count: integrity === "QUARANTINED" ? 1 : 0,
      last_error_step: lastErr,
      error_message: errMsg,
      created_at: addMs(b.created_at, offset),
      quarantined_at: quarantinedAt,
      quarantine_reason: quarantineReason,
      quarantine_batch_dir: quarantinedAt ? `/quarantine/${b.batch_id}/` : "",
      quarantine_count: quarantineCount,
      retried_at: "",
    });
  }
  return rows;
}

export function getBatchById(id: string): BatchLog | undefined {
  return AIR_PROCESSED_BATCHES.find((b) => b.batch_id === id);
}
