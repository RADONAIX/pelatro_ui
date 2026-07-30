# Adding a data source (stream)

RADONAIX is stream-driven: **AIR**, **SDP**, and (ready) **MSC**, with new ones
like **CCN** addable by **config, not code**. A "stream" flows through the
Pipeline Map (batch/file logs) and the reports (record-sequence, file-sequence,
file-exception, file-summary, report-batch-log, reconciliation).

## How it works
The engine is `app/core/data_sources.py`. Each stream's schema and table names
are **derived from its key by convention**; the operations module
(`BATCHLOG_SOURCES` / `FILE_SOURCES` / recon) and the reporting catalog
(`app/modules/reporting/catalog.py`) are **generated** from the enabled streams,
so a new stream flows into the Pipeline Map and every report with no code change.

**Convention** (for a stream key `k`):

| Thing | Name |
|---|---|
| Pipeline schema (logs + report_batch_log) | `{k}_schema` |
| BI reports schema | `bi_reports` |
| Pipeline batch logs | `{k}_raw_batch_log`, `{k}_processed_batch_log` |
| Pipeline file logs | `{k}_raw_file_log`, `{k}_processed_file_log` |
| Record-sequence (ClickHouse) | `{k}_raw_record_sequence_check`, `{k}_processed_record_sequence_check` |
| File reports (bi_reports) | `{k}_file_seq_check`, `{k}_file_exception_report`, `{k}_file_summary` |
| Report batch log | `{k}_schema.report_batch_log` |
| Reconciliation (ClickHouse) | `{k}_reconciliation` |

## Case 1 — conventional source (the common case): edit ONLY `.env`
The new source is on the **same** Postgres/ClickHouse and follows the naming
convention above. Just add its key:

```bash
# backend .env
DATA_STREAMS=air,sdp,ccn
```
Restart `radonaix-api` (and the worker, if exports run). That's it — CCN appears
in the Pipeline Map (Raw/Processed/Reconciled) and in every report's UNION, and a
`ccn_reconciliation` report is created. **No code, no schema/table definitions.**

Enabling **MSC** is the same: `DATA_STREAMS=air,sdp,msc`.

## Case 2 — source that breaks the convention: `.env` + a small YAML
The source has an odd schema name or non-standard table names. Point the env at a
YAML and override only what deviates (see `deploy/data_sources.example.yaml`):

```bash
# .env
DATA_STREAMS=air,sdp,ccn
DATA_SOURCES_FILE=/opt/radonaix/backend/data_sources.yaml
```
```yaml
# data_sources.yaml
streams:
  ccn:
    schema: ccn_pipeline               # non-standard pipeline schema
    recon_record_type: record_type     # unified record_type (like AIR)
    raw_file_variant: refill           # "refill" | "omit"
    tables:
      file_seq_check: ccn_fseq_check    # only the tables that deviate
      reconciliation: ccn_recon
```
You still don't touch `data_sources.py` — it's the engine.

### Per-stream quirks that exist today
Shipped in `_BUILTIN` in `data_sources.py` (only edit for an *existing* stream
whose tables change shape):
- **AIR** reconciliation uses a unified `record_type` (others: split
  `coalesce(raw_record_type, proc_record_type)`).
- **SDP** raw file log omits the csv/db-loading columns (`raw_file_variant: omit`).

## Case 3 — source on a DIFFERENT Postgres DB
Not yet supported (**Phase 2**). A SQL `UNION` can't span two Postgres databases,
so a cross-DB stream needs per-connection queries merged in the app. Today all
streams share the `ra_pg` / `bi_pg` / `clickhouse` connections.

## After adding a source — checklist
- [ ] The stream's tables exist on the DB(s) with the conventional (or overridden) names.
- [ ] `DATA_STREAMS` updated (+ `DATA_SOURCES_FILE` if it deviates).
- [ ] `systemctl restart radonaix-api` (+ `radonaix-worker` if exports are on).
- [ ] Pipeline Map shows the new DAG (Raw/Processed/Reconciled); reports include it.
- [ ] Startup log line `data_streams_loaded streams=[...]` lists the new key.
