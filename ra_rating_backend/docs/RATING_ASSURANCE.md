# Rating Assurance

Reads MSC switch records, works out what each call *should* have cost against
the active rule snapshot, compares that with what was actually billed, and
explains every number it produces.

```
MSC record → canonical usage → enriched usage → candidate rules → one resolved plan
           → one expected charge → compared with actual → one assurance result
```

One CDR in, one result out. Always.

---

## 1. Setup

### 1.1 Migrate

```bash
alembic upgrade head          # 0013 adds the assurance layer
```

Migration `0013` is purely additive: six new tables, twenty-three nullable
columns on `cdr_enriched`, and no change to any existing column or table.

> **Note on `alembic revision --autogenerate`.** This service shares a database
> with other systems, and its `search_path` includes `public`. Alembic reflects
> anything reachable through the search path, so before the guard in
> `migrations/env.py` an autogenerate run would emit `op.drop_table` for another
> product's tables (`file_log`, `batch_log`, `quarantine_audit_log`, the AIR
> pipeline logs) into the *upgrade* path. `_include_object` now refuses to emit a
> drop for any table this service does not declare. Keep that guard.

### 1.2 Point at the MSC source

In `.env` (see `.env.example` for the full annotated set):

```ini
MSC_SOURCE_ENABLED=true
MSC_SOURCE_HOST=10.200.37.142
MSC_SOURCE_NAME=rafms_rating
MSC_SOURCE_USER=postgres
MSC_SOURCE_PASSWORD=postgres
MSC_SOURCE_SCHEMA=msc_schema
MSC_SOURCE_TABLES=sm_msc01
MSC_HOME_COUNTRY_CODE=855
```

The connection is opened with `default_transaction_read_only=on`, so Postgres
itself rejects any write against the switch archive.

### 1.3 Seed the worked example (optional)

```bash
python -m scripts.seed_assurance
```

Creates the §28 scenario: one subscriber on SMART20 in six groups, one 195-second
call, and the rules R100 / R200 / R300 / B400 / D500 / T100 / RD10. Idempotent.

Then approve the rules and activate a snapshot (via the rule API, or the
compiler service directly), and the integration tests will run.

---

## 2. Running a batch

```bash
# 1. Pull new switch records into canonical usage
curl -X POST localhost:8000/api/rating/msc/ingest \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \
  -d '{"source_system": "MSC01", "limit": 100000}'

# 2. Rate everything unprocessed
curl -X POST localhost:8000/api/rating/execute \
  -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' \
  -d '{"limit": 1000000, "batch_size": 5000, "reprocess": false}'
```

