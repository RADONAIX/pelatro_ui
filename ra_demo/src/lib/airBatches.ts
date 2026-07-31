// AIR processed batch log — sample dataset from public.air_processed_batch_log
export type StageStatus = "SUCCESS" | "PARTIAL" | "FAILED" | "PENDING" | "RUNNING" | "IN_PROGRESS";

export interface BatchLog {
  batch_id: string;
  total_files: number;
  batch_start_time: string;
  batch_end_time: string;
  batch_status: StageStatus;
  batch_timestamp: string;
  watcher_start_time: string;
  watcher_end_time: string;
  watcher_status: StageStatus;
  archive_start_time: string;
  archive_end_time: string;
  archive_status: StageStatus;
  archived_file_count: number;
  decoder_start_time: string;
  decoder_end_time: string;
  decoder_status: StageStatus;
  decode_complete_count: number;
  decode_failed_count: number;
  validation_start_time: string;
  validation_end_time: string;
  validation_status: StageStatus;
  validation_message: string;
  ingestion_start_time: string;
  ingestion_end_time: string;
  ingestion_status: StageStatus;
  load_complete_count: number;
  load_failed_count: number;
  total_aa_rows: number;
  total_rr_rows: number;
  normalization_start_time: string;
  normalization_end_time: string;
  normalization_status: StageStatus;
  zero_kb_file_count: number;
  duplicate_file_count: number;
  corrupt_file_count: number;
  error_message: string;
  created_at: string;
  quarantined_at: string;
  quarantine_reason: string;
  quarantined_file_count: number;
  retried_at: string;
  retried_by: string;
}

