# Download Center — UI ⇄ Backend Integration Contract

Authoritative spec for wiring the **Download Center** (and the Pipeline & Job
Monitor "Export CSV") to the current backend. If the UI predates this, the
**"What the UI must implement"** checklist (§8) is the delta to apply.

Source of truth: `app/modules/exports/{router,schemas,service}.py`,
`app/modules/reporting/{service,catalog}.py`.

---

## 1. Basics
- **Base URL:** all paths under `/api` (e.g. `POST /api/exports`).
- **Auth:** JWT **Bearer** in the `Authorization` header on every call (there is
  no query-param/cookie auth). Permission: **`exports`** — `view` to read/list/
  download, `edit` to create/cancel/delete.
- **Module gate:** if bulk exports are disabled server-side, **every** `/exports`
  route returns **503** `{"error":{"code":"exports_disabled",...}}`. The UI should
  treat 503 on these routes as "feature off" (hide/disable the Download Center).
- **Error envelope (all errors):** `{ "error": { "code": string, "message":
  string, "details"?: object } }`. Show `error.message` to the user.

---

## 2. Endpoints
| Method & path | Perm | Purpose | Success |
|---|---|---|---|
| `POST /exports` | edit | Create an export job | **201** `ExportJobRow` |
| `GET /exports` | view | List jobs (own; admins see all). `?limit=&offset=` | `ExportJobRow[]` |
| `GET /exports/{id}` | view | Full job detail (KPIs + checksum) | `ExportJobDetail` |
| `GET /exports/{id}/download` | view | Download the finished `.csv.gz` | **200** file stream |
| `POST /exports/{id}/cancel` | edit | **Stop** a running/queued job (kept in list) | `ExportJobRow` |
| `DELETE /exports/{id}` | edit | **Remove** a finished job (row + file) | **204** |
| `POST /exports/kpis` | view | KPI preview for a filter selection (no rows) | `KpiPreviewResponse` |

> **Stop ≠ Remove.** "Stop" is **`POST /{id}/cancel`**; "Remove/Delete" is
> **`DELETE /{id}`**. An older UI that used `DELETE` for the stop button is wrong.

---

## 3. Request body — `POST /exports` (`ExportJobCreate`)
```jsonc
{
  "reportKey": "file_summary",          // REQUIRED — see §6 for valid keys
  "dateFrom":  "2026-05-01",            // optional, DATE-ONLY "YYYY-MM-DD"
  "dateTo":    "2026-05-31",            // optional, inclusive
  "filters": {                          // optional
    "search":     "AIR_PROCESSED",      // optional substring; matched across columns
    "categories": {                     // column-name → allowed values (OR within, AND across)
      "source": ["AIR"],
      "stream": ["Processed"]
    },
    "dateColumn": "batch_date"          // optional; defaults to the report's own date column
  }
}
```
Rules the backend enforces (surface the 422 message on failure):
- `categories` **keys** and `dateColumn` must be **real columns of that report**
  (see §6) — unknown columns → **422** `validation_failed`.
- If both dates are given, `dateFrom ≤ dateTo` and span ≤ `EXPORT_MAX_DATE_SPAN_DAYS`.
- Same-payload contract is used by **KPI preview** (`POST /exports/kpis`).

---

## 4. Responses
### `ExportJobRow` (list + create + cancel)
```jsonc
{
  "id": "uuid", "reference": "EXP-XXXXXXXXXX", "reportKey": "file_summary",
  "status": "Queued",                 // see §5
  "progressPct": 0,                   // 0..100
  "processedRows": 0,
  "totalRows": 12345,                 // MAY BE null → total unknown (see §8.3)
  "fileSizeBytes": null,              // set when Completed
  "requestedBy": "Jane Doe",
  "createdAt": "…", "startedAt": null, "completedAt": null, "expiresAt": null,
  "error": null,                      // failure message when status=Failed
  "params": { "date_from": "2026-05-01", "date_to": "2026-05-31",
              "filters": { "search": null, "categories": {…}, "dateColumn": null } }
}
```
### `ExportJobDetail` (`GET /exports/{id}`) = `ExportJobRow` **plus**
```jsonc
{ "kpis": { … } | null, "checksumSha256": "…" | null, "fileFormat": "csv.gz" }
```
> `params` is **snake_case** (`date_from`/`date_to`/`filters`) — it echoes what the
> job covers, for display in the Download Center row/detail.

---

## 5. Status lifecycle
`Queued → Running → Completed | Failed | Cancelled`

| Server `status` | Suggested UI label | Notes |
|---|---|---|
| `Queued` | queued | Accepted, waiting for a worker. Progress indeterminate. |
| `Running` | running | Streaming; `progressPct` climbs (or rows-so-far — §8.3). |
| `Completed` | completed | Download enabled; `fileSizeBytes`/`checksumSha256` set. |
| `Failed` | failed | Show `error`. |
| `Cancelled` | stopped | User stopped it; file removed. |

Multi-part exports are **transparent** to the UI — a large export may fan out into
parts internally, but the UI only ever sees the single parent job + its progress.

---

## 6. Report keys + filterable columns (the whitelist)
`categories` keys / `dateColumn` must come from this list per report:

