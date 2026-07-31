# Mirror Mapping — `radonaix_app` → `rafms_rating_new.canonical_rating`

Companion to [RULE_MIRROR_SYNC_ANALYSIS.md](RULE_MIRROR_SYNC_ANALYSIS.md), which
covers the data-flow analysis and the design. This is deliverable 2: the
column-by-column mapping, written against the supplied DDL.

Legend: **✓** mapped · **⊘** no source, left NULL/default · **⚠** lossy or a
policy decision · **✗** platform data with no target column.

---

## 1. `rating_rule` ← `ra_rule.rule` ⋈ `ra_rule.rule_version`

**Grain decision: one target row per *rule version*, keyed on
`rule_version_id`.** The target carries `version_no`, an effective window and a
status — it is shaped to hold one row per version and let a reader pick the one
live at an event's timestamp (that is exactly what `idx_rule_active_window`
indexes). Collapsing onto the logical rule would keep only the newest statement
and make every historical rating result un-re-explainable.

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `rule_id` | `rule_version.rule_version_id` | ✓ | UUID string, 36 ≤ 50 |
| `rule_name` | `rule.rule_name` | ⚠ | truncated 255 → 250 |
| `rule_description` | `rule.description` | ⚠ | truncated to 1000 |
| `rule_stage` | `rule_stage.code` via `rule.rule_stage_id` | ⚠ | 30 platform stages → 7 target stages, see §1.1 |
| `rule_type` | `rule_type.code` via `rule.rule_type_id` | ✓ | free varchar in target, no FK |
| `priority` | `rule_version.priority` | ✓ | |
| `match_strategy` | `rule_version.stop_processing` + `rule_stacking_policy.allows_multiple` | ⚠ | `stop_processing` → FIRST_MATCH; else `allows_multiple` → ALL_MATCHES; else BEST_MATCH |
| `currency_code` | `rule_version.currency_code` | ✓ | dropped to NULL if not exactly 3 chars |
| `account_scope` | `rule.charging_mode` | ✓ | PREPAID/POSTPAID/BOTH map 1:1; target's HYBRID is unused |
| `effective_from` | `rule_version.effective_from` | ✓ | DATE → midnight UTC |
| `effective_to` | `rule_version.effective_to` | ⚠ | **inclusive date → exclusive timestamp at start of next day.** Required by `chk_rating_rule_dates`; a same-day window would otherwise fail the CHECK, and treating it as midnight *of* that day would silently shorten every rule by a day |
| `version_no` | `rule_version.version_number` | ✓ | |
| `status` | `rule_version.status` | ⚠ | 9 states → 6, see §1.2 |
| `source_system_code` | — | ⊘ | FKs to `canonical_rating.source_system`, outside the ten tables in scope. Writing a code with no parent row fails the constraint |
| `created_by` | `rule_version.created_by` → `rule.created_by` → `"SYSTEM"` | ✓ | NOT NULL in target |
| `created_at` | `rule_version.created_at` | ✓ | |
| `approved_by` | `rule_version.approved_by` | ✓ | |
| `approved_at` | `rule_version.approved_at` | ✓ | |

**Platform columns with no target home (✗):** `rule.rule_key` (the logical
identity — the single most significant loss), `rule.external_ref`,
`rule.current_version_id`, `rule.owner`, `rule_version.behaviour_hash`,
`specificity_score`, `execution_mode`, `fallback_policy`, `conflict_group_id`,
`validation_state`, `supersedes_id`, `published_snapshot_id`, `canonical_json`,
`extras`, `change_reason`, `submitted_by/at`, `retired_at`, `tenant_id`,
`product_id`, `offer_id`, `tariff_plan_id`.

### 1.1 Stage mapping (30 → 7)

