# RADONAIX — Configuration & Operations Reference

The single map of **every operational knob**: what it does, which file owns it, its
current/default value, how to change it, and what to restart afterwards.

## The layered model (why things live where they do)

RADONAIX follows the standard production-grade split — **not everything goes in `.env`**.
Each knob lives in the layer that owns its concern:

| Layer | Owns | Examples | Changed by |
|---|---|---|---|
| **Backend `.env`** | Per-deployment tunables + secrets | DB creds, JWT secret, pool sizes, report limits, SSO creds | Ops, per environment |
| **systemd unit / drop-in** | Process & infra | gunicorn worker count, timeouts, exporters | Ops, per server |
| **Monitoring config** (Grafana/Prometheus provisioning) | Alert thresholds, scrape/retention, SMTP | CPU/mem/disk 85%, scrape 15s, retention 30d | Ops, config-as-code (git) |
| **Code** | Business logic | report SQL, RBAC matrix, report registry | Developers (edit + redeploy) |
| **UI build env** (`VITE_*`) | Frontend build-time | API base URLs | Frontend build |

Rule of thumb: if it changes **per deployment** or is a **secret** → `.env`. If it's about
**how the process runs** → systemd. If it's an **alert/scrape policy** → monitoring config.
If it's **logic** → code.

---

## 1. Backend `.env`

Lives at the backend root as `.env` (template: [deploy/.env.prod.example](../deploy/.env.prod.example)).
Loaded by [app/core/config.py](../app/core/config.py) via pydantic-settings. **Never commit the real `.env`.**
Restart action for all of these: **`systemctl restart radonaix-api`**.

### Reporting row caps
| Knob | Env var | Default | Notes |
|---|---|---|---|
| On-screen drill-down rows | `REPORTS_DETAIL_LIMIT` | `10000` | Rows the report table returns; full set via CSV export |
| CSV export ceiling | `REPORTS_EXPORT_LIMIT` | `1000000` | Hard OOM guard on a single export |

Used in [app/modules/reporting/service.py](../app/modules/reporting/service.py) (`settings.reports_detail_limit` / `settings.reports_export_limit`).

### Read-only ra_pg / bi_pg connection pool (shared)
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Pool size | `RA_PG_POOL_SIZE` | `3` | Per worker, **per engine** (ra_pg + bi_pg) |
| Max overflow | `RA_PG_MAX_OVERFLOW` | `2` | Burst connections above pool size |
| Pool recycle (s) | `RA_PG_POOL_RECYCLE` | `1800` | Recycle a connection after this many seconds |

Used in [app/integrations/ra_postgres.py](../app/integrations/ra_postgres.py) and [app/integrations/bi_postgres.py](../app/integrations/bi_postgres.py) (same settings — they hit the same instance).

### Pipeline Map (operations) caps + window
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Batch query row cap | `PIPELINE_BATCH_ROW_CAP` | `5000` | Safety cap; the time window is the real filter |
| File drill-down row cap | `PIPELINE_FILE_ROW_CAP` | `100000` | Per-batch file rows |
| Default look-back (hours) | `PIPELINE_DEFAULT_HOURS` | `12` | `/pipelines/batches` default; request clamped to 1..168 |

Used in [app/modules/operations/service.py](../app/modules/operations/service.py) and [router.py](../app/modules/operations/router.py).

### App database + pool
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Host / port / name / user / password | `APP_DB_HOST` … `APP_DB_PASSWORD` | localhost/5432/radonaix_app/radonaix | The app's OWN data |
| Schema | `APP_DB_SCHEMA` | `administration` | search_path |
| Pool size / overflow | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `5` | **Per worker.** See connection-budget note below |

> **Connection budget.** Total Postgres connections ≈
> `WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` **+**
> `WEB_CONCURRENCY × 2 × (RA_PG_POOL_SIZE + RA_PG_MAX_OVERFLOW)`.
> With the defaults and 2 workers: `2×10 + 2×2×5 = 40`. Keep this well under the
> server's `max_connections` (currently 300). This is what caused the past
> "too many clients" incident — do not bump pools without doing this math.

### Auth / JWT
| Knob | Env var | Default | Notes |
|---|---|---|---|
| JWT secret | `JWT_SECRET` | — | **Required** in prod, ≥32 chars; app refuses to start otherwise |
| Access token TTL (min) | `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | |
| Refresh token TTL (min) | `REFRESH_TOKEN_EXPIRE_MINUTES` | `20160` (14d) | |
| Lockout threshold / window | `MAX_FAILED_LOGINS` / `LOCKOUT_MINUTES` | `5` / `15` | |
| Login rate limit | `LOGIN_RATE_LIMIT_MAX` / `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `10` / `60` | Per-IP |
| Bootstrap admin | `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` | admin@radonaix.io / — | Password **required** in prod |

