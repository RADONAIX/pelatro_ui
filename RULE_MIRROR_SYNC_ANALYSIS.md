# Mirror-Write Analysis — Rule Management & Metadata Catalogue → `rafms_rating_new`

Status: **analysis complete, implementation complete.** Target DDL received; the
blocker in §9 is resolved. This document is deliverables 1 and 3–6; the
column-by-column mapping (deliverable 2) is in
[RULE_MIRROR_MAPPING.md](RULE_MIRROR_MAPPING.md).

---

## 1. Naming reconciliation (read this first)

The request names `rafms_db` as the current store. That name does not appear
anywhere in this codebase. The actual layout is:

| What the request calls it | What the code actually uses | Where |
| --- | --- | --- |
| `rafms_db` | database **`radonaix_app`** | `settings.rating_db_name`, `app/core/config.py:53` |
| — | schema **`ra_rule`** — canonical rules + catalogue + charging metadata | `rule_db_schema` / `catalog_db_schema` / `bundle_db_schema` / `subscriber_db_schema`, `config.py:75-78` |
| — | schema **`rating`** — execution plane (CDR, runs, results, balances) | `rating_db_schema`, `config.py:56` |
| — | schema **`administration`** — identity, READ-ONLY | `identity_db_schema`, `config.py:96` |
| `rafms_rating` | an **external, READ-ONLY** operator CDR/charge archive | `msc_source_name` / `ocs_source_name`, `config.py:130,158` |

`rafms_rating` is **not** where Rule Management writes. It is a foreign source
the platform reads MSC CDRs and OCS charges from, opened with
`default_transaction_read_only=on`.

**Assumption used throughout:** "`rafms_db`" means the platform's own write
database — `radonaix_app`, schemas `ra_rule` + `rating`. If it is a real,
separate database in your deployment, only the connection string changes; every
finding below holds.

---

## 2. Where Rule Management writes — the complete inventory

### 2.1 The single choke point

`app/modules/rules/ingest/writer.py` → `write()` (line 206) is **the only place
in the service that writes `ra_rule.*` rule data**. This is not a convention —
it is enforced:

- Module docstring, `writer.py:1-8`: *"There is no second path, and from R3 a CI
  test walks the AST to keep it that way."*
- `app/modules/rules/canonical/draft.py:1-5`: `CanonicalDraft` is *"the only
  shape that reaches the writer"*, with an AST check for ORM construction
  outside `ingest/writer.py`.
- Verified: every `db.add(CanonicalRule…)` / `RuleConditionRow` /
  `RuleActionRow` / `RuleParameter` construction in `app/` is inside
  `writer.py`.

Everything — wizard, file import, XML import, every connector, backfill —
funnels through it:

```
UI wizard        ─┐
CSV/XLSX import  ─┤
XML rule import  ─┼─→ adapter → CanonicalDraft → ingest/kernel.py:_write_one
Connector import ─┤                              (line 340)
Legacy backfill  ─┘                                   │
                                                      ↓
                                         ingest/writer.py:write()  ← ONE hook point
                                                      │
                        ┌─────────────┬───────────────┼──────────────┬─────────────┐
                        ↓             ↓               ↓              ↓             ↓
                  ra_rule.rule  rule_version  rule_condition_group  rule_action  rule_audit
                                              rule_condition        rule_parameter
                                                                    rule_set_member
                                                                    rule_source_lineage
```

`write()` **flushes but does not commit** — "the caller owns the commit"
(`writer.py:219`). The commit happens in `get_session()`
(`app/core/database.py:154-161`), one transaction per HTTP request.

### 2.2 Rule status transitions (updates, not inserts)

`app/modules/rules/lifecycle/service.py` mutates `rule.status` /
`version.status` in place and appends `CanonicalRuleAudit` rows:

| Function | Line | Effect |
| --- | --- | --- |
| `_walk` (used by `approve`) | 397-406 | `rule.status` / `version.status` → target |
| `revert` | 483-492 | statuses → `DRAFT` |
| `activate` | 536-631 | activation + audit row |
| `app/modules/rules/api/router.py:_audit` | 741-757 | manual status-change audit |
| `app/modules/rules/lifecycle/runner.py` | 98, 104, 242 | bulk-job commits |

