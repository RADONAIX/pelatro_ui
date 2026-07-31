# Rating Assurance Platform — End-to-End Development Plan

> Status: Phases 1-5 delivered, plus the orchestrated pipeline, vendor connectors
> and rule simulation. Phase 6 (stateful rating: bundles, tiers, promotions,
> balance ledger) is built and tested in the engine; its end-to-end scenario and
> balances UI remain. The UX enhancement plan (see RATING_UX_ENHANCEMENT_PLAN.md)
> has landed its Phases A-B and most of C: reconciliation navigation, assurance
> overview, run detail, records explorer, CDR investigation, exception cases,
> reconciliation dashboard, revenue leakage, approvals inbox, snapshot compare.
> Outstanding: Phases 7-9 here, plus Reports and Replay from the UX plan.
> Everything below is scoped so that **no existing
> RADONaix functionality, screen, route, API, table or theme token is modified**.

---

## 0. Where this lands in the existing estate

Today's repo:

| Folder | What it is | Touched by this programme? |
|---|---|---|
| `ra_backend/` | FastAPI modular monolith. Owns Postgres schema `administration` (users, roles, sessions, audit, export jobs). Reads `rafms` ClickHouse + `rafms_app`/`rafms` Postgres read-only. Serves `/api/*` on :8000. | **No code changes. Zero.** |
| `ra_demo/` | TanStack Start (React 19 + Vite + Tailwind v4) UI. | **Additive only** — new files, plus 2 surgical edits described in §3. |
| `ra_demo/Complete_Monitoring_NoAuth/` | Separate Next.js ServerOps app. | **No changes.** |
| `ra_rating_backend/` | **NEW** — the Rating Assurance service. | Everything new lives here. |

### Why a separate service

The rating engine has a fundamentally different runtime profile from the
existing API: it writes bulk to ClickHouse, holds long-running compile and
rating jobs, and needs its own release cadence. Coupling it into `ra_backend`
would mean shared process memory, shared connection pools, a shared Alembic
history, and a shared blast radius — exactly what we were told to avoid.

### Isolation contract (enforced, not aspirational)

1. **Process** — new FastAPI app on port **8010**, own gunicorn unit, own
   Dockerfile. `ra_backend` is never imported.
2. **Postgres** — same server, **new schema `rating`**, own Alembic history
   starting at `0001`. The `administration` schema is opened through a
   **read-only** engine (`default_transaction_read_only=on`) used solely to
   resolve the signed-in user. A write to `administration` is impossible at the
   connection level, not just by convention.
3. **ClickHouse** — same server, **new database `rating_assurance`**. The
   existing `rafms` database is never referenced.
4. **Auth** — the rating service *verifies* JWTs issued by `ra_backend`
   (shared `JWT_SECRET`/`JWT_ALGORITHM`). It never issues, refreshes or revokes
   one. Login stays entirely with `ra_backend`.
5. **RBAC** — rating permission keys live in `rating.role_permissions`, keyed by
   the existing role slug. `administration.roles.permissions` is never written,
   so the existing Role Management screen keeps working untouched.
6. **HTTP** — mounted at `/api/rating/*`. nginx matches the longest prefix, so
   `location /api/rating/` wins over `location /api/` and existing routing is
   unaffected.
7. **UI** — new routes under `/rating/*`, new lib files. The existing sidebar
   renders byte-identically for every scope that is not `Rating Assurance`.

---

## 1. Target architecture

```
                          RULE CONTROL PLANE                    (ra_rating_backend)
┌──────────────────────────────────────────────────────────────────────────┐
│ React (ra_demo, /rating/* routes, shown only when scope = Rating)        │
│   Rule Builder │ Import │ Validation │ Approval │ Simulation │ Exceptions │
├──────────────────────────────────────────────────────────────────────────┤
│ FastAPI :8010  /api/rating/*                                             │
│   catalog · rules · imports · connectors · validation · compiler          │
│   cdr · rating-runs · assurance · exceptions · simulation · replay        │
├──────────────────────────────────────────────────────────────────────────┤
│ PostgreSQL schema `rating`                                               │
│   canonical metadata · rule versions · approvals · snapshots · workflow   │
├──────────────────────────────────────────────────────────────────────────┤
│ Rule Validator → Rule Compiler → immutable Executable Snapshot            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                ▼
                          DATA EXECUTION PLANE
┌──────────────────────────────────────────────────────────────────────────┐
│ Airflow DAGs   rule_import · rule_publish · rating · replay · reports     │
├──────────────────────────────────────────────────────────────────────────┤
│ ClickHouse db `rating_assurance`                                         │
│   *_cdr_landing · cdr_normalized · cdr_enriched · rating_context          │
│   executable_rules · context_rule_map · rating_results · rating_trace     │
│   rating_exceptions · agg_* reporting tables                             │
├──────────────────────────────────────────────────────────────────────────┤
│ Stateless path: bulk ClickHouse SQL                                      │
│ Stateful path : Python workers partitioned by account/subscriber/bucket   │
├──────────────────────────────────────────────────────────────────────────┤
│ Assurance Engine  expected vs actual → variance → RCA → revenue leakage   │
└──────────────────────────────────────────────────────────────────────────┘
```