| Target stage | Platform stages |
| --- | --- |
| CLASSIFICATION | ELIGIBILITY, SERVICE_CLASSIFICATION, DESTINATION_CLASSIFICATION, TIME_BAND, BILLING_CYCLE_ASSIGNMENT |
| BASE_RATE | QUANTITY, TARIFF_SELECTION, MINIMUM_CHARGE, PULSE, BASE_CHARGE, RECURRING_CHARGE, ONE_TIME_CHARGE |
| ALLOWANCE | BUNDLE, BALANCE_SELECTION, BALANCE_DEDUCTION |
| DISCOUNT | DISCOUNT, PROMOTION |
| SURCHARGE | SURCHARGE, LATE_FEE |
| TAX | TAX, INVOICE_TAX |
| ROUNDING | ROUNDING, INVOICE_ROUNDING |
| **no mapping — rule skipped** | MAXIMUM_CHARGE, BALANCE_RESERVATION, BALANCE_EXCEPTION, SESSION_CONTROL, USAGE_AGGREGATION, PRORATION, CREDIT_CHECK, INVOICE_COMPONENT |

The eight unmapped ones are unmapped deliberately. `MAXIMUM_CHARGE` is a cap
applied after discounts settle and the target has no cap stage; the balance and
session stages are online-charging mechanics; the rest are postpaid billing steps
with no usage-rating counterpart. Filing any of them under SURCHARGE or
CLASSIFICATION would put the rule at a pipeline position that changes what it
means. Rules on those stages are **skipped and logged** (`mirror_rule_unmappable`).

### 1.2 Status mapping (9 → 6)

| Platform | Target | Reasoning |
| --- | --- | --- |
| DRAFT, VALIDATED | DRAFT | Target has no separate validated state |
| REVIEWED, APPROVED, COMPILED | PENDING_APPROVAL | Approved but not yet live |
| PUBLISHED, ACTIVE | ACTIVE | The platform's own `ex_rule_version_active_window` treats ACTIVE and PUBLISHED as the same live window; the mirror must not contradict the constraint that governs it |
| SUPERSEDED | INACTIVE | |
| RETIRED | RETIRED | |

Target's `REJECTED` has no platform equivalent and is never written.

---

## 1.3 Legacy source — `rating.rules` (the path the UI writes)

`RULE_COMPILE_SOURCE` defaults to `LEGACY`, and the rule-management API writes
the legacy model. `MIRROR_RULE_SOURCE` (default `AUTO`) follows it, so in a
default deployment §1's canonical mapping is *not* what runs — this is.

The legacy row is already one-per-version, so the grain matches the target
directly with no rule/version join.

| Target column | Legacy source | | Notes |
| --- | --- | :-: | --- |
| `rule_id` | `rules.id` | ✓ | |
| `rule_name` | `rules.name` | ⚠ | truncated to 250 |
| `rule_description` | `rules.description` | ⚠ | truncated to 1000 |
| `rule_stage` | `rules.execution_stage` | ⚠ | same §1.1 stage map |
| `rule_type` | `rules.rule_type` | ✓ | |
| `priority` | `rules.priority` | ✓ | |
| `match_strategy` | `rules.stacking_policy` | ⚠ | EXCLUSIVE→BEST_MATCH, STACKABLE→ALL_MATCHES, OVERRIDE→FIRST_MATCH. Legacy has no `stop_processing` |
| `currency_code` | `rules.currency_code` | ⚠ | `varchar(8)` on the platform; dropped unless exactly 3 |
| `account_scope` | — | ⊘ | always `BOTH` — the legacy model predates the prepaid/postpaid split |
| `effective_from` / `effective_to` | same | ✓ | inclusive→exclusive as §1 |
| `version_no` | `rules.version` | ✓ | |
| `status` | `rules.status` | ⚠ | same §1.2 status map |
| `created_by` / `created_at` / `approved_by` / `approved_at` | same | ✓ | |