These are the **second** class of write the mirror must observe: a rule already
mirrored can change status without going through `writer.write()`.

### 2.3 Other rule-adjacent writers (in scope for completeness)

| File:line | Table | Note |
| --- | --- | --- |
| `ingest/kernel.py:209` | `rule_ingestion_batch` | batch header |
| `ingest/kernel.py:413` | `rule_ingestion_record` | per-row outcome |
| `ingest/sets.py:94` | `rule_set` | set creation |
| `validation/registry.py:65` | `rule_validation_issue` | validation findings |
| `rules/service.py:224,284,381,424` | **legacy** `rules`, `rule_conditions`, `rule_actions` | pre-canonical model, still live |
| `rules/router.py:74` | legacy `rule_sets` | |

None of these map to a target table in `canonical_rating`.

### 2.4 Metadata Catalogue writes

`app/modules/catalog/service.py` is the **single generic CRUD choke point** for
all 14 catalogue entities *and* all 18 charging-metadata entities — 32 entities,
three functions:

| Function | Line | Operation |
| --- | --- | --- |
| `create()` | 68-83 | `db.add(model(**payload, created_by=actor_id))` + flush |
| `update()` | 86-99 | `setattr` loop + flush |
| `retire()` | 102-113 | soft delete: `status = RETIRED` |

Called from generated routes in `app/modules/catalog/router.py:_register()`
(lines 332-368) — `POST /{slug}`, `PATCH /{slug}/{id}`, `DELETE /{slug}/{id}`.

**One exception:** `DestinationPrefix` bypasses the generic service because it
has no `code`/`name`/`status`. Its writes are inline:

- `app/modules/catalog/router.py:594-603` — `create_prefix`, `db.add(obj)`
- `app/modules/catalog/router.py:611-613` — `delete_prefix`, **hard delete**

Also seeded at `app/seed.py:133` and `scripts/seed_assurance.py:103`.

`TimeBand` uses the generic path (registered at `catalog/router.py:114-115`),
plus seeds at `app/seed.py:97-107` and `scripts/seed_assurance.py:118-124`.

### 2.5 Vocabulary writes

`app/modules/rules/vocabulary/sync.py` → `sync_vocabulary()` upserts three
lookup tables (`rule_stage`, `rule_stacking_policy`, `rule_type`), idempotent and
additive — never deletes. Invoked from `app/seed.py:405`.

**Critical:** attributes, operators and action types — the three target
vocabulary tables — are **not stored in the database at all**. They live in
Python:

| Target table | Platform source | Kind |
| --- | --- | --- |
| `canonical_rating.rule_attribute` | `vocabulary/attributes.py` → `CANONICAL_ATTRIBUTES` (34 entries) | Python tuple |
| `canonical_rating.rule_operator` | `constants.py:117-130` → `Operator` StrEnum (13 members) | Python enum |
| `canonical_rating.rule_action_type` | `vocabulary/actions.py` → `CANONICAL_ACTIONS` (35 entries) | Python tuple |

There is no "write event" to intercept. These must be pushed by an explicit
**reconcile-on-startup** step, mirroring what `sync_vocabulary` already does for
the other three.

---

## 3. Source-of-truth entities

| Entity | Source of truth | Why |
| --- | --- | --- |
| Logical rule identity | `ra_rule.rule` (`rule_key`) | Stable, immutable; names never participate (`rule.py:63-65`) |
| Rule content | `ra_rule.rule_version` | Immutable per version; a result references a version id |
| Rule logic | `rule_condition_group` + `rule_condition` + `rule_action` + `rule_parameter` | Normalised; money as `Numeric(20,6)` |
| Behaviour identity | `rule_version.behaviour_hash` | Content fingerprint; drives idempotent re-import |
| Vocabulary | **Python modules**, DB is a projection | `sync.py:1-11` |
| Catalogue entities | `ra_rule.<entity>` keyed by `code` | `code` is the portable key across environments |
| Prefix→zone | `destination_zones` + `destination_prefixes` | Longest-prefix wins at enrichment |

---

## 4. Complete data flow