**The central principle** (from the requirement, and it drives every design
decision below): *rule resolution and calculation happen in bulk over indexed
contexts, never per-CDR-per-rule.* 1 crore CDRs collapse to ~50k–100k distinct
rating contexts; each context resolves its rule set **once**; the result is
joined back to every CDR.

---

## 2. Phase plan

Each phase is independently shippable and leaves the product in a working
state. The requirement's own 7-phase roadmap (§29) and MVP definition (§30) are
mapped into 8 build phases below.

---

### Phase 1 — Foundation, canonical model, rule authoring and import **(delivered)**

Covers requirement §4 (canonical metadata), §3A (manual rule creation), §3B
(file-based import), §5 (versioning), §6 (structural validation), §26 (RBAC),
§28 (rule-management screens), and the scope-aware UI shell.

**Backend**
- Service scaffold: config, structured logging, request-context + rate-limit
  middleware, error envelope, Prometheus `/metrics`, health/readiness.
- Read-only identity bridge to `administration` → `Principal`.
- Rating RBAC: 8 permission keys, per-role matrix in `rating.role_permissions`.
- Canonical metadata tables + CRUD: services, products, offers, tariff plans,
  time bands, destination zones, destination prefixes, rating groups,
  currencies, tax rules, rounding rules, discount definitions, bundle
  definitions, promotions.
- Canonical rule model: `rule_sets`, `rules`, `rule_conditions`,
  `rule_actions`, `rule_audit`, `rule_templates`.
- Rule lifecycle skeleton: `DRAFT → VALIDATED` plus versioning, cloning,
  new-version-from-published, retire.
- Structural validation: mandatory fields, data types, operator/attribute
  compatibility, action parameter schema, date-range sanity, currency validity.
- Rule attribute dictionary + operator + action-type catalogue endpoints — these
  drive the visual condition/action builder in the UI.
- Catalogue form schema endpoint, derived from each entity's own Pydantic model,
  so the UI's create/edit forms are generated rather than hand-maintained.
- File-based rule import (CSV / TSV / Excel / JSON): heading auto-mapping with
  vendor aliases, dry-run preview, per-row validation against the live
  catalogue, partial commit, and a rejected-rows export in the source file's
  own column order.
- Alembic `0001` and `0002`, seed reference data.

**Frontend**
- `AssuranceScopeProvider` — lifts the header's existing localStorage scope
  value into a context (same key, same default, same visual).
- Sidebar swaps its module list when scope = `Rating Assurance`; **identical
  render for every other scope**.
- `ratingApi` axios client → `/api/rating`.
- Screens: Rating overview, Rule Catalogue, Create Rule (guided form + visual
  condition builder with AND/OR groups + action builder), Rule Detail
  (conditions, actions, versions, audit), Import Rules (upload → map → preview →
  commit → download rejects), Metadata Catalogue (tabbed, with create/edit).

**Acceptance** — an analyst can author a multi-condition voice tariff rule
through the UI, save it as a draft, validate it, clone it, and cut version 2;
or upload a vendor tariff sheet and have every valid row land as a draft rule
with the bad rows returned for correction. Switching the scope back to
Mediation Assurance restores the original app exactly.

---

### Phase 2 — Governance, validation depth, compiler & snapshots **(delivered)**

Covers §5 (full lifecycle + maker-checker), §6 (business/conflict/coverage
validation), §7 (compiler & publishing).

- Full lifecycle: `DRAFT → VALIDATED → REVIEWED → APPROVED → COMPILED →
  PUBLISHED → ACTIVE → SUPERSEDED → RETIRED`, multi-level approval, scheduled
  activation/expiry, rollback, rule comparison (structural diff), impact
  analysis, approval notifications.