**Conditions** (`rule_conditions` → `rating_rule_condition`): `group_index` is
already the integer the target wants, so no tree flattening is needed — the two
models make the same simplification. Groups collapse to 1 when the rule's
`condition_logic` is AND (distinct group numbers are OR'd in the target),
otherwise `group_index + 1`.

Legacy conditions carry **no declared value type** — the gap the canonical model
was built to close. It is recovered from the attribute registry
(`vocabulary/attributes.py`), which is where the legacy engine infers it from
too. An attribute absent from the registry **skips the rule** rather than
defaulting to STRING.

**Actions** (`rule_actions` → `rating_rule_action`): legacy holds parameters in a
JSONB `params` dict; each key becomes one target row, ordered alphabetically, with
`action_id = "{action.id}:{param_name}"` to keep the primary key unique. Declared
types come from the action registry's `ParamSpec`, not from the value.

**✗ unmapped:** `rule_key`, `supersedes_id`, `category`, `service_type`,
`rule_set_id`, `product_id`, `offer_id`, `tariff_plan_id`, `specificity`,
`conflict_group`, `source_system`, `owner`, `last_validation`, `attributes`,
`change_comment`, `submitted_by/at`, `retired_at`.

---

## 2. `rating_rule_condition` ← `ra_rule.rule_condition` (+ `rule_condition_group`)

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `condition_id` | `rule_condition.rule_condition_id` | ✓ | |
| `rule_id` | `rule_version.rule_version_id` | ✓ | matches §1's grain |
| `condition_group` | flattened from the group tree | ⚠ | see §2.1 |
| `sequence_no` | recomputed per group, 1-based | ⚠ | platform numbers restart per group; `uq_rule_condition_sequence` is (rule_id, condition_group, sequence_no), so a per-group counter is required |
| `attribute_name` | `rule_condition.attribute_name` | ✓ | FK onto `rule_attribute`, seeded first |
| `operator_code` | `rule_condition.operator_code`, **inverted if negated** | ⚠ | see §2.2 |
| `value_type` | `rule_condition.comparison_value_type` | ⚠ | see §2.3 |
| `comparison_value` | `comparison_value`, or JSON array for IN/NOT_IN, or element 0 for BETWEEN | ⚠ | |
| `comparison_value_to` | `comparison_values[1]` for BETWEEN | ✓ | NULL otherwise |
| `case_sensitive` | — | ⊘ | Platform has no per-predicate flag; its comparisons are case-sensitive, which is the target's `false` default |
| `created_at` | `rule_condition.created_at` | ✓ | |

**✗ unmapped:** `comparison_value_numeric` (typed shadow), `resolved_ref_id`,
`unit_code`, `currency_code`, `negated_flag` (folded into the operator),
`tenant_id`, and the whole `rule_condition_group` table — `parent_group_id`,
`group_logic`, group-level `negated_flag`, `label`.

### 2.1 Condition-group flattening

`condition_group` is a plain integer, which can express exactly one shape: an OR
of ANDs. Supported and mapped:

| Platform shape | Target |
| --- | --- |
| One AND group | all conditions → group 1 |
| One OR group | each condition → its own group (1, 2, 3…) |
| Root OR with AND children, no deeper nesting | each child → its own group |
| Sibling AND groups, version logic AND | all conditions → group 1 (AND of ANDs is one flat AND) |
| Sibling AND groups, version logic OR | each group → its own group number |

**Refused — rule skipped and logged:** nesting deeper than two levels; any
negated group; an OR group nested inside an OR root; a root that mixes its own
conditions with subgroups.

### 2.2 Negation

The target has no negation column anywhere. A negated predicate is expressed by
inverting its operator: EQUALS↔NOT_EQUALS, IN↔NOT_IN, EXISTS↔NOT_EXISTS,
GREATER_THAN↔LESS_OR_EQUAL, LESS_THAN↔GREATER_OR_EQUAL.

`STARTS_WITH`, `CONTAINS` and `BETWEEN` have no inverse in the vocabulary. A
negated one **skips the rule** — dropping the NOT would store the exact opposite
of what the author wrote.

### 2.3 Value types

| Platform | Target | Note |
| --- | --- | --- |
| STRING, ENUM, REFERENCE | STRING | REFERENCE stores the catalogue *code*, which is what the platform stores too |
| NUMBER, MONEY | DECIMAL | Target has no money type; the currency survives on `rating_rule.currency_code` |
| BOOLEAN | BOOLEAN | |
| DATE | DATE | |
| DATETIME | TIMESTAMP | |
| LIST | ARRAY | carried as a JSON array in `comparison_value` |
| RANGE | resolved from the attribute's own data type | a BETWEEN over dates and one over money are different target types; defaulting to DECIMAL would mistype date ranges |

---

## 3. `rating_rule_action` ← `ra_rule.rule_action` + `ra_rule.rule_parameter`

**Grain: one target row per action *parameter*.** Both `parameter_name` and
`parameter_value` are NOT NULL, so a platform action with four parameters becomes
four target rows sharing an `action_type`.

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `action_id` | `rule_parameter.rule_parameter_id`, or `rule_action.rule_action_id` when the action has no parameters | ✓ | |
| `rule_id` | `rule_version.rule_version_id` | ✓ | |
| `sequence_no` | single counter across the rule's actions | ⚠ | `uq_rule_action_sequence` is (rule_id, sequence_no); ordered by `(execution_sequence, parameter_name, parameter sequence)` |
| `action_type` | `rule_action.action_type` | ✓ | FK onto `rule_action_type`, seeded first |
| `parameter_name` | `rule_parameter.parameter_name` | ✓ | see fallbacks below |
| `parameter_value` | `rule_parameter.parameter_value` | ✓ | Decimals formatted without exponent |
| `value_type` | `rule_parameter.parameter_value_type` | ⚠ | ARRAY is not permitted here, so a list parameter becomes STRING |
| `created_at` | `rule_parameter.created_at` | ✓ | |

**Fallbacks for an action with no parameter rows** — it must still appear, or the
mirrored rule would match and then do nothing:
1. `action_value` present → `parameter_name` = the action spec's `value_param`
   (else `"value"`), value = `action_value`.
2. Otherwise → `parameter_name` = `"target_attribute"`, value =
   `target_attribute` or the action code.

**Rule skipped** when an action's code is not in the platform registry, or when
its stage is one of the eight the target cannot hold (§1.1) — the same eight that
`vocabulary.py` refuses to register, so the two agree by construction.

**✗ unmapped:** `action_value_numeric`, `resolved_ref_id`, `unit_code`,
per-action `currency_code`, `tenant_id`.

---

## 4. `rule_attribute` ← `app/modules/rules/vocabulary/attributes.py`

Not a table on the platform side — a Python registry. Reconciled at startup.
**41 rows.**

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `attribute_name` | `RuleAttribute.key` | ✓ | |
| `attribute_description` | `.description` or `.label` | ✓ | NOT NULL, so the label is the fallback |
| `data_type` | `.data_type` | ⚠ | NUMBER→DECIMAL, ENUM/REFERENCE→STRING, DATETIME→TIMESTAMP |
| `source_entity` | `.group` | ✓ | the builder's grouping ("Subscriber", "Prepaid"…) |
| `is_runtime_attribute` | — | ⊘ | always `true`: every platform attribute is evaluated against a live event |
| `status` | — | ⊘ | always `ACTIVE` |

**✗ unmapped:** `.specificity` (the scoring weight), `.values` (ENUM value list),
`.reference` (catalogue slug), `ATTRIBUTE_MODE_SCOPE`.

## 5. `rule_operator` ← `RuleStatus`/`Operator` enum + `OPERATORS_BY_TYPE`

**13 rows** — the full operator vocabulary.

| Target column | Source | | |
| --- | --- | :-: | --- |
| `operator_code` | `Operator` member | ✓ | |
| `operator_name` | hand-written label table in `vocabulary.py` | ✓ | NOT NULL; the platform never needed one |
| `description` | — | ⊘ | |
| `supported_data_types` | inverted `OPERATORS_BY_TYPE` | ✓ | comma-separated, target types |
| `status` | — | ⊘ | always `ACTIVE` |

## 6. `rule_action_type` ← `app/modules/rules/vocabulary/actions.py`

**25 of 35 rows.** The other 10 sit at stages the target cannot hold and are
skipped with an `mirror_action_type_skipped` log line: RESERVE_BALANCE,
RELEASE_RESERVATION, ALLOW_PARTIAL_USAGE, ALLOW_NEGATIVE_BALANCE, STOP_SERVICE,
AGGREGATE_USAGE, APPLY_PRORATION, CHECK_CREDIT_LIMIT, ADD_INVOICE_COMPONENT,
ADD_USAGE_CHARGE.

| Target column | Source | | |
| --- | --- | :-: | --- |
| `action_type` | `ActionSpec.code` | ✓ | |
| `action_name` | `.label` | ✓ | |
| `rule_stage` | `.stage_code` through the §1.1 map | ⚠ | |
| `handler_name` | — | ⊘ | the platform's handlers are Python callables, not named strings |
| `description` | `.description` | ✓ | truncated to 500 |
| `status` | — | ⊘ | always `ACTIVE` |

**✗ unmapped:** `.target_attribute`, `.value_param`, `.params` (the whole
parameter specification), `.category`.

---

## 7. `destination_prefix` ← `ra_rule.destination_prefixes` ⋈ `destination_zones`

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `destination_prefix_id` | — | ⊘ | GENERATED ALWAYS AS IDENTITY; never supplied |
| `prefix` | `destination_prefixes.prefix` | ✓ | |
| `zone_code` | `destination_zones.code` | ✓ | denormalised — the target has no zone table |
| `destination_type` | `destination_zones.zone_type` | ✓ | |
| `country_code` | `destination_zones.country_code` | ✓ | |
| `operator_code` | — | ⊘ | no platform column carries a terminating operator |
| `priority` | — | ⊘ | constant 100; the platform resolves by longest prefix, not priority |
| `effective_from` | `destination_prefixes.created_at` | ⚠ | the platform's prefix table is not effective-dated |
| `effective_to` | — | ⊘ | always NULL |
| `status` | `destination_zones.status` | ⚠ | RETIRED folds into INACTIVE |

Write strategy: delete-then-insert on `(prefix, zone_code)`. The router's
`delete_prefix` is a **hard** delete, so it is mirrored as a delete — otherwise
the mirror would keep matching a prefix the platform no longer knows.

**✗ unmapped:** `destination_prefixes.description`, and the whole
`destination_zones` table as an entity.

## 8. `time_band` ← `ra_rule.time_bands`

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `time_band_id` | — | ⊘ | identity column |
| `time_band_code` | `code` | ⚠ | truncated 64 → 50 |
| `time_band_name` | `name` | ✓ | |
| `day_type` | derived from `days` | ⚠ | see below |
| `start_second` / `end_second` | `start_time` / `end_time` | ⚠ | see below |
| `timezone_name` | `timezone` | ✓ | |
| `priority` | `priority` | ✓ | |
| `effective_from` | `created_at` | ⚠ | platform time bands are not effective-dated |
| `effective_to` | — | ⊘ | |
| `status` | `status` | ⚠ | RETIRED → INACTIVE |

**One platform band can become several target rows**, for two independent
reasons:

* `days` is a *list*; `day_type` is a single value. All seven days (or none) →
  `ANY`; Mon–Fri → `WEEKDAY`; Sat+Sun → `WEEKEND`; any other set → **one row per
  day**, so coverage stays exact rather than rounded to the nearest bucket.
* `chk_time_band_seconds` requires `start_second < end_second`, but a platform
  band may wrap midnight (22:00 → 06:00). A wrapping band is **split** into
  `[79200, 86400)` and `[0, 21600)` — the same coverage, expressed the only way
  the target permits.

Write strategy: delete-then-insert on `time_band_code`. The target has no unique
constraint on this table, so replace-by-code is the only idempotent write
available; it is safe because one platform band owns one code.

---

## 9. Subscriber plane — implemented, disabled by default

Both tables FK into `canonical_rating.subscriber`, which **this platform has no
source for**. They are also fed by the rating execution engine rather than by
rule authoring or the metadata catalogue. Mappers, models and write paths are
implemented; they sit behind `MIRROR_SUBSCRIBER_ENABLED`, off by default.

### 9.1 `subscriber_offer` ← `rating.subscriber_products`

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `subscriber_id` | `subscriber_id` | ✓ | |
| `offer_id` | `offer_code` | ⚠ | code, not id — the target's `offer.offer_id` is itself a business code. Rows with no offer code are skipped |
| `status` | derived from `effective_to` | ⚠ | ACTIVE, or EXPIRED once the window has closed |
| `effective_from` / `effective_to` | same | ✓ | inclusive→exclusive as in §1 |
| `priority` | — | ⊘ | constant 100 |

**No runtime writer exists.** `subscriber_products` is only ever read by
enrichment; the only feed is `scripts/mirror_backfill.py --subscriber-offers`.

### 9.2 `subscriber_bundle_balance` ← `rating.balance_buckets`

| Target column | Source | | Notes |
| --- | --- | :-: | --- |
| `subscriber_id` | `subscriber_id` → `owner_key` | ✓ | |
| `bundle_code` | `bundle_code` | ✓ | |
| `balance_type` | `service_type` | ⚠ | no separate balance-type dimension on a platform bucket |
| `initial_balance` | `allocated` | ✓ | Decimal throughout |
| `remaining_balance` | `allocated − consumed`, floored at 0 | ⚠ | `chk_bundle_balance_value` forbids a negative remainder; `overflow` is tracked separately |
| `unit_of_measure` | `quota_unit` | ✓ | |
| `effective_from` / `effective_to` | `period_start` / `period_end` | ✓ | |
| `status` | derived | ⚠ | EXHAUSTED when remaining is 0, EXPIRED past the window, else ACTIVE |
| `last_updated_at` | `last_consumed_at` → `updated_at` | ✓ | |
| `priority` | — | ⊘ | constant 100 |

**✗ unmapped:** `owner_type`, `account_id`, `msisdn`, `shared`, `reset_period`,
`overflow`, `consumption_count`, `source_system`, `attributes`, and the entire
`balance_ledger` — the per-consumption audit trail.

---

## 10. Tables deliberately not written

`product`, `offer`, `offer_product`, `source_system`, `subscriber`,
`msc_usage_event`, `expected_rating_result` are outside the ten named in the
requirement and are never touched. The one consequence is
`rating_rule.source_system_code`, which stays NULL because it FKs into
`source_system`.

---

## 11a. Target-native vocabulary translation (2026-07-31)

The mirror no longer projects the platform's registries into the target's
lookup tables — `canonical_rating` has its **own** reference vocabulary, and
every mirrored value is translated into it
([targetvocab.py](ra_rating_backend/app/modules/mirror/targetvocab.py)):

| Dimension | Platform | Stored in mirror |
| --- | --- | --- |
| Operators | `EQUALS`, `GREATER_THAN`, … | Short codes: `EQ`, `NEQ`, `GT`, `LT`, `GTE`, `LTE`, `IN`, `NOT_IN`, `BETWEEN`, `STARTS_WITH`, `CONTAINS`, `EXISTS`, `NOT_EXISTS` |
| Attributes | `duration_seconds`, `usage_volume`, `bundle`, `offer`, `country_code` | `duration_sec` (INTEGER), `volume_kb` (DECIMAL, **bytes ÷ 1024**), `bundle_code`, `offer_code`, `destination_country` — the operator's 19 rows verbatim; platform-only attributes (`tariff_plan`…) pass through as supplemental rows with `source_entity = NULL` |
| Action types | 15 legacy codes | The operator's **14-action registry with handler names**, seeded verbatim. `SET_RATE` splits: rate/currency stay on `SET_RATE`, unit/per_units move to `SET_RATING_UNIT`. `CONSUME_BUNDLE`→`CONSUME_ALLOWANCE`, `APPLY_ROUNDING`→`SET_ROUNDING`, `SET_CONNECTION_FEE`→`APPLY_FIXED_SURCHARGE`, `SET_ZERO_CHARGE`→`SET_RATE rate=0`, `APPLY_DISCOUNT`/`ADD_SURCHARGE` split percent-vs-fixed by parameter. `SELECT_TARIFF`, `SET_MINIMUM_QUANTITY`, reference-only promotions: **rule skipped and logged** |
| Rule types | `BASE_TARIFF`, `PULSE`, `BUNDLE`, `TAX`, … | The five: `USAGE_RATE`, `TIERED_USAGE_RATE` (when a tiered action is present), `PERCENTAGE_DISCOUNT`, `FREE_UNIT`, `PERCENTAGE_TAX`. Types with no home (`TARIFF_SELECTION`, `SURCHARGE`, `ROUNDING`) pass through verbatim and are logged |
| Match strategy | `EXCLUSIVE`/`OVERRIDE`/`STACKABLE` + `stop_processing` | Exactly two: `FIRST_MATCH` / `ALL_MATCHES` (`BEST_MATCH` no longer written — the target cannot express specificity ranking) |
| Booleans | `true`/`false` | Uppercase `TRUE`/`FALSE`, per the operator's own sample rows |

## 11b. Target vocabulary in the authoring UI (2026-07-31)

The rule-creation UI now offers the operator's vocabulary as first-class,
selectable values, added to the legacy registries (which feed `/meta/attributes`,
`/meta/actions`, `/meta/enums`) with matching canonical entries so the parity
tests hold:

- **Rule types** — `USAGE_RATE`, `TIERED_USAGE_RATE`, `PERCENTAGE_DISCOUNT`,
  `FREE_UNIT`, `PERCENTAGE_TAX`, each an `alias_of` its legacy family
  (BASE_TARIFF, DISCOUNT, BUNDLE, TAX) on the **same stage** — the charging
  sequence is untouched.
- **Actions** — the 10 codes with no legacy spelling: `SET_RATING_UNIT`,
  `SET_ROUNDING`, `CONSUME_ALLOWANCE`, `SET_FREE_QUANTITY`,
  `APPLY_PERCENT_/APPLY_FIXED_` discount/surcharge/tax.
- **Attributes** — `duration_sec`, `volume_kb`, `bundle_code`,
  `bundle_remaining`, `offer_code`, `destination_country`,
  `destination_operator`, `tax_country`, `tax_exempt`. `duration_sec`,
  `volume_kb` and `offer_code` are wired into the engine's fact sheet
  (`resolution.facts_for`) as aliases of the legacy facts — `volume_kb`
  converts bytes→KB. The other six have no CDR-side source yet, matching
  existing attributes like `country_code`.

Rules authored in this vocabulary validate, store in `rafms_db`, and mirror
**verbatim** — a natively-authored `volume_kb GT 100` is not rescaled, unlike
the `usage_volume` (bytes) alias.

**Known limitation, stated plainly:** the expected-charge engine executes the
legacy action codes (`SET_RATE`, `APPLY_DISCOUNT`, …). The 10 new action codes
are authorable, validated, stored and mirrored, but the engine does not yet
compute a charge from them — the same situation as the ~20 canonical-only
actions. For rules that must be *executed* by rating assurance today, use the
legacy action spelling (both appear in the builder). Wiring engine synonyms is
a contained follow-up.

## 11. Cross-cutting losses

| Platform concept | Status |
| --- | --- |
| **Tenancy** (`tenant_id` + enforced RLS on every canonical row) | ✗ No target column. Every tenant's rules land in one undifferentiated set. Mirror one tenant per target database, or add `tenant_id` to the target |
| **Logical rule identity** (`rule.rule_key`) | ✗ No target column |
| **Version lineage** (`supersedes_id`, `behaviour_hash`) | ✗ |
| **Rule sets / dependencies / conflict groups / fallbacks** | ✗ No target tables |
| **Validation issues, audit trail, import lineage** | ✗ No target tables |
| **Typed numeric shadows** (`*_value_numeric`) | ✗ Values reach the target as text |
| **Nested condition logic beyond OR-of-ANDs** | ⚠ Rule skipped, logged |
| **Money type** | ⚠ MONEY→DECIMAL; currency survives at rule level only |
