# Rating Assurance — UX & Product Enhancement Plan

Derived from the 15-screen target mockup and the product review of 30 Jul 2026.
Scope is unchanged: **everything below renders only when the scope switcher is on
"Rating Assurance"**. No other scope, screen, or shared component is touched.

> **Status (30 Jul 2026): Phases A and B delivered; Phase C partially delivered.**
> Live now: the 8-module navigation, Rating Assurance Overview (real KPIs, trend
> chart, leakage tables, latest runs), Rule Operations Overview (relocated),
> Rating Run Detail (overview / pipeline / statistics / contexts tabs),
> Reconciliation Records explorer (cross-run, filterable, paginated),
> CDR Investigation (details / enrichment / candidate rules / calculation trace),
> Exception Case Detail (impact, affected CDRs, lifecycle, timeline + notes),
> Reconciliation Dashboard, Revenue Leakage, Approval Inbox and Snapshot
> Comparison. Backend additions: `/dashboards/assurance`, `/dashboards/leakage`,
> cross-run `/rating-results` filters, `/exceptions/{id}/comments`, and the
> shared display conventions (`format.ts`: dates, currency, status tones).
> Remaining: Create-Rule/Import wizard restructures (C3/C4), catalogue row
> actions (C6), simulation tabs (C7), Reports (D), Replay & Recovery (E).

## Module-mirror update (30 Jul 2026, second pass)

The navigation now mirrors the main application's module structure so the two
scopes feel like one product:

```text
Dashboard & KPIs             -> assurance overview (live)
Rule Management              -> Rule Operations hub · Catalogue · Create ·
                                Import · Rule Sources · Approvals · Snapshots ·
                                Simulation (all live)
Rating Reconciliation        -> Dashboard · Runs · Records · Leakage (all live)
Pipelines & Job Monitor      -> CDR pipeline monitor (live)
Case Management              -> exception groups + case detail (live)
Metadata Catalogue           -> 13 canonical entities (live)
Reports & Certified Exports  -> Phase D (soon)
Operations                   -> Replay & Recovery, Tolerances, Audit (soon)
System Monitoring            -> NEW: service vitals, DB health, active
                                snapshot, connector health, pipeline jobs
```

**Rule Management is now an end-to-end workflow hub.** Rule Operations opens
with the three ways a rule enters the estate — connect a vendor system
(Ericsson Charging / Oracle BRM / Huawei CBS, fingerprint-based change
detection), upload a file, or build manually with the full condition/action
editor — all converging on the same validate → review → approve → compile →
activate ladder.

**Rule file uploads now accept XML** alongside CSV, TSV, Excel and JSON. The
XML parser takes whatever repeated element a vendor export uses, flattens
attributes and nested children (`rate.currency`), strips namespaces, and feeds
the same mapping/validation pipeline — verified end-to-end: an Ericsson-style
XML tariff auto-mapped 9 of 10 columns and validated cleanly.

---

## 1. The central finding

The review's core criticism is correct and the fix is cheaper than it looks:

> *"The application is stronger as a Rule Management product than as a complete
> Rating Assurance product."*

The imbalance is a **UI problem, not a backend problem**. Auditing the review's
"biggest missing areas" against the API that already runs on :8010:

| Review's missing area                  | Backend today                                                        | UI today |
|----------------------------------------|----------------------------------------------------------------------|----------|
| Reconciliation dashboard               | `dashboards/overview` (partial — needs leakage breakdowns)           | missing  |
| Reconciliation record explorer         | `rating-results` list + filters — **exists**                         | missing  |
| CDR-level investigation                | `rating-results/{id}` + `/rules` + trace, `cdrs/{id}` — **exists**   | missing  |
| Rating run detail + processing trace   | `rating-runs/{id}/summary`, `pipeline/runs/{id}` — **exists**        | partial (pipeline list only) |
| Exception detail and workflow          | `exceptions/{id}` + `/results` + `/transition` — **exists**          | list only |
| Approval screens                       | rule status machine + audit — **exists** (no inbox aggregate)        | missing  |
| Snapshot comparison                    | `rule-snapshots/diff` — **exists**                                   | missing  |
| Expected-vs-billed reports             | data exists in `rating_results`                                      | missing (both) |
| Replay & recovery                      | `rewind_run()` engine exists; no replay API                          | missing (both) |
| Import mapping/validation workflow     | `rule-imports/preview` + templates — **exists**                      | single-page, not a wizard |

So the plan is dominated by **new screens over existing endpoints** (fast,
low-risk), with three genuinely new backend modules: **reports, replay, and
dashboard aggregations**.

---

## 2. Navigation restructure (the review's §3)

Replace the current flat `RATING_NAV` with the reviewed 8-module tree. This is
a change to `src/lib/rating/nav.ts` **only** — the sidebar swap mechanism and
every non-rating scope stay byte-identical.