- Business validation (referenced product/offer/zone/time band/tax exists,
  effective dates coherent), conflict validation (overlap detection, same-
  priority collisions, duplicate/contradictory actions, circular dependency,
  mutually-exclusive promotion stacking), coverage validation (missing product
  tariff, missing destination, missing default rule, expired-with-no-successor).
- **Compiler**: canonical → executable. Normalise values, resolve wildcards,
  compute specificity score, build the lookup key, order by dependency stage,
  precompile formulas, emit an immutable snapshot with checksum, publish the
  executable rule set into ClickHouse `executable_rules`.
- Snapshot management: list, compare, activate, rollback.
- Screens: Pending Approvals, Version History, Rule Comparison, Audit Trail,
  Activation Calendar, Validation Summary (errors/warnings/conflicts/gaps),
  Compile Rule Set, Published Snapshots, Snapshot Comparison.

---

### Phase 3 — CDR ingestion, normalization, enrichment, context & rule selection **(delivered)**

Covers §8–§12.

- Ingestion: file / SFTP / API / batch, duplicate detection by
  `(source, file, record_hash)`, corrupt-record quarantine, file + batch
  tracking, record-count reconciliation. Landing tables per source
  (`msc_cdr_landing`, `sms_cdr_landing`, `data_cdr_landing`,
  `roaming_cdr_landing`).
- Normalization to the common usage model (§9): field mapping, type/timezone/
  number/currency/duration/unit conversion, invalid-value handling.
- Enrichment (§10): subscriber, account, product, offer, service class,
  origin/destination zone, on-net/off-net, time band, roaming status, network
  type, rating group, tax jurisdiction, bundle & promotion eligibility. Data-
  quality statuses (`PRODUCT_NOT_FOUND`, `DESTINATION_NOT_FOUND`, …).
- Context builder (§11): deterministic context key + hash, unique-context
  extraction, frequency tracking, missing-dimension tracking.
- Rule selection engine (§12): staged filtering, 4-level fallback (exact →
  product → service → global default), priority/specificity/version/conflict-
  group/stacking resolution, `context_rule_map` cache.
- Screens: CDR Batch Monitor, File Monitor, Load Summary, Failed/Duplicate
  Records, Data Quality Summary, Rule Selection View.

---

### Phase 4 — Voice rating + expected vs actual + trace + exceptions **(delivered — MVP complete)**

Covers §13 (voice), §15, §16, §17, §19 (basic).

- Stateless rating in bulk ClickHouse SQL: billable quantity → minimum charge →
  pulse → base charge → tax → rounding → final expected charge, with every
  component stored separately.
- Voice scenarios: per-second, per-minute, pulse, minimum call charge,
  connection fee, on-net/off-net, local/international, peak/off-peak.
- Rating trace: an ordered, human-readable explanation per CDR (step, rule id,
  input, output) — the explainability requirement.
- Assurance engine: expected vs actual across every component; statuses
  `MATCHED / UNDERCHARGED / OVERCHARGED / UNRATED / ZERO_CHARGE /
  NO_MATCHING_RULE / MULTIPLE_RULE_MATCH / …`; undercharge and overcharge kept
  as separate signed measures.
- Exception generation + list/detail screens.

At the end of Phase 4 the MVP defined in requirement §30 is delivered.

**Delivered, and verified end to end.** A generated MSC batch of 619 records
with deliberately injected defects was ingested, enriched, rated and compared:

| Injected defect | Rows | Detected as | Attributed to |
|---|---|---|---|
| Tax not applied by billing | 120 | UNDERCHARGED | `TAX_INCORRECT` |
| Pulse ignored (billed on measured seconds) | 80 | UNDERCHARGED | `WRONG_PULSE` |
| Rateable usage with no charge | 40 | UNRATED | `BILLING_DEPLOYMENT_ISSUE` |
| Traffic with no tariff (off-net, emergency) | 75 | NO_MATCHING_RULE | `NO_TARIFF_DEFINED` |
| Rounding-mode difference | 135 | UNDERCHARGED | `ROUNDING_ISSUE` |
| Unparseable records | 2 | rejected at ingestion | — |
| Duplicate record | 1 | caught by record hash | — |

616 CDRs were rated from **3 rule decisions** — the context-collapse the §27
scale target depends on.