```
HTTP request
   │
   ├─ Depends(get_session)          app/core/database.py:154 — opens AsyncSession
   ├─ Depends(get_current_principal) app/core/deps.py:64      — resolves tenant_id
   │
   ├─ Router                         rules/api/router.py | catalog/router.py
   │     └─ Pydantic schema validation
   │
   ├─ Service / kernel
   │     ├─ rules:   ingest/kernel.py → adapter → CanonicalDraft
   │     │             ├─ resolver.ResolutionCache (catalogue code → id)
   │     │             ├─ reconcile.Decision (CREATE | NEW_VERSION | UNCHANGED)
   │     │             ├─ validation/registry.py
   │     │             └─ writer.write()  ── flush, no commit
   │     └─ catalog: catalog/service.py create/update/retire ── flush, no commit
   │
   └─ get_session() finally: session.commit()   ← THE single commit point
                             on exception: rollback
```

Everything in one request shares one transaction and one commit.

---

## 5. Target-table coverage assessment

| # | Target table | Platform source | In scope? |
| --- | --- | --- | --- |
| 1 | `rating_rule` | `ra_rule.rule` ⋈ `rule_version` | ✅ Rule Management |
| 2 | `rating_rule_condition` | `rule_condition` (+ `rule_condition_group` flattened) | ✅ Rule Management |
| 3 | `rating_rule_action` | `rule_action` (+ `rule_parameter` folded in) | ✅ Rule Management |
| 4 | `rule_action_type` | `vocabulary/actions.py` (Python) | ⚠️ No DB write event — needs reconcile |
| 5 | `rule_operator` | `constants.Operator` (Python) | ⚠️ No DB write event — needs reconcile |
| 6 | `rule_attribute` | `vocabulary/attributes.py` (Python) | ⚠️ No DB write event — needs reconcile |
| 7 | `subscriber_offer` | `rating.subscriber_products` | ❌ **Not Rule Management or Catalogue** |
| 8 | `subscriber_bundle_balance` | `rating.balance_buckets` | ❌ **Not Rule Management or Catalogue** |
| 9 | `destination_prefix` | `ra_rule.destination_prefixes` | ✅ Metadata Catalogue |
| 10 | `time_band` | `ra_rule.time_bands` | ✅ Metadata Catalogue |

### 5.1 Two targets fall outside the stated scope

**`subscriber_offer`.** The nearest platform table is
`rating.subscriber_products` (`cdr/models.py:285-315`). It has **no application
write path** — grep confirms it is only ever `SELECT`ed
(`cdr/enrich.py:171`, `cdr/bulk_enrich.py:240`). The only writer is
`scripts/seed_assurance.py:169`. There is no runtime event to mirror.

**`subscriber_bundle_balance`.** Maps to `rating.balance_buckets`
(`balances/models.py:47`). Written by the **rating execution engine**
(`balances/engine.py:199`, `source_system="RATING"`) during a rating run — i.e.
the execution plane, not Rule Management or the Metadata Catalogue.

Mirroring these is still possible, but it means hooking the CDR-enrichment and
rating-run paths — a materially different (and higher-risk) surface than the
request describes. **Recommendation: split into a phase 2.** Flagged, not
silently dropped.

### 5.2 Structural mismatches that force a documented policy