| `reportKey` | date column | filterable columns |
|---|---|---|
| `record_sequence_check` | `date` | source, stream, filename, node_id, date, missing_sequence_from, missing_sequence_to, missing_count |
| `file_sequence_check` | `date` | source, stream, date, file_node_id, file_sequence, expected_file, status |
| `file_exception` | `file_date` | source, stream, batch_date, file_date, file_status, filename |
| `file_summary` | `batch_date` | source, stream, batch_date, total_files_loaded, duplicate_file_count, zero_kb_file_count, corrupt_file_count |
| `report_batch_log` | `start_time` | source, report_batch_id, process_name, start_time, end_time, status, error_message |
| `air_reconciliation` / `sdp_reconciliation` | `created_time` | reconciliation_status, record_type, txn_id, node_id, subscriber_num, raw_tran_amt, proc_tran_amt, raw_acc_balance, proc_acc_balance, filename, created_time |
| `pipeline_batches` | `batch_start_time` | batch_id, batch_start_time, batch_end_time, dag, stream, batch_status, total_files, decode_complete_count, decode_failed_count, load_complete_count, zero_kb_file_count, duplicate_file_count, corrupt_file_count |

> Report keys are **stream-driven** — if a new data source is added (e.g. `ccn`),
> a `ccn_reconciliation` key appears automatically. Don't hard-code the list;
> ideally drive it from the report catalog the Reports screen already uses.

---

## 7. Pipeline & Job Monitor export → `pipeline_batches`
The "Export CSV" on the Pipeline Map posts a **normal export job** with
`reportKey: "pipeline_batches"`. `dag` / `stream` / `batch_status` are **real,
filterable columns** server-side (no need to derive them from `batch_id`):
```jsonc
{
  "reportKey": "pipeline_batches",
  "dateFrom": "2026-05-01", "dateTo": "2026-05-06",
  "filters": {
    "search": "AIR_PROCESSED",                       // substring on batch_id
    "categories": {
      "dag":          ["AIR", "SDP"],                // AIR | SDP | MSC | …
      "stream":       ["Raw", "Processed"],          // Raw | Processed | Reconciled
      "batch_status": ["SUCCESS", "PARTIAL", "FAILED"]
    },
    "dateColumn": "batch_start_time"
  }
}
```
Same `ExportJobRow` response + Download Center flow as every other report.

---

## 8. What the UI must implement (checklist / likely deltas from an old build)
1. **Stop vs Remove.** Stop button → `POST /exports/{id}/cancel`. Delete button →
   `DELETE /exports/{id}`. Do **not** use `DELETE` for stop.
2. **Reject handling (422).** `POST /exports` can be rejected by the planner with
   **422** (`validation_failed`) — e.g. *"Export would need N parts (> hard cap)…"*,
   *"…no date column to partition on…"*, *"Not enough free disk…"*, *"Unknown
   filter column(s)…"*. Show `error.message` in the failure toast (don't swallow it).
   Also handle **429** (`too_many_jobs`) and **503** (`exports_disabled`).
3. **Indeterminate progress.** `totalRows` **may be `null`** (a Postgres count that
   was too slow is skipped so streaming isn't blocked). When `totalRows == null`
   and `status == "Running"`, render an **indeterminate** bar and show
   `processedRows` ("N rows so far") instead of a %.
4. **Polling.** While any listed job is `Queued`/`Running`, poll `GET /exports`
   every ~3–5 s; stop polling when none are active.
5. **Filters → contract.** Send `categories` keyed by **real column names** (from
   §6), `dateColumn` as a column name, and `dateFrom`/`dateTo` as **date-only**
   `YYYY-MM-DD`. (Time-of-day is not honoured — dates are inclusive day bounds.)
6. **Download.** `GET /exports/{id}/download` returns the `.csv.gz`
   (`Content-Disposition: attachment; filename="<reference>.csv.gz"`). Enable it
   only when `status == "Completed"`. A 409 means not-ready, a 410 means
   expired/gone — refresh the row.
   - ⚠️ **Large-file caveat:** fetching via `responseType:"blob"` buffers the whole
     file in memory — fine up to ~1–2 GB, but it will OOM the tab on the multi-GB
     artifacts big exports produce. There is **no clean UI-only fix** (the endpoint
     is Bearer-header-only, so a native browser download can't authenticate). For
     huge artifacts, retrieve server-side (`REPORTS_DIR/<reference>.csv.gz`) until a
     signed-URL download is added backend-side. Keep the blob path for normal sizes.
7. **KPI preview (optional).** `POST /exports/kpis` with the same body (minus a job)
   returns `{ reportKey, dateFrom, dateTo, kpis }` — use it to preview what an
   export will summarise before creating it.
8. **Detail view.** `GET /exports/{id}` adds `kpis`, `checksumSha256`, `fileFormat`
   for a detail modal.

---

## 9. Quick reference — statuses & error codes
- Statuses: `Queued`, `Running`, `Completed`, `Failed`, `Cancelled`.
- Error `code`s: `validation_failed` (422), `too_many_jobs` (429),
  `exports_disabled` (503), `export_not_ready` (409), `export_expired` (410),
  `not_found` (404), plus the standard `401`/`403`.
