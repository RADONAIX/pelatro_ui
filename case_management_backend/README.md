# RA Backend

FastAPI backend for the RA UI application, on PostgreSQL. No authentication.

Two things live here:

* **Control rules** (`/api/rules`) — the catalog the Rule Explorer lists and
  edits. A rule is metadata: assurance, entity scope, primitive category,
  parameters and a schedule.
* **Case management** (`/api/cases`) — the single queue every assurance raises
  findings into. A case is opened by the rule engine when a control fails, or
  by an analyst, and carries the assurance, sub-module, rule id, issue type and
  the mismatch values behind the finding.

Both live in the `assurance` schema, created automatically on startup.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (localhost:8001)

```bash
./start.sh          # background, detached; logs to logs/api.log
./start.sh -f       # foreground with --reload
./start.sh stop     # stop the background instance
./start.sh status   # is it listening?
```

### Changing the port

`PORT` in `.env` is the single source of truth — `start.sh`, `run.py` and the
app all read it:

```
PORT=8001          # Pelatro_Backend/.env
```

Then point the UI at the same port (`ra_demo/.env`):

```
VITE_CASES_API_BASE=http://127.0.0.1:8001
```

Restart both (the Vite dev server only reads `.env` at startup). A one-off
override without editing files: `PORT=8002 ./start.sh`.

`start.sh` puts the server in its own session, so it keeps running after the
launching terminal closes. `python run.py` is the foreground equivalent.

> **Do not run a bare `uvicorn app.main:app`.** This machine has a `uvicorn`
> script in *both* the venv and the system Python
> (`/Library/Frameworks/Python.framework/Versions/3.12/bin/uvicorn`), and the
> system one has no dependencies installed. If your shell picks that one — which
> an active `(.venv)` prompt does **not** rule out, because zsh caches command
> paths — you get:
>
> ```
> ModuleNotFoundError: No module named 'fastapi'
> ```
>
> Use any of these instead, all of which pin the interpreter explicitly:
>
> ```bash
> ./start.sh -f                                   # recommended
> python run.py                                   # re-execs into .venv if needed
> .venv/bin/python -m uvicorn app.main:app --reload
> ```
>
> To fix the shell itself: `source .venv/bin/activate && hash -r`, then confirm
> with `which uvicorn` — it must print `.../Pelatro_Backend/.venv/bin/uvicorn`.

**Connect over `127.0.0.1`, not `localhost`.** On a dual-stack machine
`localhost` resolves to `::1` first while uvicorn binds IPv4, so the first
connection attempt is refused — which a browser reports as a failed fetch with
provisional headers. The UI's default base is `http://127.0.0.1:8001` for that
reason.

On first boot an empty schema is loaded with the control-rule catalog (67 rules
across the 9 assurances) and 40 cases raised through them, spanning the whole
lifecycle. Seeding is idempotent per rule id and per case dedupe key, so adding
entries to `rules_seed.py` / `seed.py` tops the database up on the next start
without disturbing the rows already there. Set `SEED_DEMO_DATA=false` to skip it.

## Schema (`assurance`)

| Table | Holds |
| --- | --- |
| `control_rules` | The rule catalog — `UA001`, its category, entity scope, parameters, schedule and lifecycle state |
| `cases` | One finding, with the rule identity and the measured expected/actual/variance |
| `case_mismatches` | Record-level evidence behind a case |
| `case_comments` | Investigation notes |
| `case_activities` | Audit trail — one row per field change |

## Endpoints