| Issue | Detail | Policy needed |
| --- | --- | --- |
| **Rule/version collapse** | Platform has `rule` + `rule_version` (1:N). Target has one `rating_rule`. | Mirror **only the current version** (`rule.current_version_id`), or one target row per version. Recommend: current version, since target has no version column. |
| **Condition nesting lost** | Platform: arbitrary-depth `rule_condition_group` tree. Target: flat `rating_rule_condition`. | Depth > 2 cannot be represented faithfully. Recommend: flatten with a `group_index`/`logic` column if one exists; otherwise **skip and log** rules with depth > 2 rather than silently corrupting their logic. |
| **Action parameters** | Platform: `rule_parameter` rows, `Numeric(20,6)`. Target: no parameter table. | Fold into a JSON/text column on `rating_rule_action` if one exists; else document as unmapped. |
| **Prefix→zone** | Platform: `destination_prefixes.zone_id` → `destination_zones`. Target: no zone table. | Denormalise `zone.code` onto `destination_prefix` if a column exists; else unmapped. |
| **ID types** | Platform: `String(36)` UUIDs everywhere (`canonical/base.py:1-11`). Target: schema tree shows `Sequences`, suggesting integer keys. | If integer, the mirror needs a **key-mapping table** or a deterministic UUID→int derivation. This is the single largest unknown. |
| **Tenancy** | Every platform row carries `tenant_id` under enforced RLS (`migrations/0014_tenancy_enforced.py`). | If the target has no `tenant_id`, mirroring more than one tenant collapses them. Recommend: mirror only `settings.default_tenant_id` until confirmed. |
| **Hard delete** | `delete_prefix` (`catalog/router.py:611`) hard-deletes. | The mirror must issue a matching DELETE, or prefixes drift permanently. |

---

## 6. Proposed design — minimal-diff, fail-open

### 6.1 Principles

1. **A second engine, never a second schema on the existing one.** Additive:
   nothing about the existing engine, session, or transaction changes.
2. **Hook at the choke points only.** Four call sites, not four hundred.
3. **Fail-open by default.** A mirror failure logs and returns; it never
   propagates into the request. Enforced by wrapping every mirror entry point in
   a blanket `except Exception: log.warning(...)`.
4. **Kill switch.** `MIRROR_ENABLED=false` by default. With it off the new code
   short-circuits on the first line — literally zero behavioural delta, which is
   what makes deliverable 5 provable rather than asserted.
5. **Out-of-band, after commit.** The mirror runs *after* `session.commit()`
   succeeds, so a mirrored row can never describe a transaction that rolled back.

### 6.1a Vocabulary self-healing

`rating_rule_condition` and `rating_rule_action` carry foreign keys onto
`rule_attribute`, `rule_operator` and `rule_action_type`. Those three are
projections of Python registries with no write event to observe, so they are
seeded at startup.

**Startup alone is not enough**, and the failure it leaves is permanent rather
than transient. If the lookup tables are truncated, or the mirror database is
recreated, or the service happened to boot before the mirror was reachable, then
every subsequent rule write fails with

```
ForeignKeyViolationError: insert or update on table "rating_rule_action"
violates foreign key constraint "fk_action_type"
DETAIL: Key (action_type)=(SET_PULSE) is not present in table "rule_action_type".
```

…and keeps failing until somebody restarts the process. Because the mirror is
fail-open, that shows up only as repeated log warnings — a mirror that has
silently stopped mirroring.

So `sync._write_rule` catches the integrity error, re-seeds the vocabulary via
`vocabulary.ensure()` and retries **once**. Once and not more: a second failure
means the rule is the problem, not the lookups, and further retries would only
delay the log line that says so. `vocabulary.ensure()` is also what makes the
startup reconcile optional rather than load-bearing — it runs lazily on the first
write of a process if boot-time seeding did not land.

### 6.2 Consistency approach (required documentation)

**Chosen: asynchronous, at-least-once, eventually consistent, fail-open.**

- The mirror write is **not** in the primary transaction. A 2-phase commit
  across two Postgres databases would put `rafms_rating_new`'s availability on
  the critical path of every rule save — unacceptable against the "zero
  behavioural change" constraint.
- Trigger point: FastAPI `BackgroundTasks`, enqueued only after the primary
  transaction commits.
- Failure handling: log at `WARNING` with the rule key / entity code, increment
  a counter, continue. No retry in phase 1.
- Reconciliation: a standalone `scripts/mirror_backfill.py` replays any entity
  from the primary DB into the mirror, idempotently (upsert on natural key). This
  is what makes at-least-once acceptable — a missed row is recoverable without a
  queue.
- Idempotency: every mirror write is `INSERT … ON CONFLICT (natural_key) DO
  UPDATE`, so a replay is safe.

**Rejected alternatives**, for the record:
- *Same-transaction 2PC* — couples availability, violates the constraint.
- *Outbox table in `rafms_db`* — strictly better durability, but requires a new
  table in the existing database, which the request forbids.