Both are safe to repeat — see [Idempotency](#5-idempotency).

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/rating/msc/ingest` | Pull MSC records → canonical usage |
| `GET` | `/api/rating/msc/status` | Source reachability, cursor position |
| `POST` | `/api/rating/execute` | Rate unprocessed usage |
| `POST` | `/api/rating/rerate` | Re-rate named `usage_id`s |
| `GET` | `/api/rating/results` | Results, filterable by status/date/msisdn |
| `GET` | `/api/rating/results/{usage_id}` | One verdict |
| `GET` | `/api/rating/results/{usage_id}/explanation` | The whole audit trail |
| `GET` | `/api/rating/usage-exceptions` | Per-record exceptions |
| `POST` | `/api/rating/usage-exceptions/{id}/retry` | Re-rate one failed record |
| `GET` | `/api/rating/meta/assurance-statuses` | Vocabulary for the UI |

Per-record exceptions live under `/usage-exceptions`, not `/exceptions` — the
latter is the existing *grouped* investigation queue and its ids are a different
space.

---

## 3. Worked example (§28)

Seeded by `scripts/seed_assurance.py`, asserted by
`tests/test_rating_execution_integration.py`.

**Input** — `usage_id = MSC01-FILE100-3563`, 195 seconds, `233241234567 →
233501112222`, peak weekday, PREPAID/SMART20, groups `[ACCRA, EMPLOYEE, GOLD,
PREPAID, VOICE_BUNDLE, WEEKEND_PROMOTION]`.

**Rule resolution**

| Rule | Stage | Verdict | Why |
|---|---|---|---|
| `R200_SMART20_OFFNET_PEAK` | BASE_CHARGE | **SELECTED** | highest priority (200 vs 180) |
| `R300_GOLD_CUSTOMER` | BASE_CHARGE | MATCHED_NOT_SELECTED | `LOWER_PRIORITY` |
| `R100_GENERAL_OFFNET` | BASE_CHARGE | MATCHED_NOT_SELECTED | `LOWER_PRIORITY` |
| `B400_VOICE_BUNDLE` | BUNDLE | SELECTED | only rule at this stage |
| `RD10_VOICE_PULSE` | PULSE | SELECTED | only rule at this stage |
| `D500_GOLD_DISCOUNT` | DISCOUNT | SELECTED | only rule at this stage |
| `T100_VOICE_VAT` | TAX | SELECTED | only rule at this stage |

**Calculation**

| # | Component | Result | Detail |
|---|---|---|---|
| 1 | BILLABLE_QUANTITY | 195 | 195 SECOND of raw usage |
| 2 | BUNDLE_DEDUCTION | 75 | B400: 120 of 195 covered |
| 3 | PULSE | 90 | 75s → 2 pulses of 60s/30s |
| 4 | BASE_CHARGE | 0.150000 | 90 / 60 × 0.10 |
| 5 | DISCOUNT | 0.135000 | less 10% |
| 6 | TAX | 0.155250 | plus 15% |
| 7 | ROUNDING | **0.160000** | HALF_UP, 2dp |

Expected charge **0.16**, exactly as the requirement specifies.

Given an actual of `0.60`, the verdict is `OVERCHARGED`, variance `+0.44`.

### The group rule

Six groups produce **one** enriched row, **one** rating context and **one**
result. Groups are aggregated with `ARRAY_AGG ... GROUP BY msisdn` *before* they
meet a usage row and land as a list column; rules match them with
`subscriber_groups CONTAINS GOLD`, which is a set test on one record.

`subscriber_groups` is deliberately **not** part of the rating context key. If it
were, every distinct combination of groups would be its own context and the
collapse from a million CDRs to a few thousand rule decisions — the thing that
makes the batch tractable — would disappear.

---

## 4. The actual charge

**`sm_msc01` has no charged-amount column.** All 137 of its columns were
checked: `charge_level` (`chargeBySecond`), `charged_party` (`callingParty`),
`charge_indicator`, `charge_area_code` are descriptors, never amounts. This is
what a switch record *is*, not a gap in the loader.

So the billed figure has to come from the OCS/IN system. Until
`OCS_SOURCE_ENABLED=true`, every result is `NO_ACTUAL_CHARGE`: the expected
charge is computed and stored, and the comparison says plainly that it has
nothing to compare against. It is never inferred as zero — that would classify
every unbilled call as correctly billed at nothing, which is the exact failure
this platform exists to detect.

Correlation, once a source is configured, is by `call_reference` (the switch's
own handle, an exact key), falling back to subscriber + time + duration within
`ACTUAL_CHARGE_TIME_TOLERANCE_SECONDS`. Statuses: `MATCHED`, `PARTIAL_MATCH`
(time agrees, duration does not — itself a finding), `MULTIPLE_ACTUAL_MATCHES`
(never resolved by picking one), `NO_ACTUAL_CHARGE`.

---

## 5. Idempotency

`usage_id` is derived deterministically from `source_system + source_file +
source_record_number`. It is never a UUID and never regenerated.

Everything keys off that:

| Table | Key | Behaviour on re-run |
|---|---|---|
| `cdr_enriched` | `UNIQUE (usage_id)` | insert becomes a no-op |
| `rating_result_final` | `UNIQUE (usage_id, charge_component)` | upserted in place |
| `calculation_component` | `(usage_id, sequence_number)` | cleared and rewritten |
| `rule_evaluation_audit` | `(usage_id, rule_id)` | cleared and rewritten |
| `rating_usage_exception` | `(usage_id, exception_type)` | upserted |

Audit and component rows are *cleared* rather than merged because `rule_id` is
the compiled rule's id and a new one is minted on every snapshot compile —
upserting alone would leave the previous snapshot's verdicts sitting beside the
current ones, and the explanation would show two contradictory rule sets for one
charge.

Business duplicates (the same call re-exported under a different file name) are
caught separately by `duplicate_hash` over subscriber + called number + start
time + duration + call reference.

### Why `rating_result_final` is a new table

`rating_results` is keyed `(run_id, cdr_enriched_id)` and is deliberately
append-only: the replay module re-rates the same CDRs into a second run so the
two can be compared. Putting a global unique key on it would break that. Per-run
history stays where it is; `rating_result_final` holds the current answer.

---

## 6. Scale

Verified against 35,000 real records from `sm_msc01` at ~2,450 records/second —
about seven minutes for ten lakh.

- Source reads are **keyset-paged** (`WHERE id > :cursor ORDER BY id LIMIT n`),
  not `OFFSET`, which would re-scan and discard every row already returned.
- Reference data is loaded per chunk, scoped to that chunk's subscribers.
- Rules are loaded **once per run**; scope matching runs in memory.
- One transaction per chunk. Nothing spans the batch.
- Bulk inserts are split below PostgreSQL's **32,767 bind-parameter limit**
  (the Bind message counts parameters in a signed 16-bit integer). A 5,000-row
  chunk of a 21-column table asks for 105,000 and is rejected — a failure that
  only appears at production batch sizes. Batch size is computed from the
  *table's* column count, because SQLAlchemy also binds every client-side
  default.

---

## 7. MSC decoding

Switch records are not text. `served_msisdn = 915885519585F0` is TBCD
(3GPP TS 32.005): two digits per octet, **low nibble first**, `0xF` as
terminator, prefixed by a TON/NPI octet. Read naively it is meaningless hex, and
every prefix match and subscriber lookup built on it is wrong in a way that
looks like a data problem rather than a decoding bug.

| Field | Raw | Decoded |
|---|---|---|
| `served_msisdn` | `91589556029009` | `855965200990` |
| `calling_number` | `A19056029009` | `855965200990` (same subscriber, national form) |
| `served_imsi` | `54061625669344F4` | `456061526639444` (MCC 456 Cambodia) |
| `seizure_time` | `1810250101592B0700` | `2018-10-24 18:01:59 UTC` |

The timestamp's sign arrives as the hex of its ASCII byte (`2B` = `+`), because
the field is dumped as hex rather than decoded. Both that and a literal `+`/`-`
are accepted.

### Record kinds

| Kind | Treatment |
|---|---|
| `moCallRecord` | VOICE / MO |
| `mtCallRecord` | VOICE / MT — our subscriber is the *called* party |
| `forwardCallRecord` | VOICE / FORWARDED |
| `moSMSRecord` | SMS / MO — recipient from `destination_number` |
| `mtSMSRecord` | SMS / MT |
| `ssActionRecord` | **skipped** — `NOT_RATEABLE` (supplementary service) |
| `transitRecord` | **skipped** — `NO_SERVED_SUBSCRIBER` |

Skipped records are *counted*, never silently dropped: the source and the
platform must agree on volume, and "40,083 supplementary-service actions" is an
answer while a missing 40,083 is a mystery.

---

## 8. Statuses

**Rating** — `MATCHED`, `OVERCHARGED`, `UNDERCHARGED`, `ZERO_CHARGED`,
`NO_RULE_FOUND`, `NO_ACTUAL_CHARGE`, `ENRICHMENT_FAILED`, `AMBIGUOUS_RULE`,
`CALCULATION_FAILED`

**Enrichment** — `ENRICHED`, `PARTIALLY_ENRICHED`, `SUBSCRIBER_NOT_FOUND`,
`TARIFF_NOT_FOUND`, `PREFIX_NOT_FOUND`, `MULTIPLE_ACTIVE_TARIFFS`,
`INVALID_USAGE_RECORD`

**Rule evaluation** — `CANDIDATE`, `MATCHED`, `REJECTED`,
`MATCHED_NOT_SELECTED`, `SELECTED`, `APPLIED`

`REJECTED` (the rule did not apply) and `MATCHED_NOT_SELECTED` (it applied and
lost) are kept apart deliberately. Collapsing them makes a misconfigured
priority indistinguishable from a correctly narrow condition, and that is the
most common defect in a rule estate.

### Ambiguity is fatal, not tie-broken

When two base-rate rules share priority, specificity *and* version, the engine
selects **nothing** and raises `AMBIGUOUS_RULE_MATCH`. There is no correct
answer to pick, and an arbitrary winner produces a charge that cannot be
defended when the customer disputes it.

---

## 9. Bundles

Bundle balances are read, never written (§18). Assurance over a month of history
must not drain a live customer's minutes, and running it twice must give the
same answer both times.

The consequence is deliberate: within one batch every call of a subscriber is
offered the same starting balance, so a bundle is not depleted across a
sequence. That is correct for *assurance* — each call is checked against the
allowance the billing system had — and wrong for *charging*, which is why the
stateful `BalanceEngine` still owns the charging path.

### Bundle/pulse ordering

`rate_cdr(..., bundle_before_pulse=...)` selects between two defensible
conventions:

| | 100s call, 50s allowance, 60/30 pulse |
|---|---|
| `False` (default) — pulse, then deduct | 120 − 50 = **70s** |
| `True` — deduct, then pulse | pulse(50) = **60s** |

Which is correct is the operator's tariff policy, not a fact. The default
preserves the behaviour every existing caller relies on; the assurance batch
opts in to the second because that is the sequence its requirement specifies.

---

## 10. Tests

```bash
pytest tests/test_msc_decode.py \
       tests/test_msc_canonical.py \
       tests/test_rating_assurance.py \
       tests/test_enrichment_groups.py \
       tests/test_rating_execution_integration.py -q
```

The first four need no database. The integration suite skips cleanly if the
database or the seed is absent.

Covered: TBCD/timestamp decoding against real switch values; deterministic
`usage_id`; every record kind; all fourteen operators; list-valued `CONTAINS`;
priority, specificity and version resolution; ambiguity; exclusive groups and
stackable discounts; pulse boundaries and invalid pulses; discount compounding;
tax ordering; `Decimal` precision; longest-prefix matching; effective dating;
every §29 negative case; the group-fan-out constraint; idempotency; and the
bind-parameter limit.

---

## 11. Logging

Structured, via `structlog`. MSISDN and IMSI are **masked** (`***4567`) — log
aggregation is a far wider audience than the database. Batch runs log
`batch_id`, counts by status and by enrichment status, distinct contexts, rules
loaded and duration.