**Known limitation.** The per-CDR arithmetic runs in Python, streamed in chunks.
Rule *resolution* — the expensive part — already runs once per context, so the
architecture meets §27; translating `_rate_chunk` into bulk ClickHouse SQL is
the remaining scale step, not a redesign.

---

### Phase 5 — Rule onboarding at scale: connector and API import **(delivered)**

Covers §2 (source systems & connectors), §3C/D. File import (§3B) shipped in
Phase 1.

**Delivered.** Source-system registry with vendor, type
(DB/API/SFTP/FILE/XML/JSON/CSV/Excel), credentials held apart from connection
settings and never returned by the API, cron schedule, import mode, health
status and test-connection.

**Three vendor adapters, each mapping a real export shape:**

| Vendor | Native concept | Mapped to |
|---|---|---|
| **Oracle BRM** | rate plans + rate tiers; `impact_category`, `rum`, `beat` | base tariff + pulse; RUM selects the charging unit |
| **Ericsson CS** | tariff classes + rate steps; `firstInterval`/`nextInterval` | one rule per step; intervals become a pulse, `minimumCharge` a floor |
| **Huawei CBS** | pricing plans + rating segments; numeric `serviceFlag` | base tariff + pulse + tax from `taxSchema` |

Plus a canonical passthrough adapter. Nokia, Amdocs, Netcracker, product
catalogue, CRM, tax and numbering-plan sources are **declared in the catalogue
as planned** so the gap is visible rather than silently absent — they can still
be onboarded today via CSV or the canonical format.

**Change detection is the substance of it.** A vendor export is a full dump
every night. Each mapped rule is fingerprinted over the parts that change its
*behaviour* — conditions, actions, priority, validity — excluding name and
description. Re-importing an unchanged export therefore reports
`0 created, 0 updated, N unchanged` instead of versioning every rule, and one
changed rate reports exactly `1 updated`.

- Public rule APIs: `POST /rules`, `/rules/import`, `/rules/validate`,
  `/rating-simulation`.
- Remaining in this phase: a live SFTP/JDBC fetch (the registry, health check
  and mapping are in place; the transport currently accepts a pushed export).

---

### Phase 5a — Orchestrated pipeline **(delivered, ahead of Phase 8)**

The architecture diagrams show CDR processing as six visible stages, so the
synchronous "upload and rate" call was replaced by a real orchestration:

    CDR_INGESTION → CDR_NORMALIZATION → DATA_ENRICHMENT
    → RULE_SELECTION → RATING_CALCULATION → RATING_ASSURANCE

- The upload returns in ~100 ms with a run id; stages run in the background and
  the UI polls. A 1-crore batch cannot be a synchronous HTTP request, and a
  request that times out mid-run looks exactly like data loss.
- Every stage records status, records-in, records-out, records-failed, duration
  and a human-readable detail line.
- **Each stage commits its own transaction**, so a failure is recoverable in
  place: when enrichment fails on a missing prefix, load the prefix and re-run
  *from enrichment* — ingestion and normalization stay committed. Verified.
- `deploy/airflow/rating_pipeline_dag.py` defines `ra_rating_pipeline`,
  `ra_rule_import` and `ra_rule_publication` calling the **same** stage
  functions, so in-process and Airflow execution are identical by construction.

---

### Phase 5b — Rule simulation **(delivered, ahead of Phase 6)**

Screen 4 of the design. Rates one CDR against any snapshot — including one not
yet activated — and writes nothing. It runs the **real engine against the real
compiled snapshot**, never an approximation: a simulator that approximates the
engine is worse than none, because it builds confidence in a number the engine
would not produce. Returns the enrichment result, every candidate rule with why
it won or lost, the full calculation trace, and — when a billed amount is
supplied — the assurance verdict and root cause.

---

### Phase 5c — Remaining connector work

- Connector SDK + vendor parsers → canonical mapper, for Oracle BRM, Ericsson
  CS, Huawei CBS, Nokia, Amdocs, Netcracker, custom, plus catalogue/CRM/tax/
  prefix systems. Full / incremental / change-only / scheduled imports with
  new-changed-deleted detection and import comparison.
- Public rule APIs: `POST /rules`, `/rule-sets`, `/rules/import`,
  `/rules/validate`, `/rules/simulate`.

---

### Phase 6 — Advanced charging & stateful rating

Covers §13 (SMS/data/roaming), §14, §20.