- *Postgres FDW / logical replication* — zero app code, but needs DBA access and
  cannot do the shape transformation (rule ⋈ version, group flattening).

### 6.3 Components to add (all new files)

```
app/modules/mirror/
  __init__.py
  config.py      # MIRROR_* settings, added to Settings as new optional fields
  engine.py      # second async engine + session factory, search_path=canonical_rating
  models.py      # SQLAlchemy models for the 10 target tables (separate Base!)
  mapping.py     # pure functions: platform ORM object → target row dict
  sync.py        # upsert helpers, ON CONFLICT DO UPDATE
  hooks.py       # the four entry points, each fail-open
scripts/
  mirror_backfill.py
tests/
  test_mirror_mapping.py     # pure mapping tests, no DB
  test_mirror_isolation.py   # asserts MIRROR_ENABLED=false ⇒ zero calls
```

**A separate declarative `Base`** is essential — reusing `app.core.database.Base`
would put the target tables into the platform's own Alembic autogenerate and
`create_all`, which *would* change existing behaviour.

### 6.4 Files to be modified (deliverable 6) — exactly four

| File | Change | Lines |
| --- | --- | --- |
| `app/core/config.py` | Add `mirror_enabled: bool = False` + `mirror_db_*` fields and a `mirror_database_url` computed field. Purely additive; every field defaulted. | ~15 |
| `app/modules/rules/ingest/writer.py` | At the end of `write()`, before `return`: record the write on the session for post-commit dispatch. **No behavioural change** — the recorder is a no-op when disabled. | ~3 |
| `app/modules/catalog/service.py` | Same recorder call at the end of `create()`, `update()`, `retire()`. | ~6 |
| `app/modules/catalog/router.py` | Same in `create_prefix` / `delete_prefix` (the two that bypass the service). | ~4 |
| `app/main.py` | Startup: reconcile the three vocabulary tables; shutdown: dispose the mirror engine. Both inside `if settings.mirror_enabled`. | ~6 |

Plus `app/modules/rules/lifecycle/service.py` (~3 lines) if status transitions
must be mirrored — recommended, else target rule statuses freeze at creation.

Total: **~37 lines across 5–6 existing files**, every one of them inside an
`if settings.mirror_enabled` guard or a call that returns immediately when
disabled.

### 6.5 Why this cannot break existing tests

- `MIRROR_ENABLED` defaults to `False`; `tests/conftest.py` sets no mirror env,
  so every existing test exercises the disabled path.
- The recorder's disabled path is a single boolean check and `return None`.
- No existing function signature, return type, exception, or transaction
  boundary changes.
- The AST guard in `tests/test_canonical_writer.py` checks for canonical ORM
  construction outside `writer.py`; the mirror uses its own `Base` and its own
  model classes, so it does not trip that guard. **This must be re-verified
  after implementation** — see §10.

---

## 7. Confirmation of no functional change (deliverable 5)

Provable, not asserted, on these grounds:

1. Every edit is guarded by `settings.mirror_enabled`, default `False`.
2. The mirror uses a separate engine, separate session factory, separate
   `MetaData`/`Base` — it cannot appear in the primary Alembic history or in
   `create_all`.
3. No read is ever performed from `rafms_rating_new` (write-only, per the
   requirement), so no platform decision can depend on its state.
4. The dispatch is post-commit and fail-open; the primary transaction's outcome
   is decided before the mirror is touched.
5. A dedicated test (`test_mirror_isolation.py`) asserts that with the flag off,
   the mirror engine is never constructed and no hook does work.

---

## 8. Insertion / update points — consolidated (deliverable 3)

> **Correction (post-deployment).** §2.3 originally dismissed the legacy write
> path as *"None of these map to a target table"*. That was wrong, and it is why
> the first rule created through the UI did not appear in the mirror.
>
> `RULE_COMPILE_SOURCE` defaults to `LEGACY`, and the rule-management API writes
> **`rating.rules` / `rule_conditions` / `rule_actions`** — not the canonical
> `ra_rule.*` model. In a default deployment the legacy model *is* the live
> estate; `writer.py` only ever sees imports and connector traffic.
>
> Both paths are now hooked. Which one feeds the mirror is chosen by
> `MIRROR_RULE_SOURCE` (default `AUTO`, following `RULE_COMPILE_SOURCE`), so one
> logical rule cannot land in the target twice under two `rule_id`s.