```text
1. Rating Overview                        (assurance KPIs — reworked, §3 below)
2. Rating Reconciliation
   ├── Reconciliation Dashboard           NEW UI · extend existing endpoint
   ├── Rating Runs                        exists (list) + NEW run-detail screen
   ├── Reconciliation Records             NEW UI · endpoint exists
   ├── Revenue Leakage                    NEW UI · NEW aggregation endpoint
   └── Reconciliation Reports             NEW (both)
3. Rule Management
   ├── Rule Operations Overview           NEW UI (relocated current overview)
   ├── Rule Catalogue                     exists · enhance columns/actions
   ├── Create Rule                        exists · restructure into wizard steps
   ├── Import Rules                       exists · restructure into wizard
   ├── Approvals                          NEW UI · thin NEW inbox endpoint
   ├── Snapshots                          exists · add diff/compare UI
   ├── Rule Simulation                    exists · add candidate/rejected tabs
   └── Rule Sources                       exists (rule-source connectors)
4. Metadata Catalogue                     exists · regroup tabs into 4 sections
5. Exceptions & Cases
   ├── Exception Groups                   exists (list)
   ├── Investigation Cases                NEW UI · endpoint exists (detail)
   ├── Root Causes                        NEW UI · derived from existing data
   └── Revenue Recovery                   Phase E (needs replay)
6. Replay & Recovery                      NEW (both) — Phase E
7. Reports                                NEW (both) — Phase D
8. Administration
   ├── CDR Source Systems                 exists (split from Rule Sources)
   ├── Users & Roles                      exists (rating RBAC read view)
   ├── Tolerance Policies                 NEW (small)
   └── Audit Logs                         exists (rule audit; surface it)
```

Key relocation per the review's §4: the connectors screen splits into **Rule
Sources** (under Rule Management) and **CDR Source Systems** (under
Administration). One screen, two filtered views — the `connector-catalog`
already carries the vendor type to filter on.

---

## 3. Two overviews (the review's §2)

**The single most important change.** The current `/rating` index is a rule
dashboard; the review is right that it answers "how are my rules?" not "am I
leaking revenue?".

- **Rating Assurance Overview** (new `/rating` index, mockup screen 1):
  KPI cards — total CDRs, expected vs billed revenue, match %, undercharge,
  overcharge, unrated, revenue at risk. Revenue trend chart, exception donut,
  top-5 products/rules by leakage, latest runs table. Backend: extend
  `dashboards/overview` with the leakage-by-product/rule aggregations
  (single GROUP BY queries over `rating_results`).
- **Rule Operations Overview** (current screen, moved under Rule Management):
  rule counts by status, awaiting approval, expiring rules, active snapshot,
  metadata readiness. Almost zero rework — it already is this screen.

---

## 4. Phased delivery

Ordered by the review's own priorities (§19). Each phase is shippable alone.

### Phase A — Core reconciliation workflow  *(review Priority 1 — build first)*

The operational chain the review calls "the essential missing chain":
run → records → investigation → exception.

| # | Deliverable | Mockup | Work |
|---|-------------|--------|------|
| A1 | Navigation restructure + route stubs | all | nav.ts rewrite; disabled rows for later phases |
| A2 | Rating Assurance Overview | 1 | new screen + extend `dashboards/overview` with leakage breakdowns and trend series |
| A3 | Rating Run Detail | 3 | new `/rating/runs/$runId` — tabs: Overview, Pipeline (per-stage in/out/rejected/duration/retry from `pipeline/runs`), Statistics, Errors, Contexts. Revenue summary strip |
| A4 | Reconciliation Records explorer | 4 | new screen over `rating-results` — one row per rated CDR: expected, billed, variance, status, root cause. Status/service/product/run filters, sticky header, CSV export |
| A5 | CDR Investigation | 5, 6 | new `/rating/cdrs/$resultId` — tabs: CDR Details, Enrichment, Rating Context, Candidate Rules (with rejection reasons from `/rules`), Calculation Trace (stage/rule/detail/amount, per mockup 6), Exception, Audit |
| A6 | Exception Case Detail | 10 | new `/rating/exceptions/$id` — summary, revenue impact (under/over/total), affected CDRs tab (drill to A5), root cause, lifecycle transitions via existing endpoint, comments |

Backend work in A: aggregation additions to the dashboards module; an
`exceptions/{id}/comments` table+endpoint (small). Everything else reads
existing APIs.

### Phase B — Reconciliation dashboard & leakage  *(Priority 1, second half)*

