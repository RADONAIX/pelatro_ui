# Rule Management — Production-Grade Canonical Model & Ingestion Plan

> Scope: `ra_rating_backend` rule control plane + `/rating/rules/*` and
> `/rating/metadata/*` UI.
> Goal: **one canonical rule model covering prepaid *and* postpaid in a single
> engine, and one write path into it — so that a rule hand-authored in the wizard,
> uploaded as CSV/XLSX/JSON/XML/fixed-width, or pulled from Ericsson / Oracle BRM /
> Huawei / Amdocs lands in exactly the same normalized, typed, tenant-scoped,
> lineage-traced set of PostgreSQL tables.**
>
> Non-goal: changing `ra_backend`, the ClickHouse execution-plane contract, or any
> screen outside `/rating/rules/*`, `/rating/metadata/*`, `/rating/data-sources`.

---

## Part A — Analysis

### A.0 The two problems

**Problem 1 — three write paths, wrong shape.** Manual API, file import and
connector import each construct ORM objects themselves, against a *flattened*
model (`rating.rules` + `rule_conditions` + `rule_actions`, JSONB payloads) that
is not the canonical model. Anything a vendor sends that does not fit the flat
shape is dropped into `rules.attributes` JSONB or lost.

**Problem 2 — the vocabulary is voice/data-rating only.** The spec's §5 is the
larger finding. Today's `constants.py` models 11 rule types, 11 execution stages
and 15 actions — all *usage rating*. There is:

- no `charging_mode` on a rule at all (only an `account_type` condition attribute);
- **no prepaid charging semantics** — no balance deduction, bucket selection,
  reservation, insufficient/zero/negative-balance handling, partial-session
  charging, consumption order as rules;
- **no postpaid semantics whatsoever** — no rental, recurring, one-time,
  proration, credit limit, invoice component, invoice tax, late fee, aggregation,
  invoice rounding;