export const AIR_PROCESSED_BATCHES: BatchLog[] = [
  {
    batch_id: "AIR_PROCESSED_20260602194500", total_files: 775,
    batch_start_time: "2026-06-02 10:15:01.928086", batch_end_time: "2026-06-02 10:19:04.204464",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 19:45:00",
    watcher_start_time: "2026-06-02 10:15:01.928086", watcher_end_time: "2026-06-02 10:15:04.426053", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:19:04.151007", archive_end_time: "2026-06-02 10:19:04.204464", archive_status: "SUCCESS", archived_file_count: 775,
    decoder_start_time: "2026-06-02 19:45:07.060958", decoder_end_time: "2026-06-02 19:47:18.125263", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:17:20.587762", validation_end_time: "2026-06-02 10:17:20.592922", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 19:47:23.289336", ingestion_end_time: "2026-06-02 10:19:05.188904", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99110, total_rr_rows: 60912,
    normalization_start_time: "2026-06-02 19:49:05.267126", normalization_end_time: "2026-06-02 19:49:05.332382", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:15:01.928086", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602201501", total_files: 775,
    batch_start_time: "2026-06-02 10:45:02.242216", batch_end_time: "2026-06-02 10:49:05.159411",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 20:15:01",
    watcher_start_time: "2026-06-02 10:45:02.242216", watcher_end_time: "2026-06-02 10:45:04.726907", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:49:05.05847", archive_end_time: "2026-06-02 10:49:05.159411", archive_status: "SUCCESS", archived_file_count: 775,
    decoder_start_time: "2026-06-02 20:15:07.915538", decoder_end_time: "2026-06-02 20:17:27.055774", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:47:29.769554", validation_end_time: "2026-06-02 10:47:29.77203", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 20:17:33.542657", ingestion_end_time: "2026-06-02 10:49:06.865187", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99110, total_rr_rows: 60912,
    normalization_start_time: "2026-06-02 20:19:06.945129", normalization_end_time: "2026-06-02 20:19:07.015034", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:45:02.242216", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602203501", total_files: 775,
    batch_start_time: "2026-06-02 11:05:02.248129", batch_end_time: "2026-06-02 11:09:07.050224",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 20:35:01",
    watcher_start_time: "2026-06-02 11:05:02.248129", watcher_end_time: "2026-06-02 11:05:04.842091", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 11:09:07.012485", archive_end_time: "2026-06-02 11:09:07.050224", archive_status: "SUCCESS", archived_file_count: 775,
    decoder_start_time: "2026-06-02 20:35:07.79083", decoder_end_time: "2026-06-02 20:37:11.734639", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 11:07:14.137709", validation_end_time: "2026-06-02 11:07:14.140587", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 20:37:17.480911", ingestion_end_time: "2026-06-02 11:09:08.258972", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99110, total_rr_rows: 60912,
    normalization_start_time: "2026-06-02 20:39:08.340269", normalization_end_time: "2026-06-02 20:39:08.420133", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 11:05:02.248129", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602193500", total_files: 771,
    batch_start_time: "2026-06-02 10:05:01.707591", batch_end_time: "2026-06-02 10:08:54.662192",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 19:35:00",
    watcher_start_time: "2026-06-02 10:05:01.707591", watcher_end_time: "2026-06-02 10:05:04.577963", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:08:54.633355", archive_end_time: "2026-06-02 10:08:54.662192", archive_status: "SUCCESS", archived_file_count: 771,
    decoder_start_time: "2026-06-02 19:35:07.456658", decoder_end_time: "2026-06-02 19:37:23.045231", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:07:25.720072", validation_end_time: "2026-06-02 10:07:25.723679", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 19:37:28.818373", ingestion_end_time: "2026-06-02 10:08:55.940316", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 98948, total_rr_rows: 61027,
    normalization_start_time: "2026-06-02 19:38:55.964025", normalization_end_time: "2026-06-02 19:38:56.020394", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:05:01.707591", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602192044", total_files: 459,
    batch_start_time: "2026-06-02 09:50:45.483161", batch_end_time: "2026-06-02 09:52:58.034277",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 19:20:44",
    watcher_start_time: "2026-06-02 09:50:45.483161", watcher_end_time: "2026-06-02 09:50:45.737585", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 09:52:57.987854", archive_end_time: "2026-06-02 09:52:58.034277", archive_status: "SUCCESS", archived_file_count: 459,
    decoder_start_time: "2026-06-02 19:20:47.965711", decoder_end_time: "2026-06-02 19:22:07.579022", decoder_status: "SUCCESS",
    decode_complete_count: 450, decode_failed_count: 0,
    validation_start_time: "2026-06-02 09:52:10.339642", validation_end_time: "2026-06-02 09:52:10.342167", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 19:22:12.414866", ingestion_end_time: "2026-06-02 09:52:59.042541", ingestion_status: "SUCCESS",
    load_complete_count: 450, load_failed_count: 0, total_aa_rows: 59762, total_rr_rows: 36288,
    normalization_start_time: "2026-06-02 19:22:59.124579", normalization_end_time: "2026-06-02 19:22:59.1813", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 09:50:45.483161", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602203000", total_files: 774,
    batch_start_time: "2026-06-02 11:00:01.871732", batch_end_time: "2026-06-02 11:04:03.94765",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 20:30:00",
    watcher_start_time: "2026-06-02 11:00:01.871732", watcher_end_time: "2026-06-02 11:00:05.238134", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 11:04:03.912149", archive_end_time: "2026-06-02 11:04:03.94765", archive_status: "SUCCESS", archived_file_count: 774,
    decoder_start_time: "2026-06-02 20:30:08.041457", decoder_end_time: "2026-06-02 20:32:15.353386", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 11:02:18.32538", validation_end_time: "2026-06-02 11:02:18.32775", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 20:32:21.862949", ingestion_end_time: "2026-06-02 11:04:04.93811", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99640, total_rr_rows: 60598,
    normalization_start_time: "2026-06-02 20:34:04.96077", normalization_end_time: "2026-06-02 20:34:05.00829", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 11:00:01.871732", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602200001", total_files: 775,
    batch_start_time: "2026-06-02 10:30:02.715272", batch_end_time: "2026-06-02 10:34:09.366855",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 20:00:01",
    watcher_start_time: "2026-06-02 10:30:02.715272", watcher_end_time: "2026-06-02 10:30:05.29266", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:34:09.32573", archive_end_time: "2026-06-02 10:34:09.366855", archive_status: "SUCCESS", archived_file_count: 775,
    decoder_start_time: "2026-06-02 20:00:07.662384", decoder_end_time: "2026-06-02 20:02:13.360757", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:32:15.791561", validation_end_time: "2026-06-02 10:32:15.794393", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 20:02:18.388212", ingestion_end_time: "2026-06-02 10:34:10.462977", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99640, total_rr_rows: 60588,
    normalization_start_time: "2026-06-02 20:04:10.49451", normalization_end_time: "2026-06-02 20:04:10.561802", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:30:02.715272", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602195000", total_files: 774,
    batch_start_time: "2026-06-02 10:20:02.17102", batch_end_time: "2026-06-02 10:24:05.443557",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 19:50:00",
    watcher_start_time: "2026-06-02 10:20:02.17102", watcher_end_time: "2026-06-02 10:20:04.518744", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:24:05.392899", archive_end_time: "2026-06-02 10:24:05.443557", archive_status: "SUCCESS", archived_file_count: 774,
    decoder_start_time: "2026-06-02 19:50:07.190274", decoder_end_time: "2026-06-02 19:52:20.973037", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:22:23.03465", validation_end_time: "2026-06-02 10:22:23.037385", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 19:52:25.550612", ingestion_end_time: "2026-06-02 10:24:07.181949", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99640, total_rr_rows: 60588,
    normalization_start_time: "2026-06-02 19:54:07.206927", normalization_end_time: "2026-06-02 19:54:07.259771", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:20:02.17102", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602201000", total_files: 774,
    batch_start_time: "2026-06-02 10:40:01.872855", batch_end_time: "2026-06-02 10:43:48.845609",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 20:10:00",
    watcher_start_time: "2026-06-02 10:40:01.872855", watcher_end_time: "2026-06-02 10:40:04.313095", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 10:43:48.789116", archive_end_time: "2026-06-02 10:43:48.845609", archive_status: "SUCCESS", archived_file_count: 774,
    decoder_start_time: "2026-06-02 20:10:06.600396", decoder_end_time: "2026-06-02 20:12:14.764233", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 10:42:17.842102", validation_end_time: "2026-06-02 10:42:17.846118", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 20:12:21.51288", ingestion_end_time: "2026-06-02 10:43:50.011575", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 99549, total_rr_rows: 60588,
    normalization_start_time: "2026-06-02 20:13:50.062749", normalization_end_time: "2026-06-02 20:13:50.114681", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 10:40:01.872855", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602192501", total_files: 770,
    batch_start_time: "2026-06-02 09:55:02.241229", batch_end_time: "2026-06-02 09:58:37.467371",
    batch_status: "SUCCESS", batch_timestamp: "2026-06-02 19:25:01",
    watcher_start_time: "2026-06-02 09:55:02.241229", watcher_end_time: "2026-06-02 09:55:04.750276", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 09:58:37.40796", archive_end_time: "2026-06-02 09:58:37.467371", archive_status: "SUCCESS", archived_file_count: 770,
    decoder_start_time: "2026-06-02 19:25:07.714097", decoder_end_time: "2026-06-02 19:27:17.582347", decoder_status: "SUCCESS",
    decode_complete_count: 750, decode_failed_count: 0,
    validation_start_time: "2026-06-02 09:57:21.02826", validation_end_time: "2026-06-02 09:57:21.031237", validation_status: "SUCCESS", validation_message: "",
    ingestion_start_time: "2026-06-02 19:27:23.373177", ingestion_end_time: "2026-06-02 09:58:38.644954", ingestion_status: "SUCCESS",
    load_complete_count: 750, load_failed_count: 0, total_aa_rows: 98745, total_rr_rows: 60912,
    normalization_start_time: "2026-06-02 19:28:38.715785", normalization_end_time: "2026-06-02 19:28:38.789721", normalization_status: "SUCCESS",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 09:55:02.241229", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602210501", total_files: 772,
    batch_start_time: "2026-06-02 11:35:02.118044", batch_end_time: "2026-06-02 11:37:41.882115",
    batch_status: "FAILED", batch_timestamp: "2026-06-02 21:05:01",
    watcher_start_time: "2026-06-02 11:35:02.118044", watcher_end_time: "2026-06-02 11:35:04.812118", watcher_status: "PARTIAL",
    archive_start_time: "2026-06-02 11:36:10.401221", archive_end_time: "2026-06-02 11:36:10.482733", archive_status: "SUCCESS", archived_file_count: 740,
    decoder_start_time: "2026-06-02 21:05:08.224917", decoder_end_time: "2026-06-02 21:06:48.913772", decoder_status: "FAILED",
    decode_complete_count: 612, decode_failed_count: 138,
    validation_start_time: "2026-06-02 11:36:50.221904", validation_end_time: "2026-06-02 11:36:50.224611", validation_status: "FAILED", validation_message: "Schema mismatch: 138 records failed CDR field validation (missing call_duration on rows 412–550)",
    ingestion_start_time: "2026-06-02 21:06:55.118022", ingestion_end_time: "2026-06-02 11:37:40.221008", ingestion_status: "FAILED",
    load_complete_count: 590, load_failed_count: 160, total_aa_rows: 78211, total_rr_rows: 48003,
    normalization_start_time: "2026-06-02 21:07:41.110441", normalization_end_time: "2026-06-02 21:07:41.882115", normalization_status: "PENDING",
    zero_kb_file_count: 12, duplicate_file_count: 8, corrupt_file_count: 22,
    error_message: "Decoder crashed on malformed CDR header; downstream ingestion aborted.",
    created_at: "2026-06-02 11:35:02.118044",
    quarantined_at: "2026-06-02 11:37:42.118044", quarantine_reason: "Decoder failure + validation schema mismatch", quarantined_file_count: 42,
    retried_at: "", retried_by: "",
  },
  {
    batch_id: "AIR_PROCESSED_20260602213001", total_files: 768,
    batch_start_time: "2026-06-02 12:00:01.504233", batch_end_time: "2026-06-02 12:00:01.504233",
    batch_status: "RUNNING", batch_timestamp: "2026-06-02 21:30:01",
    watcher_start_time: "2026-06-02 12:00:01.504233", watcher_end_time: "2026-06-02 12:00:04.118821", watcher_status: "SUCCESS",
    archive_start_time: "2026-06-02 12:00:05.221904", archive_end_time: "2026-06-02 12:00:05.412005", archive_status: "SUCCESS", archived_file_count: 768,
    decoder_start_time: "2026-06-02 21:30:08.117204", decoder_end_time: "", decoder_status: "RUNNING",
    decode_complete_count: 412, decode_failed_count: 0,
    validation_start_time: "", validation_end_time: "", validation_status: "PENDING", validation_message: "",
    ingestion_start_time: "", ingestion_end_time: "", ingestion_status: "PENDING",
    load_complete_count: 0, load_failed_count: 0, total_aa_rows: 0, total_rr_rows: 0,
    normalization_start_time: "", normalization_end_time: "", normalization_status: "PENDING",
    zero_kb_file_count: 0, duplicate_file_count: 0, corrupt_file_count: 0, error_message: "",
    created_at: "2026-06-02 12:00:01.504233", quarantined_at: "", quarantine_reason: "", quarantined_file_count: 0, retried_at: "", retried_by: "",
  },
];
