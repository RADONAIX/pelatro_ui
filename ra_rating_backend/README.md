# RADONaix — Rating Assurance service

A **separate** FastAPI service that provides the Rating Assurance module of the
RADONaix platform: canonical tariff metadata, rule authoring, versioning,
validation, compilation, rating and expected-vs-actual assurance.

It runs alongside `ra_backend` and shares nothing with it except a JWT secret.

```
ra_backend          :8000   /api/*          users, reports, pipelines, cases   (unchanged)
ra_rating_backend   :8010   /api/rating/*   rating assurance                   (this service)
ra_demo             :8080   /rating/*       UI, shown when Assurance Scope = Rating Assurance
```

---

## Isolation guarantees

This service was built to be added to a live estate without touching it.

| Boundary | Guarantee |
|---|---|
| Process | Own app, own port, own systemd unit. `ra_backend` is never imported. |
| Postgres | Own schema `rating`, own Alembic history from `0001`. |
| `administration` schema | Reached through an engine opened with `default_transaction_read_only=on`. A write is rejected by **Postgres**, not by convention. |
| ClickHouse | Own database `rating_assurance`. The existing `rafms` database is never referenced. |
| Auth | Verify-only. No login, no refresh, no token minting, no password hashing. |
| RBAC | Rating permission keys live in `rating.role_permissions`. `administration.roles.permissions` is never written, so the existing Role Management screen is unaffected. |
| HTTP | Mounted at `/api/rating`. nginx longest-prefix matching means the existing `location /api/` block does not change. |

---

## Setup

```bash
cd ra_rating_backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set JWT_SECRET to match ra_backend/.env
alembic upgrade head          # creates the `rating` schema and its tables
python -m app.seed            # reference data + example rules
uvicorn app.main:app --port 8010 --reload
```

Open http://localhost:8010/docs.

### Database grants

The rating user needs full rights on its own schema and read-only access to two
administration tables:

```sql
CREATE SCHEMA IF NOT EXISTS rating AUTHORIZATION radonaix;
GRANT USAGE ON SCHEMA administration TO radonaix;
GRANT SELECT ON administration.users, administration.user_sessions TO radonaix;
```

If the rating service uses a dedicated Postgres role, grant it exactly those two
SELECTs and nothing else — then the isolation guarantee holds at the privilege
level as well as the transaction level.

---

## API surface (Phase 1)

| Area | Endpoints |
|---|---|
| Service | `GET /health`, `GET /me`, `GET /permissions` |
| Rule vocabulary | `GET /meta/attributes`, `/meta/operators`, `/meta/actions`, `/meta/enums` |
| Canonical metadata | `GET|POST /catalog/{entity}`, `GET|PATCH|DELETE /catalog/{entity}/{id}`, `GET /catalog/summary` |
| Destination prefixes | `GET /catalog/destination-zones/{id}/prefixes`, `POST|DELETE /catalog/destination-prefixes` |
| Rule sets | `GET|POST /rule-sets` |
| Rules | `GET /rules`, `GET /rules/stats`, `POST /rules`, `GET|PATCH|DELETE /rules/{id}` |
| Rule lifecycle | `POST /rules/{id}/validate`, `/status`, `/versions`, `/clone` |
| History | `GET /rules/{id}/versions`, `GET /rules/{id}/audit` |
| Templates | `GET /rule-templates`, `GET /rule-templates/{code}` |
| Dashboard | `GET /dashboards/overview` |

`{entity}` is one of: `currencies`, `rounding-rules`, `tax-rules`, `services`,
`products`, `offers`, `tariff-plans`, `time-bands`, `destination-zones`,
`rating-groups`, `discounts`, `bundles`, `promotions`.

### The rule vocabulary drives the UI

`/meta/attributes`, `/meta/operators` and `/meta/actions` are generated from
`app/modules/rules/constants.py`. The UI's visual condition and action builders
render whatever those endpoints return, so adding a rating attribute or an
action type is a one-file backend change that appears in the builder with no UI
work — and the builder can never offer something the engine cannot execute.

---

## Rule model in one page

```
rule_key   the logical rule, stable forever          PREPAID_A_VOICE_ONNET_PEAK
version    increments per change                     1, 2, 3 …
id         one immutable version row                 uuid
```

A rule is editable only in `DRAFT` or `VALIDATED`. After approval it is frozen:
changing it means cutting version N+1. Rating results reference the version
`id`, so any historical charge can be re-explained against the exact rule text
that produced it.

Selection is resolved by **specificity first, then priority**. Specificity is
computed from the bound conditions (`service.compute_specificity`): pinning
product + destination + time band scores higher than a service-wide default, so
the requirement's 4-level fallback (exact → product → service → global default)
emerges from the data instead of being hand-coded.

---

## Deployment

Add one location block above the existing `location /api/` in
`ra_backend/deploy/nginx/radonaix.conf` — nginx matches the longest prefix, so
the existing block is untouched:

```nginx
upstream radonaix_rating_api { server 127.0.0.1:8010; }

location /api/rating/ {
    proxy_pass http://radonaix_rating_api;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 600s;   # compiles and rating runs are long
}
```

Metrics are exposed at `/metrics` with a `rating_` prefix, so they can be
scraped into the same Prometheus without colliding with the existing API's
series.

---

## Rating assurance (MSC → expected charge → variance)

Reads MSC switch records from the operator's landing database, prices each call
against the active rule snapshot, compares that with what was billed, and
explains every number.

```bash
alembic upgrade head                 # 0013 adds the assurance layer
python -m scripts.seed_assurance     # optional: the worked example

curl -X POST localhost:8000/api/rating/msc/ingest -d '{"limit": 100000}'
curl -X POST localhost:8000/api/rating/execute    -d '{"batch_size": 5000}'
curl localhost:8000/api/rating/results/MSC01-FILE100-3563/explanation
```

Two things worth knowing before you run it:

* **MSC records carry no charged amount.** That is what a switch record is, not
  a gap in the loader. Without an OCS/IN source configured every result is
  `NO_ACTUAL_CHARGE` — the expected charge is computed and stored, but there is
  nothing to compare it against. It is never inferred as zero.
* **`alembic revision --autogenerate` needs the guard in `migrations/env.py`.**
  This service shares a database, and its search path includes `public`; without
  `_include_object` refusing to drop undeclared tables, autogenerate writes
  `op.drop_table` for another product's tables into the upgrade path.

Full documentation, including the worked example and the idempotency model:
[`docs/RATING_ASSURANCE.md`](docs/RATING_ASSURANCE.md).

---

## Roadmap

See `../RATING_ASSURANCE_PLAN.md` for the full phase-by-phase plan. Phase 1
(this release) delivers the foundation, canonical metadata and manual rule
authoring. Phase 2 adds the approval workflow, deep validation and the rule
compiler; Phase 3 adds CDR ingestion and rule selection; Phase 4 completes the
MVP with voice rating and expected-vs-actual assurance.