| # | Event | Hook site | Target tables affected |
| --- | --- | --- | --- |
| 0 | **Legacy rule created / edited / versioned / cloned / status-changed / deleted** | `rules/service.py` × 6 | `rating_rule`, `rating_rule_condition`, `rating_rule_action` |
| 1 | Rule created / new version | `rules/ingest/writer.py:write()` end | `rating_rule`, `rating_rule_condition`, `rating_rule_action` |
| 2 | Rule status change | `rules/lifecycle/service.py` `_walk` / `revert` / `activate` | `rating_rule` (status column) |
| 3 | Catalogue create | `catalog/service.py:create()` | `time_band` (when `model is TimeBand`) |
| 4 | Catalogue update | `catalog/service.py:update()` | `time_band` |
| 5 | Catalogue retire | `catalog/service.py:retire()` | `time_band` (status) |
| 6 | Prefix create | `catalog/router.py:create_prefix` | `destination_prefix` |
| 7 | Prefix delete | `catalog/router.py:delete_prefix` | `destination_prefix` (DELETE) |
| 8 | Startup reconcile | `app/main.py` lifespan | `rule_attribute`, `rule_operator`, `rule_action_type` |
| 9 | *(phase 2)* Subscriber product load | no runtime writer exists | `subscriber_offer` |
| 10 | *(phase 2)* Bundle consumption | `balances/engine.py` | `subscriber_bundle_balance` |

---

## 9. ~~Blocker~~ — resolved

The DDL for all ten target tables was supplied and the mapping is complete. The
open questions it settled:

* **ID types are mixed.** `rating_rule`, `rating_rule_condition` and
  `rating_rule_action` use `varchar(50)` keys — the platform's 36-char UUIDs fit
  directly, so no key-mapping table is needed. `destination_prefix`, `time_band`,
  `subscriber_offer` and `subscriber_bundle_balance` use `GENERATED ALWAYS AS
  IDENTITY` bigints, which the mirror never supplies; those four are written
  delete-then-insert on their natural key.
* **No `tenant_id` anywhere in the target.** Recorded as a cross-cutting loss
  (mapping doc §11): one target database can faithfully hold one tenant.
* **Every CHECK constraint is enforced ahead of the write**, in
  `valuemaps.py`, so a violation surfaces as a skipped-and-logged row rather
  than a failed statement.

### Original blocker text (kept for the record)

Deliverable 2 (column-by-column mapping) and deliverable 7 (implementation)
cannot be completed without the **column definitions of the 10 target tables**.
The column list referenced in the request did not arrive; only the schema-tree
screenshot did.

The mapping requirement is explicit that missing values must be *documented, not
invented* — which equally forbids inventing the column names themselves. Writing
`INSERT`s against guessed columns would fail on the first execution and would
produce a mapping document that is fiction.

**Needed — any one of these:**

1. `psql -d rafms_rating_new -c '\d+ canonical_rating.<table>'` for the 10 tables, or
2. the `CREATE TABLE` DDL, or
3. read-only credentials for `rafms_rating_new`, so the schema can be introspected directly.

Item 3 is the most reliable: it also settles the ID-type question (§5.2), the
`tenant_id` question, and the nullability of every column in one pass.

On receipt, §5.2's open policies get concrete answers and implementation
proceeds immediately against the design in §6, which does not otherwise depend
on the column list.

---

## 10. Post-implementation verification plan

1. `pytest` — all 32 test modules green, unchanged.
2. `tests/test_canonical_writer.py` AST guard still passes.
3. `MIRROR_ENABLED=false`: assert mirror engine never instantiated.
4. `MIRROR_ENABLED=true` against a throwaway `rafms_rating_new`: create a rule
   via the API, assert the primary DB row is byte-identical to the pre-change
   baseline and the mirror row matches the mapping.
5. Mirror DB **unreachable** with the flag on: assert the API returns 201 with an
   identical body and only a `WARNING` is logged.