| # | Deliverable | Work |
|---|-------------|------|
| B1 | Reconciliation Dashboard | dedicated screen: the full KPI grid + expected-vs-billed trend, match-rate trend, leakage by product/service/zone/rule/source, with date/run/snapshot/product filters |
| B2 | Revenue Leakage screen | mockup 15's table: per-product CDRs, expected, billed, under, over, net leakage; drill into records pre-filtered |
| B3 | Backend: `dashboards/reconciliation` + `dashboards/leakage` | set-based GROUP BY queries; date-bucketed series; all read-only over `rating_results` |

### Phase C — Rule governance completion  *(review Priority 2)*

| # | Deliverable | Mockup | Work |
|---|-------------|--------|------|
| C1 | Approval Inbox | 13 | new screen: pending rule/import/snapshot approvals with impact + validation status; approve / reject / return actions on the existing status machine. Thin new `approvals` aggregate endpoint |
| C2 | Rule Detail tabs | — | extend `$ruleId` to the reviewed tab set: Overview, Conditions, Actions, Versions, Simulation, Validation, Audit |
| C3 | Create Rule wizard | 7, 8 | restructure the existing form into the 6 steps (Basic → Conditions → Actions → Behaviour → Validate & test → Review) with specificity preview and human-readable summary. Same API |
| C4 | Import wizard | 9 | Upload → **Map columns** (source col, example value, target, transform, status) → Validate (valid/warn/fail, CREATE/UPDATE/UNCHANGED counts) → Review → Commit as drafts. `preview` endpoint already returns this data |
| C5 | Snapshot Comparison UI | 14 | new/changed/retired rules, per-rule change description — `rule-snapshots/diff` already computes it |
| C6 | Catalogue enhancements | — | add product/offer/plan/owner/effective-to/validation columns, row actions (clone, simulate, submit, retire), bulk select |
| C7 | Simulation enhancements | — | candidate + rejected-rules tabs (rejection reasons), snapshot-vs-snapshot compare (endpoint exists), save test case |

### Phase D — Reports  *(review §6)*

| # | Deliverable | Work |
|---|-------------|------|
| D1 | Backend `reports` module | report registry + generator: each report is a named, parameterised query over `rating_results`/`rating_runs`/`rating_exceptions`, rendered to CSV/XLSX and stored with an audit row |
| D2 | Report Catalogue UI | mockup 11: card per standard report with format buttons + schedule. Ship the review's top set first: Daily Reconciliation Summary, Expected-vs-Billed, Undercharge, Overcharge, No-Matching-Rule, Product Leakage, Rule Leakage, Tax Reconciliation, Exception Ageing |
| D3 | Scheduling | cron-style schedule rows executed by the existing background runner (Airflow DAG variant included, same pattern as the pipeline DAG) |

### Phase E — Replay & recovery  *(review §16, Priority 3)*

| # | Deliverable | Work |
|---|-------------|------|
| E1 | Backend `replay` module | replay request (by run / batch / exception group / date+product), executes: `rewind_run` balances → re-rate against a chosen snapshot → **old-vs-new comparison** (variance before/after, exceptions remaining, revenue recovered). The run model already supports `run_type=REPLAY` + `replay_of_run_id` |
| E2 | Replay UI | request form, replay run list, and the comparison screen (original vs new expected, recovered revenue, new problems introduced) |
| E3 | Revenue Recovery view | recovered-revenue rollup per exception/case; closes the loop on Exception Detail's "READY FOR REPLAY → REPROCESSED → RESOLVED" lifecycle states (add the missing states to the existing transition map) |

### Phase F — Visual & consistency pass  *(review §17 — applied progressively, enforced here)*

- **Dates**: display `30 Jul 2026` everywhere; ISO only in exports.
- **Currency**: always show the symbol (`£21.44`), never a bare number for money.
- **Status colours**: green = matched/active, amber = pending/warning, red =
  failed/overcharge, blue = investigating, grey = draft/inactive — one shared
  `statusTone()` helper in `src/lib/rating/` so it cannot drift.
- **Tables**: sticky headers + filter rows, column chooser, pagination on
  Records/Catalogue/Reports.
- **Empty states**: every list gets an actionable empty state ("No rating runs
  yet — upload a CDR batch to begin").
- All of this lives in the rating components only; the shared design system is
  not modified.

---

## 5. What is deliberately *not* changed

- The sidebar swap mechanism, header, scope switcher, and every non-rating
  screen — untouched, as before.
- The backend isolation contract (separate process :8010, `rating` schema,
  read-only identity engine) — all new modules follow it.
- Existing endpoint shapes — new screens consume them as-is; additions are new
  endpoints, not breaking changes.

## 6. Sequencing note

Phase 6 (stateful rating: bundles/tiers/balances) is mid-flight in the backend
and completes independently; its **Balances screen** slots into the new nav
under *Rating Reconciliation* once Phase A's structure exists. Phases A–C are
pure product-completion work and can start immediately; D and E depend on
nothing outside themselves.