### SSO / OAuth
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Enable SSO | `SSO_ENABLED` | `false` | |
| Auto-provision first login | `SSO_AUTO_PROVISION` | `true` | Creates a `viewer` user on first SSO login |
| Default role | `SSO_DEFAULT_ROLE` | `viewer` | |
| Google | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | |
| Microsoft Entra | `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_TENANT_ID` / `MS_REDIRECT_URI` | — / — / `common` / … | Domain restriction handled in Azure (single-tenant) |
| SPA success redirect | `FRONTEND_SUCCESS_REDIRECT` | …/login | Where the backend sends the browser after sign-in |

### Superset (embedded analytics)
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Enable | `SUPERSET_ENABLED` | `false` | |
| Base URL / admin creds / guest user / dashboard id | `SUPERSET_BASE_URL` / `SUPERSET_ADMIN_USERNAME` / `SUPERSET_ADMIN_PASSWORD` / `SUPERSET_GUEST_USERNAME` / `SUPERSET_DASHBOARD_ID` | see config.py | Guest-token flow |

### Read-only ra-platform sources
| Knob | Env var | Default | Notes |
|---|---|---|---|
| ClickHouse | `CLICKHOUSE_ENABLED` / `CLICKHOUSE_HOST` … `CLICKHOUSE_DATABASE` | true / 10.200.37.142 … rafms | Recon data |
| ClickHouse query safety | `CLICKHOUSE_MAX_BYTES_BEFORE_EXTERNAL_SORT` / `CLICKHOUSE_MAX_MEMORY_USAGE` / `CLICKHOUSE_MAX_EXECUTION_TIME` | 8 GiB / 0 / 300s | Per-query caps applied to every CH read — big sorts spill to disk / error cleanly instead of OOMing the shared server (0 = server default) |
| ra_pg | `RA_PG_ENABLED` / `RA_PG_HOST` … `RA_PG_PASSWORD` | true / 10.200.37.142 … | Pipeline batch/file logs |
| BI pg | `RA_BI_PG_NAME` / `RA_BI_PG_SCHEMA` | rafms / bi_reports | Report matviews; same creds as ra_pg |
| Airflow | `AIRFLOW_ENABLED` | `false` | Pipeline retry/replay; off = no-op (URL/creds fall back to code defaults) |
| Data streams | `DATA_STREAMS` / `DATA_SOURCES_FILE` | `air,sdp` / — | Enabled source prefixes (plug-and-play); every schema/table name derives from the key. Optional YAML for non-conventional sources — see `docs/DATA_SOURCES.md` |