- a **single global `STAGE_ORDER` tuple** ([constants.py:64](ra_rating_backend/app/modules/rules/constants.py#L64)),
  which cannot express "prepaid runs the balance stages, postpaid runs the
  billing stages, both run the common stages".

So "do not keep prepaid and postpaid rules in separate engines" is currently true
by accident — there is only one engine because only half the domain is modelled.
Making it true *by design* is the substance of this plan.

### A.1 The design answer to "one engine, not two"

The stage registry becomes mode-scoped, and the pipeline is derived, not hard-coded:

```
rule_stage.applies_to ∈ {COMMON, PREPAID, POSTPAID}

pipeline(PREPAID)  = stages WHERE applies_to IN (COMMON, PREPAID)  ORDER BY execution_order
pipeline(POSTPAID) = stages WHERE applies_to IN (COMMON, POSTPAID) ORDER BY execution_order
pipeline(BOTH)     = COMMON stages only
```

One rule table, one condition grammar, one action grammar, one selection
algorithm, one compiler. What differs between prepaid and postpaid is *which
stages are in the pipeline* — which is data in `rule_stage`, not a branch in code.
That is the whole of the "single canonical model" requirement, and it costs one
column.

### A.2 Spec reconciliation — conflicts I had to resolve

| # | Spec says | Code/plan said | Resolution |
|---|---|---|---|
| C1 | `charging_mode` values `PREPAID / POSTPAID / BOTH` | earlier draft of this plan used `HYBRID`; `account_type` attribute uses `HYBRID` | **`BOTH`** on rules (spec wins). `account_type` keeps `HYBRID` — it describes a *subscriber*, not a rule, and they are genuinely different things |
| C2 | Actions `SET_MINIMUM`, `SET_MAXIMUM`, `ZERO_RATE` | `SET_MINIMUM_CHARGE`, `SET_MAXIMUM_CHARGE`, `SET_ZERO_CHARGE` | Keep the explicit `_CHARGE` names canonical (a minimum *charge* and a minimum *quantity* both exist and `SET_MINIMUM` is ambiguous); accept the spec's short names as **ingest aliases** (§B.3.4) |
| C3 | Attributes `product_id`, `offer_id` | attributes `product`, `offer` (values are *codes*, not ids) | Keep code-valued `product`/`offer` — codes survive an environment move, ids do not. `product_id`/`offer_id` accepted as aliases; the resolved id is stored alongside in `resolved_ref_id`, so both readings are satisfied |
| C4 | Attribute `roaming_flag` | attribute `roaming` | Alias |
| C5 | Step 4 lets the author set **Specificity** | `specificity` is computed from conditions ([service.py:49](ra_rating_backend/app/modules/rules/service.py#L49)) | Shown **read-only with its derivation** in Step 4. An author-editable specificity plus an author-editable priority gives two knobs for one job and guarantees drift. Flagged in §E |
| C6 | Catalogue column **Snapshot** | no link from a rule to the snapshot that published it | Add `rule_version.published_snapshot_id → rating.rule_snapshots` |
| C7 | `USAGE_RATING` (postpaid) and `BASE_TARIFF` (common) | one `BASE_TARIFF` | `USAGE_RATING` seeded as a postpaid-facing **alias rule type** onto the same `BASE_CHARGE` stage — the operator's word, our single implementation |
| C8 | "Billing cycle" listed as a *rule* and as *metadata* | neither exists | Both: `ra_catalog.billing_cycle` (metadata) + a `BILLING_CYCLE_ASSIGNMENT` rule type that assigns one |

### A.3 What the spec implies but does not state (and needs building)

| # | Implication | Why it matters |
|---|---|---|
| I1 | `RESERVE_BALANCE` / `RELEASE_RESERVATION` need a **reservation store** keyed by session | Reservation is stateful and session-scoped. No such table exists. Added as `ra_subscriber.balance_reservation` (§C.6) |
| I2 | Prepaid reservation rules are **online** (session-time, sub-100 ms); rental/invoice rules are **batch** (bill-run) | One rule *model*, two runtimes. `rule_version.execution_mode ∈ {ONLINE, OFFLINE, BOTH}` makes that explicit and lets the compiler emit two snapshots from one rule set. Without it, an online engine loads 4,000 invoice rules it can never fire |
| I3 | `AGGREGATE_USAGE` needs an aggregation **window definition** | Added `ra_catalog.usage_aggregation_profile` |
| I4 | `ADD_LATE_FEE` needs fee terms | Added `ra_catalog.late_fee_profile` |
| I5 | `charging_mode = BOTH` must be constrained | A `BOTH` rule containing `DEDUCT_BALANCE` is nonsense. Validator: a rule's actions must all belong to stages in `pipeline(its charging_mode)` |
| I6 | Wizard Step 5 must show **balance impact or invoice impact** | Today's `simulation.simulate()` returns rating output only. Needs the prepaid tail and a postpaid invoice projection |
| I7 | §11 adds metadata screens for Charging Units, Pulse Profiles, Min/Max Profiles, Reservation Policies | None exist. Four new catalogue tables (§C.5) |
| I8 | Catalogue column **Validation** = Pass/Warning/Error | `rules.last_validation` is a summary JSONB blob; cannot answer "which check failed" | Persisted `rule_validation_issue` rows (§C.4) |

### A.4 Gap analysis — canonical tables

| Canonical table (spec §8–§10) | Today | Gap |
|---|---|---|
| `ra_rule.rule` (`tenant_id`, `rule_key`, `charging_mode`, `rule_type_id`, `rule_stage_id`, `source_system_id`, `current_version_id`) | `rating.rules` — logical + version fused in one row; no `tenant_id`; no `charging_mode`; type/stage are enum strings not FKs; `source_system` free text; no `current_version_id` | **Critical** |
| `ra_rule.rule_version` | same row as `rules`; `stacking_policy`/`conflict_group` are strings; no `fallback_policy`, `stop_processing`, `execution_mode`, snapshot link. `specificity` exists (good) | **Critical** |
| `ra_rule.rule_type`, `ra_rule.rule_stage` | `StrEnum` only; single global stage order; no mode scoping; not joinable for reporting | **Critical** (blocks §A.1) |
| `ra_rule.rule_condition_group` | collapsed to `group_index` int + one rule-level `condition_logic`; no nesting, labels or group negation | High |
| `ra_rule.rule_condition` (`comparison_value`, `comparison_value_type`, `sequence_number`, `negated_flag`) | `values` JSONB, `sequence`, `negate`; no declared value type → engine re-infers per CDR | High |
| `ra_rule.rule_action` (`target_attribute`, `action_value`, `action_value_type`, `execution_sequence`) | `action_type` + opaque `params` JSONB | High |
| `ra_rule.rule_parameter` | **absent**; params live in JSONB and **money is parsed to `float`** ([mapper.py:77](ra_rating_backend/app/modules/imports/mapper.py#L77)) | **Critical** |
| `ra_rule.rule_dependency` | absent; no cycle detection | High |
| `ra_rule.rule_conflict_group` | free-text column; no resolution strategy | Medium |
| `ra_rule.rule_stacking_policy` | enum string | Medium |
| `ra_rule.rule_fallback` | absent; the 4-level fallback is implicit in the specificity score | High |
| `ra_rule.rule_set` / `rule_set_member` | `rule_sets` exists; membership is one FK → a rule cannot be in two sets | Medium |
| `ra_catalog` prepaid (balance_type, balance_bucket, balance_priority, charging_profile, ocs_profile) | `balance_buckets`, `balance_ledger`, `usage_counters` only — no type registry, no priorities, no profiles | High |
| `ra_bundle.*` | `bundle_definitions` only — unversioned, single-bucket, no consumption rules or priorities | High |
| `ra_subscriber` prepaid | `subscriber_products` only | High |
| `ra_catalog` postpaid (billing_cycle, invoice_component, recurring_charge, one_time_charge, proration_profile, credit_limit_profile) | **none** | **Critical** |
| `ra_subscriber` postpaid (billing_account, account_product_history, billing_cycle_history, credit_profile_history) | **none** | **Critical** |
| §11 metadata (charging_unit, pulse_profile, min/max profile, reservation_policy) | none | Medium |

### A.5 Defects to fix while we are in here

| # | Defect | Where | Consequence |
|---|---|---|---|
| D1 | Money parsed and stored as `float` | `mapper.py::_parse_number`, action `params` | Cent-level drift; unfixable after the fact |
| D2 | `rule_key` slugged from the rule **name** | `service.py::derive_rule_key` | Two vendor rules named "Peak" collide; a rename forks a phantom logical rule |
| D3 | No stable external identity | imports/connectors | Re-import cannot match a vendor rule except by derived key |
| D4 | `latest_version()` called per row inside the commit loop | `imports/service.py:213`, `connectors/service.py:259` | O(n) round trips; a 40k-row dump takes hours |
| D5 | Connector import writes directly, no preview | `connectors/service.py::run_import` | A nightly delta cannot be inspected before it changes live pricing |
| D6 | One transaction, no checkpointing | both importers | A failure at row 39,000 loses the run |
| D7 | `import_fingerprint` hidden in `rules.attributes` | `connectors/service.py:270` | Change detection is a JSONB scan |
| D8 | FULL-mode reconciliation silently retires drafts only | `connectors/service.py:342` | A rule the vendor deleted keeps rating, invisibly |
| D9 | No tenancy anywhere | — | Cannot host two operators; no RLS blast radius |
| D10 | `last_validation` is a summary | `rules.last_validation` | Cannot answer "which rules failed which check" — blocks the catalogue's Validation column |
| D11 | `STAGE_ORDER` is one global tuple | `constants.py:64` | Cannot express mode-scoped pipelines (§A.1) |
| D12 | `SET_MAXIMUM_CHARGE` runs at `BASE_CHARGE` | `constants.py:390` | A cap evaluated in the same pass as the rate it caps is order-dependent; needs its own stage after discounts |

---

## Part B — Target design

### B.1 One kernel, many mouths

```
   MANUAL WIZARD        FILE UPLOAD                  CONNECTOR
   (6 steps)        csv xlsx json xml fixed     Ericsson / BRM / Huawei
       │                    │                    (API, DB, SFTP, file)
       ▼                    ▼                            ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ SOURCE ADAPTER LAYER   rules/ingest/adapters/*                    │
 │   sole contract:  Iterable[SourceRecord]                          │
 └───────────────────────────┬───────────────────────────────────────┘
                             ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ RULE INGESTION KERNEL   rules/ingest/kernel.py                    │
 │  1 STAGE      raw record + content hash        → ingestion_record │
 │  2 NORMALIZE  profile-driven mapping           → CanonicalDraft   │
 │  3 TYPE       Decimal money, units, currency, dates, enums        │
 │  4 RESOLVE    codes → catalog ids (batched, cached per run)       │
 │  5 VALIDATE   structural · semantic · cross-rule · mode-coherence │
 │  6 RECONCILE  new / changed / unchanged / withdrawn (by ext ref)  │
 │  7 COMMIT     normalized write, chunked, checkpointed, audited    │
 │  8 PROJECT    specificity · behaviour_hash · canonical_json       │
 └───────────────────────────┬───────────────────────────────────────┘
                             ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ CANONICAL STORE   ra_rule.*  (truth)                              │
 │   ra_catalog / ra_bundle / ra_subscriber  (prepaid + postpaid)     │
 └───────────────────────────┬───────────────────────────────────────┘
                             ▼
     COMPILER → rating.executable_rules   (ONLINE snapshot | OFFLINE snapshot)
```

**Enforcement:** after phase R3, the `Rule`, `RuleVersion`, `RuleCondition`,
`RuleAction`, `RuleParameter` ORM classes may only be instantiated inside
`ingest/writer.py`; a CI test AST-walks the tree and fails otherwise. Manual
authoring is not a special case — the wizard's payload becomes a `CanonicalDraft`
and runs steps 3–8 identically. Documentation does not keep three write paths
converged; a test does.

### B.2 Code layout

```
app/modules/rules/
  models/      rule.py lookups.py logic.py graph.py sets.py lineage.py
  vocabulary/  modes.py stages.py types.py actions.py attributes.py aliases.py
  canonical/   draft.py valuetypes.py units.py fingerprint.py specificity.py
  ingest/      kernel.py writer.py resolver.py reconcile.py profiles.py
               adapters/{manual,file,ericsson,oracle_brm,huawei,amdocs,generic_xml}.py
  validation/  structural.py semantic.py crossrule.py modes.py registry.py
  service.py   router.py
app/modules/prepaid/    models.py service.py router.py   # ra_catalog + ra_bundle + ra_subscriber
app/modules/postpaid/   models.py service.py router.py
```

`vocabulary/` replaces the monolithic `constants.py`, which becomes a
deprecation shim re-exporting from it so nothing breaks mid-migration. The
vocabulary modules are also the **seed source** for the `rule_type` / `rule_stage`
/ `rule_stacking_policy` tables, so the Python registry and the database rows can
never disagree — a migration test asserts they match.

### B.3 The rule vocabulary (spec §5, fully enumerated)

#### B.3.1 Charging modes

```python
class ChargingMode(StrEnum):
    PREPAID = "PREPAID"; POSTPAID = "POSTPAID"; BOTH = "BOTH"
```

`rule.charging_mode` is **required**. `BOTH` means "the same rule text is correct
for either" — validated per I5 to contain only `COMMON`-stage actions.

#### B.3.2 Stages — mode-scoped, gap-numbered

Gaps of 10 so a future stage inserts without renumbering (which would silently
reorder a live pipeline).

| Order | Stage | Applies to | Status |
|---|---|---|---|
| 10 | `ELIGIBILITY` | COMMON | **new** |
| 20 | `SERVICE_CLASSIFICATION` | COMMON | **new** |
| 30 | `DESTINATION_CLASSIFICATION` | COMMON | **new** |
| 40 | `TIME_BAND` | COMMON | **new** |
| 50 | `QUANTITY` | COMMON | exists |
| 60 | `TARIFF_SELECTION` | COMMON | exists |
| 70 | `MINIMUM_CHARGE` | COMMON | exists |
| 80 | `PULSE` | COMMON | exists |
| 90 | `BASE_CHARGE` | COMMON | exists |
| 100 | `BUNDLE` | COMMON | exists |
| 110 | `PROMOTION` | COMMON | exists |
| 120 | `DISCOUNT` | COMMON | exists |
| 130 | `SURCHARGE` | COMMON | exists |
| 140 | `MAXIMUM_CHARGE` | COMMON | **new** (fixes D12) |
| 150 | `TAX` | COMMON | exists |
| 160 | `ROUNDING` | COMMON | exists |
| 200 | `BALANCE_SELECTION` | PREPAID | **new** |
| 210 | `BALANCE_RESERVATION` | PREPAID | **new** |
| 220 | `BALANCE_DEDUCTION` | PREPAID | **new** |
| 230 | `BALANCE_EXCEPTION` | PREPAID | **new** |
| 240 | `SESSION_CONTROL` | PREPAID | **new** |
| 300 | `BILLING_CYCLE_ASSIGNMENT` | POSTPAID | **new** |
| 310 | `USAGE_AGGREGATION` | POSTPAID | **new** |
| 320 | `RECURRING_CHARGE` | POSTPAID | **new** |
| 330 | `ONE_TIME_CHARGE` | POSTPAID | **new** |
| 340 | `PRORATION` | POSTPAID | **new** |
| 350 | `CREDIT_CHECK` | POSTPAID | **new** |
| 360 | `INVOICE_COMPONENT` | POSTPAID | **new** |
| 370 | `INVOICE_TAX` | POSTPAID | **new** |
| 380 | `LATE_FEE` | POSTPAID | **new** |
| 390 | `INVOICE_ROUNDING` | POSTPAID | **new** |

Existing relative order among the 11 current stages is preserved exactly, so
Phase R1 changes no rating outcome. `MAXIMUM_CHARGE` moving out of `BASE_CHARGE`
is the one deliberate behaviour change — called out, tested, and shipped with a
before/after parity report on the seeded estate.

#### B.3.3 Rule types (spec §5's three categories)

**Common (13)** — `PRODUCT_ELIGIBILITY`→ELIGIBILITY · `SERVICE_CLASSIFICATION`→SERVICE_CLASSIFICATION ·
`DESTINATION_CLASSIFICATION`→DESTINATION_CLASSIFICATION · `TIME_BAND`→TIME_BAND ·
`MINIMUM_QUANTITY`→QUANTITY · `TARIFF_SELECTION`→TARIFF_SELECTION ·
`BASE_TARIFF`→BASE_CHARGE · `ZERO_RATE`→BASE_CHARGE · `PULSE`→PULSE ·
`MINIMUM_CHARGE`→MINIMUM_CHARGE · `MAXIMUM_CHARGE`→MAXIMUM_CHARGE ·
`BUNDLE`→BUNDLE · `PROMOTION`→PROMOTION · `DISCOUNT`→DISCOUNT ·
`SURCHARGE`→SURCHARGE · `TAX`→TAX · `ROUNDING`→ROUNDING

**Prepaid (10)** — `BALANCE_DEDUCTION`→BALANCE_DEDUCTION ·
`BALANCE_BUCKET_PRIORITY`→BALANCE_SELECTION ·
`BALANCE_PREFERENCE` (main vs promotional)→BALANCE_SELECTION ·
`INSUFFICIENT_BALANCE`→BALANCE_EXCEPTION · `ZERO_BALANCE`→BALANCE_EXCEPTION ·
`NEGATIVE_BALANCE`→BALANCE_EXCEPTION · `PARTIAL_SESSION_CHARGING`→BALANCE_EXCEPTION ·
`SESSION_RESERVATION`→BALANCE_RESERVATION · `RESERVATION_RELEASE`→BALANCE_RESERVATION ·
`BUNDLE_CONSUMPTION_ORDER`→BUNDLE

**Postpaid (12)** — `MONTHLY_RENTAL`→RECURRING_CHARGE ·
`RECURRING_CHARGE`→RECURRING_CHARGE · `ONE_TIME_CHARGE`→ONE_TIME_CHARGE ·
`USAGE_RATING`→BASE_CHARGE (alias of BASE_TARIFF, C7) ·
`USAGE_AGGREGATION`→USAGE_AGGREGATION ·
`BILLING_CYCLE_ASSIGNMENT`→BILLING_CYCLE_ASSIGNMENT · `PRORATION`→PRORATION ·
`CREDIT_LIMIT`→CREDIT_CHECK · `INVOICE_COMPONENT`→INVOICE_COMPONENT ·
`INVOICE_TAX`→INVOICE_TAX · `LATE_FEE`→LATE_FEE ·
`INVOICE_ROUNDING`→INVOICE_ROUNDING

11 existing types keep their codes and stages. Every type carries
`required_action_types` (already the validator's contract,
[constants.py:463](ra_rating_backend/app/modules/rules/constants.py#L463)) and a
`charging_mode` applicability, both as table columns.

#### B.3.4 Actions

Existing 15 keep their codes and specs. **New prepaid (7):**

| Action | Stage | Parameters |
|---|---|---|
| `SELECT_BALANCE_BUCKET` | BALANCE_SELECTION | `balance_type`†, `balance_bucket`†, `consume_order`, `fallback_balance_type` |
| `DEDUCT_BALANCE` | BALANCE_DEDUCTION | `balance_type`†, `amount_source` (CHARGE\|QUANTITY), `unit`, `allow_partial` |
| `RESERVE_BALANCE` | BALANCE_RESERVATION | `balance_type`†, `quota_amount`, `unit`, `validity_seconds`, `ocs_profile`† |
| `RELEASE_RESERVATION` | BALANCE_RESERVATION | `release_mode` (UNUSED\|ALL), `grace_seconds` |
| `ALLOW_PARTIAL_USAGE` | BALANCE_EXCEPTION | `min_chargeable_quantity`, `unit`, `round_mode` |
| `ALLOW_NEGATIVE_BALANCE` | BALANCE_EXCEPTION | `limit_amount` (MONEY), `currency` |
| `STOP_SERVICE` | SESSION_CONTROL | `action` (TERMINATE\|REDIRECT\|THROTTLE), `redirect_target`, `notify` |

**New postpaid (7):**

| Action | Stage | Parameters |
|---|---|---|
| `ASSIGN_BILLING_CYCLE` | BILLING_CYCLE_ASSIGNMENT | `billing_cycle`† |
| `AGGREGATE_USAGE` | USAGE_AGGREGATION | `aggregation_profile`†, `dimension`, `window` |
| `ADD_RECURRING_CHARGE` | RECURRING_CHARGE | `recurring_charge`†, `amount` (MONEY), `currency`, `advance_flag`, `invoice_component`† |
| `ADD_ONE_TIME_CHARGE` | ONE_TIME_CHARGE | `one_time_charge`†, `amount` (MONEY), `currency`, `trigger_event` |
| `APPLY_PRORATION` | PRORATION | `proration_profile`†, `method`, `basis` |
| `CHECK_CREDIT_LIMIT` | CREDIT_CHECK | `credit_limit_profile`†, `breach_action`, `warning_threshold_pct` |
| `ADD_INVOICE_COMPONENT` | INVOICE_COMPONENT | `invoice_component`†, `amount` (MONEY), `currency`, `sign` |
| `ADD_USAGE_CHARGE` | INVOICE_COMPONENT | `invoice_component`†, `amount_source` (RATED_CHARGE), `currency` |
| `ADD_LATE_FEE` | LATE_FEE | `late_fee_profile`†, `amount`, `percentage`, `grace_days` |

† = `REFERENCE` type, resolved against the new `ra_catalog`/`ra_bundle` tables.
`INVOICE_TAX` and `INVOICE_ROUNDING` reuse `APPLY_TAX` / `APPLY_ROUNDING` —
same semantics at a different stage, so no new action is warranted.

**Ingest aliases** (`vocabulary/aliases.py`, applied in kernel step 2, recorded in
lineage so the original token is never lost):

```
SET_MINIMUM → SET_MINIMUM_CHARGE      SET_MAXIMUM → SET_MAXIMUM_CHARGE
ZERO_RATE   → SET_ZERO_CHARGE         product_id  → product
offer_id    → offer                   roaming_flag → roaming
tariff_plan_id → tariff_plan          USAGE_RATING → BASE_TARIFF (type)
```

#### B.3.5 Condition attributes

Existing 24 keep their keys. **New:**

| Attribute | Type | Specificity | Notes |
|---|---|---|---|
| `charging_mode` | ENUM(PREPAID,POSTPAID,BOTH) | 30 | Spec's first-listed attribute |
| `balance_type` | REFERENCE `balance-types` | 35 | Prepaid |
| `balance_amount` | NUMBER | 15 | Prepaid — "if main balance < 5" |
| `bundle` | REFERENCE `bundles` | 40 | |
| `billing_cycle` | REFERENCE `billing-cycles` | 30 | Postpaid |
| `invoice_type` | ENUM(REGULAR,FINAL,INTERIM,CREDIT_NOTE) | 25 | Postpaid |
| `account_status` | ENUM(ACTIVE,SUSPENDED,BARRED,TERMINATED) | 25 | |
| `contract_type` | ENUM(PREPAID,POSTPAID,SIM_ONLY,BUNDLED) | 20 | |
| `session_type` | ENUM(INITIAL,UPDATE,TERMINATE) | 20 | Prepaid online only |

The spec's two examples both validate against this registry unchanged:

```
Charging Mode = PREPAID  AND Product = PREPAID_A  AND Service = VOICE
  AND Destination Zone = LOCAL_ONNET  AND Time Band = PEAK  AND Roaming = NO
Charging Mode = POSTPAID AND Product = POSTPAID_A AND Service = VOICE
  AND Billing Cycle = MONTHLY  AND Destination Zone = LOCAL_OFFNET
```

`GET /api/rating/meta/*` already generates the builder from this registry, so the
wizard gains all of it without any UI-side enumeration.

---

## Part C — Canonical DDL

Four new schemas beside the existing `rating`: `ra_rule`, `ra_catalog`,
`ra_bundle`, `ra_subscriber`. `rating` keeps CDR, run, result, exception and
executable-snapshot tables — execution plane, different lifecycle and backup
profile; a schema boundary is the cheapest way to say so.

Conventions everywhere: `tenant_id uuid NOT NULL` + RLS
(`tenant_id = current_setting('ra.tenant_id')::uuid`); UUID surrogate PKs; business
keys unique **per tenant**; `created_at/updated_at timestamptz`,
`created_by/updated_by uuid`; money `numeric(20,6)` + explicit `currency_code
char(3)`. **No floats in the rule path.**

### C.1 Lookups

```sql
CREATE TABLE ra_rule.rule_stage (
  rule_stage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  code varchar(48) NOT NULL, name varchar(128) NOT NULL,
  applies_to varchar(16) NOT NULL DEFAULT 'COMMON',  -- COMMON | PREPAID | POSTPAID
  execution_order integer NOT NULL,                  -- gap-numbered, §B.3.2
  is_stateful boolean NOT NULL DEFAULT false,         -- needs balance/counter state
  UNIQUE (tenant_id, code), UNIQUE (tenant_id, execution_order)
);

CREATE TABLE ra_rule.rule_type (
  rule_type_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  code varchar(48) NOT NULL, name varchar(128) NOT NULL,
  rule_category varchar(16) NOT NULL,     -- COMMON | PREPAID | POSTPAID  (spec §5)
  rule_stage_id uuid NOT NULL REFERENCES ra_rule.rule_stage,
  charging_mode varchar(16) NOT NULL DEFAULT 'BOTH',   -- applicability
  required_action_types text[] NOT NULL DEFAULT '{}',
  alias_of varchar(48),                   -- USAGE_RATING → BASE_TARIFF (C7)
  is_system boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, code)
);

CREATE TABLE ra_rule.rule_stacking_policy (
  stacking_policy_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL, code varchar(32) NOT NULL, name varchar(128) NOT NULL,
  allows_multiple boolean NOT NULL, overrides_lower boolean NOT NULL,
  UNIQUE (tenant_id, code)
);

CREATE TABLE ra_rule.rule_conflict_group (
  conflict_group_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL, code varchar(64) NOT NULL, name varchar(255) NOT NULL,
  resolution_strategy varchar(32) NOT NULL DEFAULT 'HIGHEST_SPECIFICITY',
     -- HIGHEST_SPECIFICITY | HIGHEST_PRIORITY | FIRST_MATCH | ERROR
  owner varchar(255),
  UNIQUE (tenant_id, code)
);
```

### C.2 Rule + version

```sql
CREATE TABLE ra_rule.rule (
  rule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_key varchar(96) NOT NULL,          -- stable, immutable logical identity
  rule_name varchar(255) NOT NULL,
  description text NOT NULL DEFAULT '',
  charging_mode varchar(16) NOT NULL,     -- PREPAID | POSTPAID | BOTH   (C1)
  rule_type_id uuid NOT NULL REFERENCES ra_rule.rule_type,
  rule_stage_id uuid NOT NULL REFERENCES ra_rule.rule_stage,  -- denormalized from type
  service_type varchar(16) NOT NULL,
  source_system_id uuid REFERENCES rating.source_systems(id), -- NULL = MANUAL
  external_ref varchar(255),              -- the vendor's own primary key
  current_version_id uuid,                -- deferred FK (circular)
  status varchar(16) NOT NULL DEFAULT 'DRAFT',
  owner varchar(255),
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid, updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rule_key),
  UNIQUE (tenant_id, source_system_id, external_ref)     -- D3: real idempotency key
);

CREATE TABLE ra_rule.rule_version (
  rule_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  version_number integer NOT NULL,
  product_id     uuid REFERENCES rating.products(id),
  offer_id       uuid REFERENCES rating.offers(id),
  tariff_plan_id uuid REFERENCES rating.tariff_plans(id),
  -- Step 4 "Behaviour" ------------------------------------------------------
  priority integer NOT NULL DEFAULT 100,
  specificity_score integer NOT NULL DEFAULT 0,   -- COMPUTED, never author-set (C5)
  stacking_policy_id uuid NOT NULL REFERENCES ra_rule.rule_stacking_policy,
  conflict_group_id  uuid REFERENCES ra_rule.rule_conflict_group,
  fallback_policy varchar(24) NOT NULL DEFAULT 'FALLBACK_CHAIN',
     -- FALLBACK_CHAIN | NEXT_MATCH | GLOBAL_DEFAULT | ERROR | NONE
  stop_processing boolean NOT NULL DEFAULT false,   -- halt this stage after a match
  execution_mode varchar(8) NOT NULL DEFAULT 'BOTH',-- ONLINE | OFFLINE | BOTH  (I2)
  condition_logic varchar(4) NOT NULL DEFAULT 'AND',
  -- Validity ---------------------------------------------------------------
  effective_from date NOT NULL, effective_to date,
  currency_code char(3),
  -- Lifecycle --------------------------------------------------------------
  status varchar(16) NOT NULL DEFAULT 'DRAFT',
  change_reason text NOT NULL DEFAULT '',
  supersedes_id uuid REFERENCES ra_rule.rule_version,
  published_snapshot_id uuid REFERENCES rating.rule_snapshots(id),  -- C6
  validation_state varchar(16) NOT NULL DEFAULT 'UNKNOWN',  -- PASS|WARNING|ERROR|UNKNOWN
  behaviour_hash char(64) NOT NULL,     -- D7: indexed change detection
  canonical_json jsonb NOT NULL,        -- §B.1 step 8 projection
  submitted_by uuid, submitted_at timestamptz,
  approved_by uuid,  approved_at timestamptz,
  retired_at timestamptz,
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, rule_id, version_number),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX ix_rule_version_selection ON ra_rule.rule_version
  (tenant_id, status, execution_mode, effective_from, effective_to)
  INCLUDE (rule_id, priority, specificity_score);
CREATE INDEX ix_rule_version_hash ON ra_rule.rule_version (tenant_id, behaviour_hash);
CREATE INDEX ix_rule_version_snapshot ON ra_rule.rule_version (published_snapshot_id);

-- Two live prices for the same event is the most expensive incident class there is.
ALTER TABLE ra_rule.rule_version ADD CONSTRAINT ex_rule_version_active_window
  EXCLUDE USING gist (
    rule_id WITH =,
    daterange(effective_from, COALESCE(effective_to,'infinity'::date), '[]') WITH &&
  ) WHERE (status IN ('ACTIVE','PUBLISHED'));
```

### C.3 Conditions, actions, parameters

```sql
CREATE TABLE ra_rule.rule_condition_group (
  condition_group_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  parent_group_id uuid REFERENCES ra_rule.rule_condition_group ON DELETE CASCADE,
  group_logic varchar(4) NOT NULL DEFAULT 'AND',
  negated_flag boolean NOT NULL DEFAULT false,
  sequence_number smallint NOT NULL,
  label varchar(128) NOT NULL DEFAULT '',
  CHECK (parent_group_id IS DISTINCT FROM condition_group_id)
);
-- Depth capped at 4 by the validator, not the schema: deeper is unreadable to an
-- author and pathological for the compiler's key expansion.

CREATE TABLE ra_rule.rule_condition (
  rule_condition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  condition_group_id uuid NOT NULL REFERENCES ra_rule.rule_condition_group ON DELETE CASCADE,
  attribute_name varchar(64) NOT NULL,
  operator_code varchar(24) NOT NULL,
  comparison_value text NOT NULL,
  comparison_value_type varchar(16) NOT NULL,
     -- STRING|NUMBER|MONEY|BOOLEAN|ENUM|REFERENCE|DATE|DATETIME|LIST|RANGE
  comparison_values jsonb NOT NULL DEFAULT '[]',   -- typed elements for IN/BETWEEN
  comparison_value_numeric numeric(20,6),          -- typed shadow for NUMBER/MONEY
  resolved_ref_id uuid,                            -- REFERENCE → catalog id (C3)
  unit_code varchar(16), currency_code char(3),
  sequence_number smallint NOT NULL,
  negated_flag boolean NOT NULL DEFAULT false,
  UNIQUE (rule_version_id, condition_group_id, sequence_number)
);
CREATE INDEX ix_rule_condition_attr
  ON ra_rule.rule_condition (tenant_id, attribute_name, comparison_value);

CREATE TABLE ra_rule.rule_action (
  rule_action_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  action_type varchar(48) NOT NULL,
  target_attribute varchar(64),        -- charge | quantity | balance | invoice_line …
  action_value text,
  action_value_type varchar(16),
  action_value_numeric numeric(20,6),  -- what the engine and every report read
  currency_code char(3), unit_code varchar(16),
  resolved_ref_id uuid,
  execution_sequence smallint NOT NULL,
  UNIQUE (rule_version_id, execution_sequence)
);

CREATE TABLE ra_rule.rule_parameter (
  rule_parameter_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  rule_action_id uuid REFERENCES ra_rule.rule_action ON DELETE CASCADE,  -- NULL = rule-level
  parameter_name varchar(64) NOT NULL,
  parameter_value text NOT NULL,
  parameter_value_type varchar(16) NOT NULL,
  parameter_value_numeric numeric(20,6),
  currency_code char(3), unit_code varchar(16), resolved_ref_id uuid,
  sequence_number smallint NOT NULL DEFAULT 0,   -- ordered params: rate tiers
  UNIQUE (rule_version_id, rule_action_id, parameter_name, sequence_number)
);
```

`rule_parameter` is the fix for D1: `params` JSONB stops being truth and survives
only inside `canonical_json` as a derived projection.

### C.4 Graph, sets, lineage, governance

```sql
CREATE TABLE ra_rule.rule_dependency (
  rule_dependency_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  depends_on_rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  dependency_type varchar(32) NOT NULL,   -- REQUIRES | PRECEDES | EXCLUDES | AMENDS
  notes text NOT NULL DEFAULT '',
  UNIQUE (tenant_id, rule_id, depends_on_rule_id, dependency_type),
  CHECK (rule_id <> depends_on_rule_id)
);   -- cycles detected by recursive CTE at publish

CREATE TABLE ra_rule.rule_fallback (
  rule_fallback_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  fallback_rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  fallback_level smallint NOT NULL,
  fallback_scope varchar(32) NOT NULL,    -- PRODUCT | SERVICE | GLOBAL_DEFAULT
  reason varchar(255) NOT NULL DEFAULT '',
  UNIQUE (tenant_id, rule_id, fallback_level),
  CHECK (rule_id <> fallback_rule_id)
);   -- makes exact → product → service → global explicit, not emergent

CREATE TABLE ra_rule.rule_set (
  rule_set_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL, code varchar(64) NOT NULL, name varchar(255) NOT NULL,
  description text NOT NULL DEFAULT '',
  set_type varchar(32) NOT NULL DEFAULT 'LOGICAL',  -- LOGICAL|RELEASE|VENDOR_IMPORT
  charging_mode varchar(16), status varchar(16) NOT NULL DEFAULT 'ACTIVE',
  owner varchar(255), source_system_id uuid REFERENCES rating.source_systems(id),
  UNIQUE (tenant_id, code)
);

CREATE TABLE ra_rule.rule_set_member (
  rule_set_member_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_set_id uuid NOT NULL REFERENCES ra_rule.rule_set ON DELETE CASCADE,
  rule_id uuid NOT NULL REFERENCES ra_rule.rule ON DELETE CASCADE,
  rule_version_id uuid REFERENCES ra_rule.rule_version,   -- pinned for RELEASE sets
  sequence_number integer NOT NULL DEFAULT 0,
  UNIQUE (rule_set_id, rule_id)
);

CREATE TABLE ra_rule.rule_validation_issue (            -- fixes D10
  issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  severity varchar(8) NOT NULL, code varchar(64) NOT NULL,
  message text NOT NULL, path varchar(128) NOT NULL DEFAULT '',
  hint text NOT NULL DEFAULT '', checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_validation_issue_code
  ON ra_rule.rule_validation_issue (tenant_id, code, severity);

-- Ingestion & lineage (the spec assumes these without listing them)
CREATE TABLE ra_rule.rule_ingestion_batch (
  batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  channel varchar(16) NOT NULL,        -- MANUAL | FILE | CONNECTOR | API
  source_system_id uuid REFERENCES rating.source_systems(id),
  profile_id uuid REFERENCES ra_rule.rule_import_profile,
  filename varchar(512), content_hash char(64),
  import_mode varchar(16) NOT NULL DEFAULT 'DELTA',   -- FULL | DELTA
  status varchar(24) NOT NULL,   -- STAGED|VALIDATING|AWAITING_APPROVAL|COMMITTING|
                                 -- COMPLETED|PARTIAL|FAILED|CANCELLED|ROLLED_BACK
  dry_run boolean NOT NULL DEFAULT false,
  counts jsonb NOT NULL DEFAULT '{}',
  checkpoint jsonb NOT NULL DEFAULT '{}',   -- D6: resume offset
  reasons jsonb NOT NULL DEFAULT '[]',
  started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
  triggered_by uuid, triggered_by_name varchar(255)
);
CREATE UNIQUE INDEX uq_batch_content ON ra_rule.rule_ingestion_batch
  (tenant_id, source_system_id, content_hash) WHERE content_hash IS NOT NULL;

CREATE TABLE ra_rule.rule_ingestion_record (
  record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  batch_id uuid NOT NULL REFERENCES ra_rule.rule_ingestion_batch ON DELETE CASCADE,
  source_offset integer NOT NULL, external_ref varchar(255),
  raw_payload jsonb NOT NULL, raw_hash char(64) NOT NULL,
  canonical_payload jsonb,
  decision varchar(16) NOT NULL,  -- NEW|CHANGED|UNCHANGED|WITHDRAWN|REJECTED|QUARANTINED
  rule_id uuid, rule_version_id uuid,
  issues jsonb NOT NULL DEFAULT '[]',
  UNIQUE (batch_id, source_offset)
) PARTITION BY HASH (batch_id);   -- 8 partitions; 40k-row dumps are routine

CREATE TABLE ra_rule.rule_source_lineage (
  lineage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  rule_version_id uuid NOT NULL REFERENCES ra_rule.rule_version ON DELETE CASCADE,
  source_system_id uuid REFERENCES rating.source_systems(id),
  batch_id uuid REFERENCES ra_rule.rule_ingestion_batch, record_id uuid,
  external_ref varchar(255), external_version varchar(64), raw_hash char(64),
  field_provenance jsonb NOT NULL DEFAULT '{}',
    -- {"priority":{"source_field":"tariffClass.rank","raw":"10","transform":"int"}}
  applied_aliases jsonb NOT NULL DEFAULT '{}',   -- B.3.4: original token preserved
  imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ra_rule.rule_import_profile (
  profile_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL, code varchar(64) NOT NULL, name varchar(255) NOT NULL,
  vendor varchar(64) NOT NULL, format varchar(16) NOT NULL,
  spec jsonb NOT NULL, version integer NOT NULL DEFAULT 1,
  is_system boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, code, version)
);
```

`rule_audit` (existing) moves to `ra_rule.rule_audit`, keyed
`(tenant_id, rule_id)` + `rule_version_id`, so the timeline still spans versions.

### C.5 Shared & charging metadata (spec §11's new groups)

```sql
-- ra_catalog
charging_unit     (charging_unit_id, tenant_id, code, name, dimension TIME|VOLUME|
                   EVENT|MESSAGE, base_unit, factor numeric(20,6), decimals)
pulse_profile     (pulse_profile_id, tenant_id, code, name, service_type,
                   initial_seconds, subsequent_seconds, round_mode,
                   min_chargeable_seconds)
charge_limit_profile  -- §11 "Minimum/Maximum Profiles"
                  (charge_limit_profile_id, tenant_id, code, name, service_type,
                   min_charge numeric(20,6), max_charge numeric(20,6),
                   min_quantity numeric(20,6), max_quantity numeric(20,6),
                   currency_code, unit_code)
reservation_policy (reservation_policy_id, tenant_id, code, name, ocs_profile_id,
                   initial_quota numeric(20,6), subsequent_quota numeric(20,6),
                   unit_code, validity_seconds, threshold_pct,
                   release_mode UNUSED|ALL, on_timeout RELEASE|EXTEND|TERMINATE)
```

These four are *why* the wizard can offer "pick a pulse profile" instead of
retyping 60/60 on 4,000 rules — and why a pulse change is one row, not 4,000
rule versions.

### C.6 Prepaid metadata (spec §9)

```sql
-- ra_catalog
balance_type      (balance_type_id, tenant_id, code, name,
                   category MAIN|PROMOTIONAL|VOICE|SMS|DATA|SHARED,
                   unit_code, is_monetary, allows_negative,
                   expiry_policy CALENDAR|ROLLING|NONE, UNIQUE(tenant_id, code))
balance_bucket    (balance_bucket_id, tenant_id, code, name, balance_type_id,
                   unit_code, initial_amount numeric(20,6), currency_code,
                   validity_days, carry_over_flag, shared_flag)
balance_priority  (balance_priority_id, tenant_id, charging_profile_id,
                   balance_type_id, service_type, consumption_order smallint,
                   UNIQUE(tenant_id, charging_profile_id, balance_type_id, service_type))
charging_profile  (charging_profile_id, tenant_id, code, name, charging_mode,
                   ocs_profile_id, reservation_policy_id, default_currency_code,
                   rounding_rule_id, negative_balance_allowed,
                   credit_limit_profile_id)
ocs_profile       (ocs_profile_id, tenant_id, code, name, vendor,
                   reservation_strategy, quota_unit, initial_quota,
                   subsequent_quota, quota_validity_seconds,
                   redirect_on_exhaust, connection jsonb)

-- ra_bundle
bundle                  (bundle_id, tenant_id, code, name, bundle_type,
                         charging_mode, current_version_id, status)
bundle_version          (bundle_version_id, tenant_id, bundle_id, version_number,
                         effective_from, effective_to, recurrence,
                         price numeric(20,6), currency_code, validity_days,
                         status, behaviour_hash)
bundle_bucket           (bundle_bucket_id, tenant_id, bundle_version_id,
                         balance_type_id, allowance numeric(20,6), unit_code,
                         unlimited_flag, expiry_days, shared_flag)
bundle_consumption_rule (bundle_consumption_rule_id, tenant_id, bundle_version_id,
                         bundle_bucket_id, rule_id → ra_rule.rule,
                         applies_when jsonb, consume_order smallint,
                         overage_action CHARGE|BLOCK|THROTTLE, overage_rule_id)
bundle_priority         (bundle_priority_id, tenant_id, charging_profile_id,
                         bundle_id, consumption_order,
                         UNIQUE(tenant_id, charging_profile_id, bundle_id))

-- ra_subscriber
subscriber_balance_profile (tenant_id, subscriber_id, charging_profile_id,
                            balance_type_id, current_amount numeric(20,6),
                            reserved_amount numeric(20,6), currency_code,
                            expiry_date, updated_at)
subscriber_bundle_history  (tenant_id, subscriber_id, bundle_version_id,
                            activated_at, deactivated_at, activation_source,
                            consumed numeric(20,6), remaining numeric(20,6))
balance_group              (balance_group_id, tenant_id, code, name,
                            group_type FAMILY|CORPORATE|IOT,
                            owner_subscriber_id, shared_balance_type_id)
balance_group_member       (balance_group_id, subscriber_id, role OWNER|MEMBER,
                            share_limit numeric(20,6), joined_at, left_at)
balance_reservation        -- I1: required by RESERVE/RELEASE, not in the spec list
                           (reservation_id, tenant_id, subscriber_id, session_id,
                            balance_type_id, reserved_amount numeric(20,6),
                            unit_code, currency_code,
                            status ACTIVE|CONSUMED|RELEASED|EXPIRED,
                            reserved_at, expires_at, released_at,
                            rule_version_id, UNIQUE(tenant_id, session_id, balance_type_id))
```

`balance_priority` + `bundle_priority` under a `charging_profile` are what turn
consumption order into **data**, replacing the hard-coded `consume_order` action
param the engine reads today.

### C.7 Postpaid metadata (spec §10)

```sql
-- ra_catalog
billing_cycle        (billing_cycle_id, tenant_id, code, name,
                      frequency MONTHLY|QUARTERLY|ANNUAL, cycle_start_day,
                      bill_run_offset_days, timezone, proration_profile_id)
invoice_component    (invoice_component_id, tenant_id, code, name,
                      component_type RECURRING|USAGE|ONE_TIME|CREDIT|TAX|
                      ADJUSTMENT|LATE_FEE, gl_account, tax_rule_id, display_order,
                      sign DEBIT|CREDIT)
recurring_charge     (recurring_charge_id, tenant_id, code, name, product_id,
                      offer_id, amount numeric(20,6), currency_code,
                      billing_cycle_id, invoice_component_id,
                      proration_profile_id, advance_flag,
                      effective_from, effective_to)
one_time_charge      (one_time_charge_id, tenant_id, code, name,
                      trigger_event ACTIVATION|SUSPENSION|SIM_SWAP|MIGRATION|MANUAL,
                      amount numeric(20,6), currency_code,
                      invoice_component_id, refundable_flag)
proration_profile    (proration_profile_id, tenant_id, code, name,
                      method DAILY|MONTHLY_30|ACTUAL_DAYS|NONE, round_mode,
                      apply_on_activation, apply_on_cease, apply_on_plan_change)
credit_limit_profile (credit_limit_profile_id, tenant_id, code, name,
                      limit_amount numeric(20,6), currency_code,
                      warning_threshold_pct,
                      breach_action NOTIFY|BAR_OUTGOING|BAR_ALL|NONE, grace_days)
usage_aggregation_profile  -- I3
                     (aggregation_profile_id, tenant_id, code, name,
                      dimension SUBSCRIBER|ACCOUNT|GROUP, window CYCLE|DAY|WEEK|MONTH,
                      service_type, reset_policy, invoice_component_id)
late_fee_profile     -- I4
                     (late_fee_profile_id, tenant_id, code, name,
                      fee_amount numeric(20,6), fee_percentage numeric(9,4),
                      currency_code, grace_days, max_occurrences,
                      invoice_component_id)

-- ra_subscriber
billing_account         (billing_account_id, tenant_id, account_number,
                         subscriber_id, account_type, billing_cycle_id,
                         credit_limit_profile_id, currency_code,
                         payment_terms_days, status,
                         UNIQUE(tenant_id, account_number))
account_product_history (tenant_id, billing_account_id, product_id, offer_id,
                         tariff_plan_id, from_date, to_date, change_reason)
billing_cycle_history   (tenant_id, billing_account_id, billing_cycle_id,
                         period_start, period_end, bill_run_date, invoice_number,
                         invoice_total numeric(20,6), tax_total numeric(20,6),
                         status OPEN|BILLED|SETTLED|DISPUTED,
                         payment_status UNPAID|PARTIAL|PAID|OVERDUE|WRITTEN_OFF)
credit_profile_history  (tenant_id, billing_account_id, credit_limit_profile_id,
                         limit_amount, from_date, to_date, changed_by, reason)
```

Rules reach these through `rule_parameter.resolved_ref_id` — e.g. a
`MONTHLY_RENTAL` rule's `ADD_RECURRING_CHARGE` action carries a
`recurring_charge` parameter resolving to `ra_catalog.recurring_charge`. No new
join mechanism, and the reference validator ([validation.py:41](ra_rating_backend/app/modules/rules/validation.py#L41))
just gains 14 entries in `_REFERENCE_MODELS`.

---

## Part D — Kernel, values, formats

### D.1 Typed value model — `canonical/valuetypes.py`

One codec used by every adapter, the validator, the writer and the compiler.

| Type | Storage | Notes |
|---|---|---|
| `NUMBER` | `numeric(20,6)` + canonical text | `Decimal(str(x))`, never `float(x)`. Fixes D1 |
| `MONEY` | `numeric(20,6)` + `currency_code` | Rejected without a currency. Minor-unit sources (BRM ships 1/100 000) declared in the profile and scaled on ingest |
| `BOOLEAN` | `'true'/'false'` | TRUTHY/FALSY sets kept from `imports/constants.py` |
| `ENUM` | upper-cased code | Validated against the attribute registry |
| `REFERENCE` | code in `comparison_value`, id in `resolved_ref_id` | Both — codes survive an environment move, ids give FK-grade joins (C3) |
| `DATE`/`DATETIME` | ISO-8601 UTC | `_DATE_FORMATS` kept; a naive datetime takes the *source system's* declared timezone, never the server's |
| `LIST` | `comparison_values` jsonb of typed elements | `IN` / `NOT_IN` |
| `RANGE` | two typed elements + ordering check | `BETWEEN` |

`canonical/units.py` holds the unit registry backed by `ra_catalog.charging_unit`.
`unit_code` is stored **as authored** and converted in the engine — never silently
at ingest — so the rate an operator can point at in the vendor's document is the
rate they see on our screen.

### D.2 `CanonicalDraft` — the single inbound shape

```python
@dataclass(frozen=True, slots=True)
class CanonicalDraft:
    rule_key: str | None              # None → derived deterministically (D.3)
    rule_name: str
    description: str
    charging_mode: ChargingMode       # PREPAID | POSTPAID | BOTH
    rule_type_code: str               # aliases already resolved
    service_type: str
    external_ref: str | None
    external_version: str | None
    condition_groups: tuple[DraftConditionGroup, ...]   # nested, ≤4 deep
    actions: tuple[DraftAction, ...]                    # each with DraftParameters
    behaviour: DraftBehaviour         # priority, stacking, conflict group,
                                      # fallback_policy, stop_processing, execution_mode
    targets: DraftTargets             # product / offer / tariff plan codes
    validity: DraftValidity           # effective_from/to, currency
    dependencies: tuple[DraftDependency, ...]
    fallbacks: tuple[DraftFallback, ...]
    set_codes: tuple[str, ...]
    provenance: Provenance            # source field + raw + transform, per field
    extras: Mapping[str, Any]         # unmapped vendor fields, preserved verbatim
```

`extras` is deliberate: vendor data we do not yet model stays on the ingestion
record and surfaces in the UI as "3 unmapped fields on this rule" — visible, not
silently discarded.

### D.3 Deterministic rule keys (fixes D2)

```
explicit key from the source   → use it
else external_ref present      → f"{source_code}:{external_ref}"
else                           → f"{source_code}:{sha1(natural_key)[:16]}"
   natural_key = charging_mode | service_type | rule_type
               | sorted(conditions) | sorted(action types)
```

Names never participate in identity: a rename is an update, a changed predicate is
a new logical rule. `derive_rule_key(name)` survives only as the wizard's
*suggestion*, uniqueness-checked with a numeric suffix instead of colliding.

### D.4 Format & profile layer

`ingest/profiles.py` reads `rule_import_profile.spec`, so onboarding a vendor
export is configuration plus tests, not a deploy:

```jsonc
{
  "format": "XML",
  "record_path": "/TariffExport/TariffClass/RateStep",
  "namespaces": {"t": "urn:ericsson:tariff"},
  "identity": {"external_ref": ["../@classId", "@stepId"]},
  "money": {"scale": 100000, "currency_from": "../@currency"},
  "timezone": "Europe/London",
  "defaults": {"charging_mode": "PREPAID", "execution_mode": "OFFLINE"},
  "fields": {
    "rule_name":      {"from": "../@name"},
    "priority":       {"from": "../@rank", "type": "NUMBER", "default": 100},
    "effective_from": {"from": "../@validFrom", "type": "DATE"}
  },
  "conditions": [
    {"attribute": "destination_zone", "from": "@zone", "operator": "EQUALS"},
    {"attribute": "time_band", "from": "@band", "operator": "EQUALS", "optional": true}
  ],
  "actions": [
    {"action_type": "SET_RATE", "target_attribute": "charge",
     "value": {"from": "@rate", "type": "MONEY"},
     "parameters": {"unit": {"from": "@unit"},
                    "per_units": {"from": "@perUnits", "default": 1}}}
  ],
  "postconditions": ["actions_non_empty", "currency_resolved", "mode_coherent"]
}
```

| Format | Change from today |
|---|---|
| CSV/TSV | Keep sniffing + BOM handling; **add streaming** — the in-memory `MAX_ROWS = 50_000` becomes an unbounded stream with a configurable cap |
| XLSX | Keep `read_only=True`; add multi-sheet and named-range headers |
| JSON | Streaming array parse (`ijson`) for >100 MB; keep the `{rules:[…]}` unwrap |
| XML | Replace the "whichever child repeats" heuristic with profile `record_path` + `iterparse` streaming and element clearing, namespace-aware. Heuristic retained as fallback so ad-hoc uploads keep working |
| Fixed-width | New — several BRM/legacy mediation exports are column-positional |
| DB (Oracle/MySQL/MSSQL) | New — `source_type='DATABASE'` today only *validates config* ([connectors/service.py:107](ra_rating_backend/app/modules/connectors/service.py#L107)). Add a read-only pooled reader, profile-supplied query, cursor paging |
| SFTP | New — same; poll, fetch, hash, dedupe by `content_hash` |

Vendor adapters become thin: a **system profile** row plus only the code a
declarative spec cannot express (Ericsson's class-header/rate-step flattening,
BRM's `RATE_PLAN`→`RATE_TIER` nesting). Today's `adapters.py` logic is preserved
as those functions — relocated and unit-testable, not rewritten.

### D.5 Kernel steps

**1 STAGE** — hash the payload; a completed `(source, content_hash)` returns that
batch (idempotent re-post). Persist every raw record before anything is
interpreted, so a parse crash is still forensically complete.

**2 NORMALIZE** — profile-driven mapping → `CanonicalDraft`, recording
`field_provenance` and `applied_aliases`. `suggest_mapping` remains the fallback
when no profile matches, and a successful manual mapping saves as a profile in one
click.

**3 TYPE** — §D.1. Failures are record-level, never batch-level.

**4 RESOLVE** — one batched query per catalogue for the *whole* batch (≈20 queries
for 40,000 records), cached per run; plus a single pre-loaded
`{(tenant, rule_key) → rule_id, behaviour_hash}` map. Together these kill D4.

**5 VALIDATE** — four tiers:
- *structural* — today's `validation.py`, ported to the normalized model.
- *mode coherence* (new, I5) — every action's stage must be in
  `pipeline(rule.charging_mode)`; a `BOTH` rule may use `COMMON` stages only; an
  `ONLINE` rule may not use a stage flagged `is_stateful=false`-incompatible;
  a prepaid rule may not reference postpaid-only catalogue entities.
- *semantic* — overlapping validity in a conflict group; unreachable rules (a
  broader higher-priority rule shadows it); rate sanity bands; monetary action
  without a currency; balance action without a resolvable balance type.
- *cross-rule* — dependency cycles (recursive CTE); fallback chains must
  terminate; coverage gaps per (charging_mode × service × zone × time band);
  duplicate `behaviour_hash` under different rule keys.

**6 RECONCILE** — `NEW` · `CHANGED` (hash differs) · `UNCHANGED` · `WITHDRAWN`
(FULL mode, in store, absent from export) · `REJECTED` · `QUARANTINED`.
Fixes D8: a withdrawal is **never** auto-applied to a non-draft rule; it becomes a
`WITHDRAWAL` proposal in the approvals inbox showing the rule's live traffic
volume.

**7 COMMIT** — chunks of 500 in savepoints, checkpointing after each chunk (D6:
resume, not restart). `writer.py` is the only writer. `dry_run=true` runs 1–6 and
rolls back — preview and commit are literally the same path, for connectors too
(D5).

**8 PROJECT** — recompute `specificity_score`, `behaviour_hash`, `canonical_json`
in the same transaction. `canonical_json` is the compiler's and engine's read
model, so `compiler/`, `rating/selection.py` and `rating/engine.py` go from
"read 5 tables" to "read one JSONB column" and the `rating.executable_rules`
contract is untouched. A nightly job re-derives it from the normalized rows and
alerts on mismatch, so the denormalization cannot drift silently.

### D.6 Approval gate for imports

```
STAGED → VALIDATING → AWAITING_APPROVAL → COMMITTING → COMPLETED
                            │
                            └─ reviewer sees: 12 new · 3 changed (field-level diff
                               vs the live version) · 1 withdrawn (4.2M CDRs/month
                               affected) · 8 quarantined
```

Per-source `auto_commit` (default **off**) is permitted only for batches with zero
`CHANGED`-against-an-ACTIVE-rule and zero `WITHDRAWN` decisions. Anything touching
a live price needs a human — that control is the product.

---

## Part E — Surfaces

### E.1 API (additive, under `/api/rating`)

```
Rules
  GET    /rules                    # facets: charging_mode, service, product, rule_type,
                                   #   status, source, snapshot, effective_on, validation
  POST   /rules                    # → CanonicalDraft → kernel (MANUAL channel)
  GET    /rules/{rule_id}          # logical rule + version list
  GET    /rules/{rule_id}/versions/{n}
  PATCH  /rules/{rule_id}/versions/{version_id}
  POST   /rules/{rule_id}/versions                  # cut N+1
  GET    /rules/{rule_id}/versions/{a}/diff/{b}
  POST   /rules/{rule_id}/copy                      # catalogue "Copy" action
  POST   /rules/{rule_id}/status                    # submit / approve / retire
  POST   /rules/{rule_id}/validate
  POST   /rules/{rule_id}/simulate                  # → rating + balance/invoice impact
  GET    /rules/{rule_id}/summary                   # human-readable text (wizard step 6)
  GET    /rules/{rule_id}/impact                    # impacted products/offers/subscribers
  GET    /rules/{rule_id}/lineage | /graph | /issues | /audit
  Full CRUD: /rule-dependencies /rule-fallbacks /rule-conflict-groups
             /rule-stacking-policies /rule-sets /rule-sets/{id}/members
Ingestion
  POST   /rule-ingest/batches                       # any channel, multipart or JSON
  GET    /rule-ingest/batches/{id}                  # counts, decisions, checkpoint
  GET    /rule-ingest/batches/{id}/records          # filter by decision
  GET    /rule-ingest/batches/{id}/rejects.csv
  POST   /rule-ingest/batches/{id}/approve | /cancel | /resume
  POST   /rule-ingest/batches/{id}/records/{rid}/repair
  GET/POST/PUT /rule-ingest/profiles                # + POST /{id}/test
Metadata (new groups, spec §11)
  /metadata/charging-units | pulse-profiles | charge-limit-profiles
  /metadata/balance-types | balance-buckets | balance-priorities
  /metadata/charging-profiles | ocs-profiles | reservation-policies
  /metadata/bundles (+ /versions /buckets /priorities /consumption-rules)
  /metadata/billing-cycles | invoice-components | recurring-charges
  /metadata/one-time-charges | proration-profiles | credit-limit-profiles
  /metadata/usage-aggregation-profiles | late-fee-profiles
Meta (builder generation)
  /meta/charging-modes | rule-categories | rule-types | rule-stages | pipelines
  /meta/attributes | actions | operators | value-types | units
```

Existing `/rules/import/*` and `/connectors/*/import` stay as thin façades over
`/rule-ingest` so the current UI keeps working, deprecated with a sunset header.

### E.2 Rule Catalogue screen (spec §6, exactly)

Columns — Rule Name · Rule Key · **Charging Mode** · Rule Type · Service ·
Product · Offer · Tariff Plan · Status · Priority · Specificity · Effective From ·
Effective To · Source · **Snapshot** · **Validation**.

| Column | Source |
|---|---|
| Charging Mode | `rule.charging_mode` — **new** |
| Rule Type | `rule_type.name` via FK — joinable, no longer an enum string |
| Product / Offer / Tariff Plan | `rule_version.*_id` → catalogue code + name |
| Priority / Specificity | `rule_version.priority` / `specificity_score` (read-only) |
| Source | `rule.source_system_id` → MANUAL / connector code / FILE_IMPORT |
| Snapshot | `rule_version.published_snapshot_id` → snapshot label — **new (C6)** |
| Validation | `rule_version.validation_state` PASS/WARNING/ERROR, drilling into `rule_validation_issue` — **new (D10)** |

Filters: Charging Mode · Service · Product · Rule Type · Status · Source ·
Snapshot · Effective Date. All eight are single indexed predicates on
`rule`/`rule_version` — no JSONB scans, which is why the normalized model is
worth the migration.

Row actions: View · Edit · Copy · Create New Version · Validate · Simulate ·
Compare · Submit for Approval · Retire · Audit — each maps 1:1 to an endpoint in
§E.1, gated by the existing `rating.role_permissions` RBAC.

### E.3 Create Rule wizard (spec §7, 6 steps)

| Step | Fields / content | Notes |
|---|---|---|
| **1 Basic Details** | Rule Name · Rule Key · Description · **Charging Mode** · Service Type · Rule Type · Execution Stage · Product · Offer · Tariff Plan · Priority · Stacking Policy · Conflict Group · Effective From · Effective To · Currency · Source | Charging Mode chosen **first** — it filters Rule Type to that mode's categories and Execution Stage to `pipeline(mode)`. Execution Stage is derived from Rule Type and shown read-only unless the type permits an override. Rule Key auto-suggested, editable, uniqueness-checked live |
| **2 Conditions** | Nested groups (≤4 deep), per-group AND/OR + negation, attribute picker generated from `/meta/attributes` filtered by charging mode | Both spec examples build without a custom control |
| **3 Actions** | Action palette filtered to `pipeline(mode)` — common always, prepaid or postpaid per mode; typed parameter forms from `/meta/actions`; money inputs carry a currency; quantity inputs carry a unit | An operator authoring a prepaid rule is never offered `ADD_INVOICE_COMPONENT` |
| **4 Behaviour** | Priority · Specificity (**read-only, with derivation shown** — C5) · Stacking Policy · Fallback Policy · Stop Processing · Conflict Handling · Execution Mode (ONLINE/OFFLINE/BOTH) | Four of these are new columns on `rule_version` |
| **5 Test** | Sample CDR (or synthetic generator) · enriched context · candidate rules · winning rule per stage · expected charge · **balance impact (prepaid) or invoice impact (postpaid)** · validation issues | Needs simulation extension — see I6 and phase R7 |
| **6 Review** | Human-readable rule summary · conditions · actions · dependencies · effective dates · impacted products · Submit for Approval | Summary generated server-side (`/rules/{id}/summary`) so the same sentence appears in the approval email, the audit trail and the UI |

The wizard is a client of the same `POST /rules` the importers use — it builds a
`CanonicalDraft`, and step 5 is `dry_run=true` through the identical kernel.

### E.4 Metadata Catalogue screen (spec §11)

| Group | Entities | State |
|---|---|---|
| Commercial | Products · Offers · Tariff Plans | exist |
| Charging | Services · Rating Groups · **Charging Units** · **Pulse Profiles** · **Min/Max Profiles** | 2 exist, 3 new |
| Prepaid | **Balance Types** · **Balance Buckets** · **Bundle Priorities** · **OCS Profiles** · **Reservation Policies** | all new (`balance_buckets` exists but is subscriber state, not catalogue) |
| Postpaid | **Billing Cycles** · **Invoice Components** · **Recurring Charges** · **One-Time Charges** · **Proration Profiles** | all new |
| Shared | Destination Zones · Prefixes · Time Bands · Discounts · Promotions · Taxes · Rounding Rules · Currencies | all exist |

The existing catalogue router is already generic over
`(model, schema, slug)` ([catalog/router.py](ra_rating_backend/app/modules/catalog/router.py)) — the 13
new entities are registry entries plus schemas, not 13 new routers.

---

## Part F — Delivery

### F.1 Migration — expand, migrate, contract

| Step | Migration | Detail |
|---|---|---|
| M1 | `0010_canonical_rule_schema` **(delivered)** | Creates the `ra_rule` schema and its 19 tables, `btree_gist`, the overlapping-window exclusion constraint, the covering selection index, and RLS enabled permissively on every table. Reversible (`downgrade` drops the schema; verified). Lookup rows are reconciled by `vocabulary.sync.sync_vocabulary`, called from `app.seed`, rather than frozen into the migration — so adding a stage is a restart, not a schema change. `ra_catalog`/`ra_bundle`/`ra_subscriber` move to M6 with the prepaid/postpaid work that populates them |
| M2 | `0011_backfill_rules` | `rating.rules` → `ra_rule.rule` + `rule_version`; one condition group per distinct `group_index`; `values` → typed `comparison_value(_type)`; `params` → `rule_action` + `rule_parameter` with `Decimal` **re-parsed from the retained raw import payload** where one exists, else from the stored value (flagged); synthesize lineage from `rule_import_rows` / `connector_imports`; every existing rule gets `charging_mode` inferred from its `account_type` condition, defaulting to `BOTH` with a review flag |
| M3 | — | **Parity job**: legacy row → compiled output vs canonical → compiled output, byte-identical for 100% of rules before M4. `MAXIMUM_CHARGE`'s stage move (D12) is the one expected diff and is reported separately |
| M4 | `0012_switch_writes` | Kernel is the only writer; legacy tables revoked INSERT/UPDATE at the role level, so a stray path fails loudly instead of diverging |
| M5 | — | Repoint compiler, `rating/selection.py`, `rating/engine.py`, dashboards, reports, `seed.py` |
| M6 | `0013_prepaid_postpaid` | Prepaid + postpaid metadata; migrate `bundle_definitions` → `ra_bundle.bundle` + v1 `bundle_version` + `bundle_bucket` |
| M7 | `0014_contract` | Drop `rating.rules`, `rule_conditions`, `rule_actions`, `rule_sets`, `bundle_definitions` after two release cycles and a verified backup |
| M8 | `0015_tenancy_enforced` | Real per-tenant RLS policy; `SET ra.tenant_id` in the session dependency in `core/deps.py` |

Backfill is a resumable script (`scripts/backfill_canonical.py`) with `--dry-run`,
`--limit`, `--rule-key` and a parity report — not a one-shot `op.execute`, because
a 40,000-rule backfill that dies at 90% must not restart from zero.

### F.2 Non-functional targets

| Concern | Target | How |
|---|---|---|
| Import throughput | ≥2,000 rec/s normalize, ≥500/s commit | batched resolution, chunked commit, bulk insert for conditions/params |
| 40k-rule full import | <5 min, <1 GB RSS | streaming parsers, nothing materialized whole |
| Catalogue query (all 8 filters) | p95 <200 ms at 40k rules | normalized indexed predicates |
| Rule detail read | p95 <120 ms | one `canonical_json` read |
| Selection | p95 <15 ms | covering index + `executable_rules` snapshot |
| Validate 40k rules | <90 s | per-tier batching; cross-rule tier as set operations |
| Online (prepaid) snapshot load | only `execution_mode IN (ONLINE, BOTH)` rules | I2 — an online engine must not carry invoice rules |
| Tenancy | cross-tenant read impossible at SQL level | RLS + session GUC, proven by test |
| Secrets | credentials never leave the process | keep `redact()`; envelope-encrypt at rest (`pgcrypto`) |
| Audit | every write has actor, reason, lineage | writer refuses a commit without an actor |
| Observability | per-batch events; records/s, decision mix, quarantine rate, parity mismatches | extend `core/logging.py`, `pipeline_events` |

### F.3 Testing

| Layer | Tests |
|---|---|
| Vocabulary | Python registry ↔ seeded rows match exactly; every rule type's stage is in its category's pipeline; stage orders unique and gap-numbered |
| Value codec | Property tests: money round-trips losslessly; an AST test bans `float(` under `rules/**` |
| Key derivation | Same name + different predicates → different keys; rename keeps the key |
| Mode coherence | A `BOTH` rule with `DEDUCT_BALANCE` is rejected; a prepaid rule with `ADD_INVOICE_COMPONENT` is rejected; an `ONLINE` invoice rule is rejected |
| Adapters | Golden-file per vendor (Ericsson XML, BRM JSON/DB, Huawei CSV, generic XLSX) → asserted `CanonicalDraft` snapshot |
| Kernel | Idempotency (same file twice → one batch, zero versions); resume after mid-batch failure; dry-run writes nothing; 40k perf smoke |
| Reconciliation | Renames ignored; a 0.000001 rate change caught; withdrawal of an ACTIVE rule proposes, never retires |
| Round-trip | draft → write → read → identical draft, for all 39 rule types incl. nested groups |
| Governance | Dependency cycle rejected; overlapping ACTIVE windows rejected by the exclusion constraint; fallback chain must terminate |
| Prepaid E2E | Prepaid voice CDR: bucket selection → reservation → rating → deduction → insufficient-balance partial charge, with a balance-impact assertion |
| Postpaid E2E | Monthly rental + usage + proration + credit check + invoice tax + rounding → invoice projection assertion |
| Migration | Backfill parity on a 5,000-rule seeded estate; compiled output identical pre/post except the reported `MAXIMUM_CHARGE` diff |
| Tenancy | Cross-tenant read returns nothing; cross-tenant write raises |
| Contract | Existing `/api/rating/rules*` and `/connectors/*` response shapes unchanged during the façade window |

### F.4 Phases

**R1 — Vocabulary + canonical schema + tenancy. ✅ DELIVERED.**
`rules/vocabulary/` (3 charging modes, 31 mode-scoped stages, 39 rule types, 35
actions, 34 attributes, 4 alias tables, stacking policies, value types);
`rules/canonical/` (19 ORM tables); migration `0010`; `vocabulary/sync.py` wired
into `app.seed`; RLS scaffolding and `tenant_id` throughout.
*Exit met:* 19 tables + RLS + exclusion constraint verified against live Postgres;
52 new tests; full suite 215 passed; seeder idempotent on re-run; migration
reverses cleanly. **Fixes D11.**

Three implementation decisions worth recording, all made to keep the phase
zero-impact:

1. **`constants.py` is byte-unchanged.** The original plan had R1 refactor it into
   a shim. It cannot: [compiler.py:34](ra_rating_backend/app/modules/compiler/compiler.py#L34)
   derives `executable_rules.stage_order` from *index positions* in
   `STAGE_ORDER`, so appending even one stage silently renumbers every snapshot
   ever compiled. The canonical registry therefore sits **alongside** the legacy
   one as a strict superset, and 13 parity tests fail if the overlap diverges —
   including one that asserts `STAGE_ORDER` still has exactly 11 entries. R4
   retires the legacy module once the compiler reads from the canonical side.
2. **`String(36)` UUIDs, not native `uuid`.** Every incumbent PK is a 36-char
   string and the canonical tables carry real FKs into `rating.products` /
   `offers` / `tariff_plans` / `source_systems` / `rule_snapshots`. A native
   `uuid` column cannot reference a `varchar(36)` one, so matching the incumbent
   type is what keeps those FKs enforced rather than advisory.
3. **An action's `stage_code` is a default, not a constraint.** The rule *type*
   decides when a rule fires; the action's stage is where it normally belongs and
   is used to infer a type from a tariff sheet with no type column. The two
   legitimately differ — `APPLY_TAX` runs at `TAX` for a usage rule and at
   `INVOICE_TAX` for a postpaid invoice rule, and minting `APPLY_INVOICE_TAX`
   would be two implementations of one idea. The invariant that *is* enforced, and
   tested, is mode coherence: every action a rule uses must sit at a stage inside
   `pipeline(rule.charging_mode)`.

Also deferred deliberately: `rule_ingestion_record` (created hash-partitioned in
R5, since repartitioning a populated table means a rewrite and nothing before R5
writes it); tenant resolution in `core/deps.py` (moves to M8 with the real RLS
policy, as the permissive policy has no runtime dependency on the session GUC);
`/meta/*` extension (R3, alongside the API that consumes it).

**R2 — Typed values + draft + writer. ✅ DELIVERED.** `canonical/`,
`ingest/writer.py`, `ingest/resolver.py`, round-trip tests.
*Exit met:* a `CanonicalDraft` persists and reads back losslessly, money as
`Decimal`, for all 39 rule types plus nested groups.

Two defects surfaced the first time the round-trip suite ran against a live
Postgres rather than being skipped, and both are fixed:

1. **Child rows carried a null parent id.** `_write_groups` and `_write_actions`
   read `row.condition_group_id` / `row.rule_action_id` off a freshly constructed
   ORM object, but a Python-side column default is not applied until flush — so
   every condition went in with a null group. Both ids are now minted before the
   parent row is built, which is the only ordering that works when parent and
   children are constructed in one unit of work.
2. **A draft did not hash equal to its own stored form.** The codec quantizes
   numbers to six decimal places and the writer substitutes the action spec's
   default `target_attribute`, so `behaviour_hash(draft)` and
   `behaviour_hash(read_back(draft))` disagreed on `1` vs `1.000000` and on
   `""` vs `"charge"`. Left alone this would have made reconciliation report the
   entire estate as CHANGED on the first re-import — the exact failure the hash
   exists to prevent. `fingerprint` now canonicalises both before hashing.

**R3 — Kernel + validation tiers. ✅ DELIVERED (except the `POST /rules` switch).**
`ingest/kernel.py` (the eight steps, chunked commit with per-chunk savepoint and
checkpoint, dry-run on the identical path), `ingest/reconcile.py`,
`ingest/keys.py` (deterministic identity, D2), and `validation/` as a package —
`structural`, `modes`, `semantic`, `registry` — with issues persisted to
`rule_validation_issue`.
`validation.py` became `validation/legacy.py` re-exported from the package, so
every existing `from app.modules.rules import validation` caller (the compiler,
the file importer, the rule service) is untouched.
*Exit met:* the kernel writes canonically, rejects mode-incoherent rules, cuts no
version for an unchanged re-import, proposes rather than applies withdrawals, and
never auto-commits a batch that touches live pricing. **Fixes D1, D2, D7, D10,
and D5/D6/D8 for anything on the kernel path.**

*Deliberately still outstanding:* `POST /rules` is **not** yet routed through the
kernel, and the legacy manual writer is **not** deleted. Doing either now would
diverge the estate: the compiler and `rating/engine.py` still read
`rating.rules`, so a hand-authored rule written only to `ra_rule.*` would stop
rating. That switch belongs with R4's backfill and parity gate, which is why the
plan calls R1–R4 one indivisible unit. The AST lock-down test lands with it, for
the same reason — it would fail today against the legacy writer it is meant to
outlaw.

**R4 — Backfill + parity + write switch. ⏳ M2–M3 DELIVERED; M4–M5 gated on you.**

Delivered: `rules/ingest/adapters/legacy.py` (legacy row → `CanonicalDraft`),
`rules/canonical/compilerview.py` (a canonical version wearing the legacy row's
interface), `rules/backfill.py` and `scripts/backfill_canonical.py`
(`--dry-run`, `--limit`, `--rule-key`, `--parity-only`, resumable and
idempotent — a second run reports UNCHANGED rather than re-versioning).
*Exit met for M3:* parity clean on the seeded estate and on the twelve rule
shapes a naive converter gets wrong.

The parity gate compiles **both sides through the same `compile_rule`**. That is
the whole design: comparing an old compiler against a new one proves the two
implementations agree, which is a much weaker claim than the one anybody cares
about — that the migration changed nothing. It has already earned its place
twice, catching two defects that would have shipped silently:

1. **Version renumbering.** The kernel numbers from 1, so a legacy rule at v2
   landed as canonical v1. `rule_version` is how an operator and a six-month-old
   rating result refer to a specific rule text; renumbering makes every such
   reference wrong. The backfill now carries the legacy number across, leaving an
   honest hole beneath it — those earlier versions really do exist, in the legacy
   table this phase is not dropping.
2. **Group-index shift.** A legacy rule with two bracketed groups converts to a
   root that holds nothing and exists only to carry the OR between them.
   Numbering that empty root 0 pushed its children to 1 and 2, so every compiled
   predicate carried a group index one higher than the rule it came from —
   invisible on screen, and it changes which predicates the engine ANDs together.

Two judgements the backfill makes, both reported rather than assumed: a rule with
no `account_type` condition gets `charging_mode = BOTH` (the only value that
cannot be wrong, since it preserves exactly today's behaviour), and a monetary
value whose float storage cannot be reversed is written as the closest exact
decimal **and listed under `needs_money_review`**. That is §G.6 answered by
reporting — the alternative, carrying an unverifiable rate silently into a
money-exact model, defeats the point of building one.

*Not done, deliberately:* M4's write revoke and M5's compiler repoint. Both change
live behaviour and both belong behind a green parity report on **your** estate,
not behind a code review of this one. `MAXIMUM_CHARGE`'s stage move (D12) ships
with them, since it is the one intended difference and needs its own before/after
report. **Fixes D12 when M4–M5 land.**

**R5 — Ingestion at scale. ⏳ THE API AND THE PATH ARE IN; SCALE AND PROFILES ARE NOT.**

Delivered: `rule_ingestion_record` (migration `0012`, hash-partitioned on
`batch_id` into 8 partitions, established before anything writes because
repartitioning a populated table is a rewrite); `ingest/files.py` and the
`tabular` and `payload` adapters; `/rule-ingest/*` — `columns`, `preview`,
`batches` (upload, history, detail), `batches/{id}/records`,
`batches/{id}/rejects.csv`, `batches/{id}/cancel`, `connectors/{id}/sync`.

The adapters *reuse* rather than replace what works: `imports.parser` (CSV
sniffing, XLSX, JSON, XML) and `suggest_mapping` are in production and correct,
and the vendor connectors already know how to read Ericsson's class headers and
BRM's `RATE_PLAN`→`RATE_TIER` nesting. What is new is the conversion to a draft,
because the legacy mappers produce floats.

**Fixes D3–D8** on this path: vendor identity (`external_ref` as a first-class
column, so a re-import updates rather than duplicates), batched resolution, a
preview that is the commit minus the commit, chunked checkpointed writes,
`behaviour_hash` change detection, and withdrawal-as-proposal.

One defect the end-to-end run found and fixed: an unregistered `source_system_code`
surfaced as N quarantined records with a commit error, after every row had been
typed, resolved and validated. That reads as "my file is broken" when the truth is
"that source system does not exist yet". It is now a batch-level precondition
raised before a single row is interpreted — one wrong argument must not look like
a thousand wrong rows.

*Still outstanding:* the profile registry (`rule_import_profile` exists and is
unused, so onboarding a vendor export is still code rather than configuration);
streaming parsers and fixed-width (the in-memory `MAX_ROWS` cap stands, so the
40k-row / <5 min / <1 GB target is unproven); DB and SFTP connector *readers*;
checkpoint **resume** as an endpoint (the checkpoint is written, nothing consumes
it yet); and the approval gate as a workflow — `touched_live_pricing` is computed
and returned, but nothing yet blocks on it.

**R6 — Governance depth.** Dependencies with cycle detection; fallback chains;
conflict groups and stacking policies as data; `RELEASE` sets pinned for the
compiler; persisted validation issues; semantic + cross-rule + mode-coherence
tiers.
*Exit:* publish blocked on a cycle, an overlapping window, an unterminated
fallback chain or a mode-incoherent rule. **Fixes D10.**

**R7a — Prepaid.** Metadata ✅ DELIVERED; execution outstanding.
`app/modules/charging/` and migration `0011` create the prepaid catalogue —
balance types, bucket definitions, OCS profiles, reservation policies, charging
profiles, balance priorities — plus the shared charging metadata of §C.5
(charging units, pulse profiles, min/max profiles). All seventeen entities are
served by the *existing* generic catalogue router, so they arrived as registry
entries and schemas rather than seventeen new routers, and the resolver now
backs every catalogue the vocabulary references (`PENDING_CATALOGUES` is empty,
and a test keeps it that way).
*Still outstanding:* `balance_reservation` and `subscriber_balance_profile`
(subscriber state, not catalogue metadata), bundle versioning, the 5 prepaid
stages executing in the engine, and balance-impact simulation for wizard step 5.
*Exit:* the prepaid E2E test in F.3 passes.

**R7b — Postpaid.** Metadata ✅ DELIVERED; execution outstanding.
Billing cycles, invoice components, recurring charges, one-time charges,
proration profiles, credit limit profiles, usage aggregation profiles and late
fee profiles all exist and are editable.
*Still outstanding:* `billing_account` / `billing_cycle_history` /
`account_product_history` / `credit_profile_history` (subscriber state), the 10
postpaid stages executing in the engine, and invoice projection for wizard step 5.
*Exit:* the postpaid E2E test in F.3 passes.

> **Schema decision, taken during this phase.** The plan's §C put the metadata
> catalogue in `ra_catalog` / `ra_bundle` / `ra_subscriber`. It is instead all in
> the **rule-management schema, `ra_rule`**, beside `rule` and `rule_version`.
> Every one of these tables exists to be named by a rule parameter, is edited on
> the same screens, is granted to the same role and is restored in the same
> recovery as the rules themselves; three more schemas would have put a boundary
> through the middle of one lifecycle and made every rule→metadata reference a
> cross-schema foreign key. `catalog_db_schema` / `bundle_db_schema` /
> `subscriber_db_schema` remain as settings, so a deployment that wants them
> split later can do it without a code change.
>
> One rename follows from sharing a schema: the catalogue table is
> `balance_bucket_definition`, because `rating.balance_buckets` already exists and
> holds a *subscriber's* balance. They are different things, and merging them
> would leave "how much data does the ADD_5GB bucket grant" answerable only by
> scanning subscriber rows.

**R8 — UI.** Catalogue's 16 columns / 8 filters / 10 row actions; the 6-step
wizard; Lineage, Graph, Issues, Parameters tabs; import batch console; profile
manager; 13 new metadata screens; approvals inbox extension.

**Sequencing:** R1–R4 is one indivisible unit — shipping R1/R2 alone leaves two
models with no parity gate. R5 is where the visible value lands. R6, R7a, R7b and
R8 parallelize once R4 is done; R8's wizard needs R7a/R7b for step 5's impact
panes, so build the other five steps first.

---

## Part G — Concerns and open questions

**All four open questions are now answered (2026-07-30).** The decisions and what
each one changes:

| | Decision | Consequence |
|---|---|---|
| **G.2** | Revenue assurance **over** the operator's charging systems (Ericsson, Oracle BRM, Huawei CBS) — we do not implement an OCS | R7a shrinks by an order of magnitude. No session runtime, no sub-100 ms budget, no reservation state we author. `balance_reservation` becomes an **observed** table populated from the OCS, not one we write during a session. `execution_mode=ONLINE` means "this rule describes what the OCS should have done", and the prepaid engine work is *offline recalculation and reconciliation against OCS actuals* |
| **G.3** | Postpaid **invoice projection and bill simulation** only | R7b confirmed as scoped. No invoice generation, numbering, delivery or payment allocation. `billing_cycle_history.invoice_number` becomes a field we *read* from the billing system to correlate against, not one we mint |
| **G.4** | **True multi-tenancy** with tenant isolation | Promotes M8 from future-proofing to a real deliverable, and moves it earlier: retrofitting isolation onto a populated estate is exactly the cost the tenant column was added to avoid. See §G.4 below for the one structural problem this exposes |
| **G.5** | Real anonymised **Ericsson Charging** and **Oracle BRM** exports as golden fixtures | R5's vendor tests become meaningful. Today the XML adapter is tested against shapes I invented, which proves it is self-consistent and nothing more |

**G.4 has a structural problem worth stating.** This service has no tenant
anywhere in its identity chain: the JWT that `ra_backend` issues carries `sub`,
`sid` and `type` and nothing else, and `administration.users` is a **read-only**
view we may not extend. So a tenant cannot be taken from the token or from the
user record without a change to a service outside this plan's scope.

The answer is a tenant registry and membership table that *this* service owns
(`ra_rule.tenant`, `ra_rule.tenant_membership`), resolved alongside the role
permissions we already read. If `ra_backend` later adds a `tenant_id` claim, it
is honoured in preference — but nothing waits on that.

**And one limitation that cannot be engineered away yet.** Isolation covers
`ra_rule.*`, which is every table with a `tenant_id`. The legacy `rating.rules`,
`rule_conditions` and `rule_actions` have no tenant column and cannot get one
without rewriting every index on a live table. Until M7 drops them, a second
tenant's rules are isolated in the canonical model and **not** in the legacy one.
That is an argument for finishing the R4 cut-over before onboarding a second
operator, not an argument for delaying tenancy.

Below is the original text of the questions, kept because the reasoning still
explains why the answers matter.

Four things in the spec need a decision before the phase they land in, and one I
would push back on.

**G.1 Push-back: author-set Specificity (C5).** §7 step 4 lists Specificity beside
Priority as an author field. Two hand-tuned knobs for one job is how a 10,000-rule
estate becomes unpredictable — an author raises specificity to win a fight,
someone else raises priority, and neither can later say which rule wins. I have it
**computed and read-only, with the derivation shown** ("product 50 + destination
zone 45 + time band 35 = 130"). If you want it editable, the honest version is an
explicit `specificity_override` column, defaulting NULL, requiring a reason,
visible in the audit trail — say the word and I will add it that way.

**G.2 Prepaid online charging is a second runtime, not a second rule model.**
`RESERVE_BALANCE` / `RELEASE_RESERVATION` / `STOP_SERVICE` only mean anything
inside a live session with sub-100 ms budgets and a reservation store. The *rule
model* is shared (that requirement is met); the *execution plane* is not — this
service is an offline assurance recalculator. `execution_mode` and
`balance_reservation` let us author, validate, version and compile those rules
correctly, and reconcile them against what the real OCS did. **Is that the intent
— assurance over the operator's OCS — or is this service expected to *be* the
online charging function?** The answer changes R7a's size by an order of
magnitude.

**G.3 Postpaid: metadata + rating, or invoicing too?** `LATE_FEE`,
`INVOICE_ROUNDING`, `USAGE_AGGREGATION` and `billing_cycle_history.invoice_number`
imply a bill run that produces invoices. I have scoped R7b to *metadata + rule
execution + invoice projection* (what a bill run *would* produce for a given
account and cycle), not invoice generation, numbering, delivery or payment
allocation. Confirm — a real bill run is its own phase.

**G.4 Tenancy** — one operator per deployment (`tenant_id` as future-proofing,
one row) or genuinely multi-tenant now? Decides how much of R1's RLS work is real.

**G.5 Vendor fixtures** — one anonymized real Ericsson and one Oracle BRM export.
R5's golden-file tests are only as good as the fixtures, and guessed shapes are
precisely how import projects fail.

**G.6 Money already stored as `float`** — is re-parsing from retained raw import
payloads acceptable, or should un-reparseable rules be flagged for manual
re-confirmation rather than silently carried forward?

## Part H — Decisions taken

1. **Mode-scoped stage registry, one engine** (§A.1). The spec's "do not keep
   prepaid and postpaid in separate engines" costs exactly one column,
   `rule_stage.applies_to`, and a derived pipeline. Everything else — conditions,
   actions, selection, compiler — stays single.
2. **Normalized truth + `canonical_json` projection.** Normalized rows give
   constraints and make the catalogue's 8 filters index scans; the projection keeps
   the compiler and engine on one cheap read. The nightly re-derivation check is
   the price and it is worth paying.
3. **Spec column names verbatim** (`rule_version_id`, `comparison_value_type`,
   `negated_flag`, `execution_sequence`…). Additions (`tenant_id`,
   `behaviour_hash`, `canonical_json`, `*_numeric`, `execution_mode`,
   `stop_processing`, `fallback_policy`, `published_snapshot_id`, lineage tables)
   each carry a stated reason.
4. **Explicit action names kept canonical, spec's short names accepted as
   aliases** (C2), with the original token preserved in lineage.
5. **Reference values stored as codes *and* resolved ids** (C3) — codes survive an
   environment move, ids give real joins.
6. **One kernel, enforced by an AST test.** Documentation does not stop a future
   connector calling `db.add(Rule(...))`.
7. **Withdrawals never auto-apply to live rules.** A vendor omission silently
   retiring a rule that rates 4M CDRs a month is the exact failure this product
   exists to catch.
8. **`float` banned from the rule path**, retroactively where raw payloads allow.