| Method   | Path                          | Purpose                                                        |
| -------- | ----------------------------- | -------------------------------------------------------------- |
| `GET`    | `/api/cases`                  | Filtered, sorted, paginated case list                          |
| `GET`    | `/api/cases/summary`          | Tile counts for the current filters                            |
| `GET`    | `/api/cases/facets`           | Filter options with live counts                                |
| `GET`    | `/api/cases/export.csv`       | The filtered set as CSV                                        |
| `POST`   | `/api/cases/ingest`           | **Rule engine** — raise a case from a failed control           |
| `POST`   | `/api/cases/ingest/bulk`      | Raise many cases from one rule cycle                           |
| `POST`   | `/api/cases`                  | Raise a case manually (Add Case dialog)                        |
| `GET`    | `/api/cases/{id}`             | One case + mismatches, notes, audit trail (id or `CASE-####`)   |
| `PATCH`  | `/api/cases/{id}`             | Partial update; every change is audited                        |
| `POST`   | `/api/cases/{id}/assign`      | Assign / unassign                                              |
| `POST`   | `/api/cases/{id}/status`      | Move through the lifecycle, optionally with a note             |
| `POST`   | `/api/cases/{id}/comments`    | Add an investigation note                                      |
| `POST`   | `/api/cases/{id}/insights`    | Pin an assistant reply to the case                             |
| `GET`    | `/api/cases/{id}/mismatches`  | The mismatch evidence, paginated                               |
| `POST`   | `/api/cases/{id}/mismatches`  | Attach further mismatch rows                                   |
| `DELETE` | `/api/cases/{id}`             | Delete a case and everything under it                          |
| `GET`    | `/api/rules`                  | The provisioned controls, filtered and paginated               |
| `GET`    | `/api/rules/stats`            | Rule counts by category, assurance and last run status         |
| `POST`   | `/api/rules`                  | Author a rule (id generated as `UA001`, `UA002`…)              |
| `GET`    | `/api/rules/{id}`             | One rule + a live count of cases open against it               |
| `PATCH`  | `/api/rules/{id}`             | Edit a rule, incl. promoting Draft → Active                    |
| `DELETE` | `/api/rules/{id}`             | Delete a rule; the cases it raised are kept                    |
| `POST`   | `/api/rules/{id}/run`         | **Report a control execution** — a failure opens a case        |
| `GET`    | `/api/catalog/meta`           | Assurances, modules, rule categories, vocabularies             |
| `GET`    | `/health`                     | Liveness probe                                                 |
| `GET`    | `/docs`                       | Swagger UI                                                     |

### List filters

All repeatable (`?status=Open&status=In+Progress`) and shared by
`/api/cases`, `/summary` and `/export.csv`:

`q`, `assurance` (code or name), `group`, `module`, `category` (issue type),
`status`, `severity`, `origin`, `action`, `owner`, `ruleId`, `stream`,
`assignment` (`all|assigned|unassigned|mine` + `me`), `dateFrom`, `dateTo`,
`dateField` (`createdAt|detectedAt|updatedAt`), `openOnly`, plus `page`,
`pageSize`, `sortBy`, `sortDir`.

## Raising a case from a rule (the integration point)

The engine reports the outcome of a run and the case builds itself from the
rule. `PASS` just stamps the rule; `FAIL`/`WARNING` opens a case that inherits
the assurance, module, issue type, feeds, tolerance and severity from the rule
definition, so the run only supplies what it measured:

```bash
curl -X POST http://localhost:8001/api/rules/UA001/run \
  -H 'Content-Type: application/json' -d '{
    "status": "FAIL",
    "runId": "RUN-UA-77412",
    "title": "Missing CDR — switch vs mediation count breach",
    "description": "MSC exported 1,284,322 CDRs; mediation landed 1,281,904.",
    "expectedValue": "1,284,322", "actualValue": "1,281,904",
    "variance": "-2,418", "variancePct": -0.19,
    "estimatedImpact": 18450, "affectedCount": 2418,
    "nodeId": "MSC-EU-1", "stream": "MSC", "linkedBatch": "BATCH-441A",
    "dedupeKey": "UA001:2026-07-30:MSC-EU-1",
    "mismatches": [
      {"recordRef": "CDR100251", "entity": "MSC-EU-1", "subscriber": "254710061234",
       "field": "mediation_record", "expectedValue": "present",
       "actualValue": "missing", "delta": "1 record", "status": "SOURCE_ONLY"}
    ]
  }'
```

A rule that is not `Active` returns 409 rather than putting work in front of an
analyst. `dedupeKey` makes retries safe — the same key returns the case that
already exists instead of a duplicate.

### Posting a case directly

`POST /api/cases/ingest` takes the same body plus the classification fields, for
a producer that has no rule row. Supplying `ruleId` still inherits everything
the rule knows; anything you pass explicitly wins.

`assurance` accepts a code (`UA`) or a full name (`Usage Assurance`); the
service resolves it against the catalog and fills in the name and group.
`GET /api/catalog/meta` returns the valid assurances, their modules and the
rule categories, so the rule-authoring UI and the case filters stay in sync.

## Configuration

Copy `.env.example` to `.env`. Every value there is also the built-in default,
so a local checkout runs without one:

```
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=rafms_db_new
DB_SCHEMA=assurance
SEED_DEMO_DATA=true
```

Set `DATABASE_URL` to override the parts (that is how a managed deployment
injects credentials). The schema and its tables are created on startup, so
there is no separate migration step for a first install.

CORS defaults to `*` for local development — restrict `CORS_ORIGINS` before deploying.