### Bulk exports (Download Center)
| Knob | Env var | Default | Notes |
|---|---|---|---|
| Master switch | `EXPORTS_ENABLED` | `true` (`false` in prod template) | Off = `/exports` → 503, no Redis needed |
| Redis / Celery | `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | redis://localhost… | Only when exports enabled. The worker runs the dedicated **`exports`** queue (`-Q exports`) |
| Tuning | `EXPORT_RETENTION_HOURS` / `EXPORT_MAX_CONCURRENT_PER_USER` / `EXPORT_MAX_DATE_SPAN_DAYS` / `EXPORT_CHUNK_ROWS` | 48 / 3 / 31 / 50000 | Retention (48h) must match the UI's "available for 48 hours" copy |
| Reports dir | `REPORTS_DIR` | `/var/lib/radonaix/reports` | Shared by api + worker; **readable by nginx** for X-Accel downloads |

**Scale hardening** (billion-row exports — full rationale in the Download Center guide). Changing any of these needs a **`radonaix-worker`** restart too, not just the API:
| Knob | Env var | Default | Notes |
|---|---|---|---|
| gzip level | `EXPORT_GZIP_LEVEL` | `1` | 1 ≈ half the CPU/wall-clock of level 9 for ~30–50% larger files |
| Redis visibility timeout | `EXPORT_VISIBILITY_TIMEOUT_SECONDS` | `43200` (12h) | **Both limits below must stay under this** — with `acks_late`, a task that outlives it is re-delivered (→ duplicate run) |
| Job time limit | `EXPORT_JOB_SOFT_LIMIT_SECONDS` | `39600` (11h) | Single-file `run_export` + `finalize_export`. Soft; hard = +5 min |
| Part time limit | `EXPORT_PART_SOFT_LIMIT_SECONDS` | `21600` (6h) | Each fan-out part. Soft; hard = +5 min |
| PG count timeout | `EXPORT_COUNT_TIMEOUT_SECONDS` | `15` | Postgres exact-count timeout → indeterminate progress (no 0% stall) |
| Histogram timeout | `EXPORT_HISTOGRAM_TIMEOUT_SECONDS` | `20` | Per-day density sizing pass; on timeout → conservative fallback plan |
| Progress throttle | `EXPORT_PROGRESS_COMMIT_SECONDS` | `2` | Progress persistence interval (was per-chunk) |
| Multi-part trigger | `EXPORT_MULTIPART_ROW_THRESHOLD` / `EXPORT_TARGET_PART_ROWS` / `EXPORT_DAYS_PER_PART` | `2M` / `5M` / `7` | Fan out by **estimated rows** (not calendar span) so wide/no-date exports parallelise; parts packed by rows, capped at N days |
| Part caps | `EXPORT_SOFT_MAX_PARTS` / `EXPORT_HARD_MAX_PARTS` / `EXPORT_SINGLE_FILE_HARD_MAX_ROWS` | `60` / `200` / `50M` | Warn / reject above N parts; reject an un-partitionable (no date column) giant |
| Retries | `EXPORT_PART_MAX_RETRIES` / `EXPORT_FINALIZE_MAX_RETRIES` (+ `*_RETRY_COUNTDOWN`) | `2` / `2` (60s / 30s) | A transient blip retries just that part (siblings kept); job fails only after retries exhaust |
| Excel split | `EXPORT_CSV_SPLIT_ROWS` | `1000000` | Output is a `.zip` of CSVs, each ≤ this many rows (Excel-openable); `0` = single `.csv.gz` |
| Disk guard | `EXPORT_BYTES_PER_ROW_ESTIMATE` / `EXPORT_MIN_FREE_BYTES` / `EXPORT_MAX_TOTAL_STORAGE_GB` | 200 / 20 GB / 800 | Refuse up front unless the volume holds the export's **estimated peak** (sized from the planner's row estimate, gzip ratio + multipart concat peak) + the free floor; and under the live-storage cap |
| nginx download offload | `EXPORT_XACCEL_ENABLED` / `EXPORT_XACCEL_LOCATION` | `true` / `/_export_files` | X-Accel-Redirect (resumable Range); `false` → FastAPI streams the file |

### General
| Knob | Env var | Default |
|---|---|---|
| Environment | `ENVIRONMENT` | `development` (`production` on server) |
| Debug | `DEBUG` | `false` |
| Log level / JSON | `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` |
| CORS origins | `CORS_ORIGINS` | localhost dev origins |

---

## 2. systemd (process & infrastructure)

| Knob | File | Current | How to change | Restart |
|---|---|---|---|---|
| **Gunicorn worker count** | [deploy/systemd/radonaix-api.service](../deploy/systemd/radonaix-api.service) `Environment=WEB_CONCURRENCY` (+ [override.conf](../deploy/systemd/radonaix-api.service.d/override.conf.example)) | `2` | Edit the **drop-in** `/etc/systemd/system/radonaix-api.service.d/override.conf`, not the unit | `systemctl daemon-reload && systemctl restart radonaix-api` |
| Request timeout / graceful | same unit `--timeout` / `--graceful-timeout` | `60` / `30` | Edit `ExecStart` | daemon-reload + restart |
| Worker recycle | same unit `--max-requests` / `--max-requests-jitter` | `2000` / `200` | Edit `ExecStart` | daemon-reload + restart |
| Run-as user | unit `User`/`Group` (override on root hosts) | `radonaix` (override → root) | Drop-in | daemon-reload + restart |
| Bind address | unit `-b` | `127.0.0.1:8000` | Edit `ExecStart` | daemon-reload + restart |
| Prometheus retention | [deploy/systemd/prometheus.service](../deploy/systemd/prometheus.service) `--storage.tsdb.retention.time` | `30d` | Edit unit | `systemctl restart prometheus` |
| postgres_exporter DSN | `/etc/monitoring/postgres_exporter.env` (NOT committed, chmod 600) | — | Edit env file | `systemctl restart postgres_exporter` |

> **Worker count canonical location** is the systemd drop-in. It *can* technically be
> set in `.env` (the unit has `EnvironmentFile=.env`), but keep it in the unit so process
> sizing stays a server concern, not an app-config one.

---

## 3. Monitoring config (alert thresholds, scrape, SMTP — config-as-code, in git)

| Knob | File | Current | Restart |
|---|---|---|---|
| CPU / mem / disk alert threshold | [deploy/grafana/provisioning/alerting/rules.yaml](../deploy/grafana/provisioning/alerting/rules.yaml) `evaluator params` | `85` (`for: 5m`) | `systemctl restart grafana-server` |
| Target-down alert | rules.yaml `radonaix-target-down` | `up < 1` for `3m` | restart grafana |
| Postgres connections alert | rules.yaml `radonaix-pg-connections` | `>80%` for `5m` | restart grafana |
| Alert eval interval | rules.yaml `interval` | `1m` | restart grafana |
| Alert recipients | [contactpoints.yaml](../deploy/grafana/provisioning/alerting/contactpoints.yaml) `addresses` | manojbm@, kalyanm@platum-ai.co.in | restart grafana |
| SMTP from / host | [deploy/grafana/grafana.ini](../deploy/grafana/grafana.ini) `[smtp]` + `GF_SMTP_PASSWORD` env | platum07@gmail.com | restart grafana |
| Scrape interval | [deploy/prometheus/prometheus.yml](../deploy/prometheus/prometheus.yml) `scrape_interval` | `15s` | `systemctl restart prometheus` |
| postgres_exporter custom query | [deploy/monitoring/postgres_exporter_queries.yaml](../deploy/monitoring/postgres_exporter_queries.yaml) | radonaix_pg_connections_{total,active,idle} | restart postgres_exporter |

---

## 4. Code-only (edit + redeploy — not configurable at runtime)

| Knob | File | Notes |
|---|---|---|
| Report definitions (SQL, count, columns) | [app/modules/reporting/service.py](../app/modules/reporting/service.py) `REPORTS` registry | Add/modify a report |
| Pipeline source registries | [app/core/data_sources.py](../app/core/data_sources.py) (stream registry) → [operations/service.py](../app/modules/operations/service.py) + [reporting/catalog.py](../app/modules/reporting/catalog.py) | Generated from `DATA_STREAMS`; add a conventional source via `.env`, not code |
| RBAC permission matrix | `app/core/rbac.py` | Roles → permissions |
| nginx body size / proxy timeout / export offload | [deploy/nginx/radonaix.conf](../deploy/nginx/radonaix.conf) | `client_max_body_size 10m`, `proxy_read_timeout 300s`, and the internal `location /_export_files/` (alias = `REPORTS_DIR`, `sendfile`) for X-Accel downloads → `systemctl reload nginx` |

---

## 5. UI build env (`VITE_*`, build-time)

In the UI repo's `.env` / `.env.production`. Baked in at **build** time — rebuild the UI to change.

| Knob | Var | Notes |
|---|---|---|
| API base | `VITE_API_BASE_URL` / `VITE_AUTH_API_BASE` / `VITE_PIPELINES_API_BASE` | Relative in `.env.production` (single-origin) |
| Grafana URL | `VITE_GRAFANA_URL` | Defaults to `/grafana` behind nginx |
| Superset URL | `VITE_SUPERSET_URL` | |
| Reports auto-refresh | `REFRESH_MS` in `src/routes/reports.tsx` (code constant, `30000`) | Edit + rebuild |

---

## Common tasks (cheat-sheet)

| I want to… | File | Change | Then |
|---|---|---|---|
| Change the reports on-screen limit | backend `.env` | `REPORTS_DETAIL_LIMIT=<n>` | `systemctl restart radonaix-api` |
| Change the CSV export ceiling | backend `.env` | `REPORTS_EXPORT_LIMIT=<n>` | restart radonaix-api |
| Tune large-export behaviour | backend `.env` | `EXPORT_GZIP_LEVEL` / `EXPORT_MULTIPART_*` / `EXPORT_VISIBILITY_TIMEOUT_SECONDS` | restart radonaix-api **and** radonaix-worker |
| Add a new data source | backend `.env` | `DATA_STREAMS=air,sdp,<key>` (+ `DATA_SOURCES_FILE` if non-conventional) | restart radonaix-api (+ worker) |
| Change gunicorn worker count | `radonaix-api.service.d/override.conf` | `Environment=WEB_CONCURRENCY=<n>` | `systemctl daemon-reload && systemctl restart radonaix-api` |
| Change DB pool sizes | backend `.env` | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `RA_PG_POOL_SIZE` / `RA_PG_MAX_OVERFLOW` | restart radonaix-api (mind the connection budget) |
| Change an alert threshold | `provisioning/alerting/rules.yaml` | edit `evaluator params` / `for` | `systemctl restart grafana-server` |
| Change alert recipients | `provisioning/alerting/contactpoints.yaml` | edit `addresses` | restart grafana-server |
| Add/rotate SSO creds | backend `.env` | `GOOGLE_*` / `MS_*` | restart radonaix-api |
| Change token expiry | backend `.env` | `ACCESS_TOKEN_EXPIRE_MINUTES` | restart radonaix-api |
| Change pipeline look-back default | backend `.env` | `PIPELINE_DEFAULT_HOURS=<n>` | restart radonaix-api |
| Change Prometheus retention | `prometheus.service` | `--storage.tsdb.retention.time` | `systemctl restart prometheus` |