- Discounts, promotions, bundles, shared bundles, tiered & progressive
  charging, accumulated discount, balance consumption, first-call-of-day.
- Stateful rating in Python workers partitioned by
  `account_id / subscriber_id / balance_bucket_id` with strict event ordering
  and idempotent retry.
- SMS (per message, destination, bundle, premium), Data (per KB/MB/GB, session
  minimum, tiering, APN pricing), Roaming (country/operator/zone, surcharge,
  tax).
- Simulation module: single CDR, bulk file, snapshot A/B comparison, candidate
  vs selected vs rejected rules, revenue-impact estimate, simulation history.

---

### Phase 7 — Assurance operations

Covers §18, §19 (full), §21.

- Root-cause analysis: first-component-mismatch detection, expected vs applied
  tariff, affected rule/product/subscribers/dates, revenue impact, exception
  grouping, probable-cause suggestion across the 12 root-cause categories.
- Exception & case management: full lifecycle, auto-grouping, severity,
  priority, assignment, comments, attachments, SLA, escalation, resolution,
  reopen, linked exceptions.
- Replay & reprocessing: by CDR / batch / date range / product / rule /
  exception group; old-vs-new comparison; recovered-revenue tracking.

---

### Phase 8 — Productisation: dashboards, reports, Airflow, performance, security

Covers §22, §24, §25, §26, §27.

- Executive / Operational / Rule / Product dashboards; the 9 named reports;
  scheduled + custom reports; export centre.
- Airflow DAGs exactly as specified in §24 (rule import, rule publication,
  rating, replay), with parallel task fan-out and idempotent retry.
- Performance hardening to the §27 scale target: 1 crore CDRs, 10,000+ rules,
  50k–100k contexts, 1–20 candidates per context. Partitioning by
  `event_date / source_batch_id / service_type / subscriber_hash`.
- Multi-tenancy, sensitive-data masking, encryption, publishing/replay
  authorization, HA.

---

### Phase 9 — Intelligence (§7 of their roadmap)

AI rule mapping, rule explanation in natural language, AI conflict detection,
AI root-cause suggestion, revenue-impact prediction, anomaly detection. Built on
Claude models via the Anthropic API, always as an *advisory* layer — a
suggestion never mutates a rule without maker-checker approval.

---

## 3. The only two edits to existing files

Everything else in `ra_demo` is a new file. These two are additive and
behaviour-preserving:

1. **`src/routes/__root.tsx`** — wrap the tree in `<AssuranceScopeProvider>`
   (one more provider, alongside the existing Auth/Downloads/Language ones).
2. **`src/components/layout/Header.tsx`** — the scope `useState` +
   `useEffect(localStorage)` pair is replaced by `useAssuranceScope()`. Same
   storage key (`radonaix_scope`), same default (`Mediation Assurance`), same
   markup. The dropdown renders identically.
3. **`src/components/layout/Sidebar.tsx`** — one branch: if scope is
   `Rating Assurance`, render the rating module list; otherwise fall through to
   today's exact code path.

`ra_backend` is not edited at all.

---

## 3a. What is not yet built

Phases 5-9 remain. In priority order:

| Phase | Outstanding |
|---|---|
| **6** | Bundles, promotions, shared balances, tiered charging, **stateful rating workers**; SMS/data/roaming rating; bulk simulation |
| **7** | Replay & reprocessing, recovered-revenue tracking, SLA/escalation on exceptions, attachments |
| **8** | Executive/operational/product dashboards, the 9 named reports, **Airflow DAGs**, ClickHouse bulk-SQL rating, multi-tenancy, HA |
| **9** | AI rule mapping, NL rule explanation, AI conflict detection, RCA suggestion, anomaly detection |

Also outstanding inside the delivered phases: the maker-checker **approvals
screen** (the API and RBAC exist and are enforced; the queue UI does not),
snapshot **diff** in the UI (the endpoint exists), live SFTP/JDBC transport for
connectors (registry and mapping are in place; exports are pushed), and
adapters for Nokia, Amdocs and Netcracker.

---

## 4. Deployment delta

```nginx
# ADD above the existing `location /api/` block — longest-prefix wins,
# so the existing block is unaffected.
location /api/rating/ {
    proxy_pass http://radonaix_rating_api;   # 127.0.0.1:8010
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 600s;   # compiles and rating runs are long
}
```

Plus one systemd unit (`radonaix-rating-api.service`) and a
`rating_assurance` ClickHouse database. No change to any existing unit.
