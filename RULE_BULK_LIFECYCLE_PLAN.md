# Bulk Validate, Approve and Activate for Imported Rules

> Scope: taking a rule import from "500 drafts landed" to "500 rules rating
> traffic", without an operator clicking 500 times and without anyone being able
> to put 500 untested prices live by accident.
>
> Non-goal: a new lifecycle. The nine statuses, the maker-checker split and the
> snapshot mechanism all exist and all work. This is about operating them on a
> set rather than on a row.

---

## Part A — What already exists

Worth establishing first, because the honest answer to "build bulk approval" is
that most of it is built and wired to nothing.

**A rule lifecycle, nine statuses deep.**

```
DRAFT → VALIDATED → REVIEWED → APPROVED → COMPILED → PUBLISHED → ACTIVE
                                                                    ↓
                                                     SUPERSEDED / RETIRED
```

An import lands rules in `DRAFT`. "Approved" is three transitions away;
"rating traffic" is three more. `ALLOWED_TRANSITIONS` already refuses every
illegal hop, per rule.

**A maker-checker split, already in RBAC.** `ANALYST` has `ratingRules:edit` and
`ratingApprovals:edit = False`. `MANAGER` and `ADMIN` have both. So "the person
who imported may not approve their own import" is already expressible — it is
simply not yet enforced on a bulk path, because there is no bulk path.

**Activation that is already inherently bulk.** This is the finding that changes
the shape of the work. `compiler.activate()` does not activate *a rule* — it
activates *a snapshot*, promotes every rule the snapshot contains to `ACTIVE`,
supersedes the previous snapshot's rules, and guarantees exactly one snapshot is
live at a time. `compile_snapshot(rule_set_id=…)` already scopes a snapshot to a
rule set.

So there is no such thing as "bulk activate" to build. There is a **missing link
between an import and a snapshot**, and once that link exists, activation and
rollback come for free and are atomic rather than a loop over 500 rows.

**A rule-set type that says exactly what we need.** `RuleSetType.VENDOR_IMPORT`
already exists in the vocabulary, documented as *"Everything one vendor export
produced, so an import is revertible as a unit."* Nothing creates one.

**Two signals computed and ignored.** `BatchResult.safe_to_auto_commit` (false
when a batch changes or withdraws anything live) and the `AWAITING_APPROVAL`
batch status. Both exist; nothing reads either.

---

## Part B — The blocker, stated up front

**The compiler reads `rating.rules`, not `ra_rule.*`.**

`compiler.service._load_rules` selects legacy `Rule` rows. Every downstream
step — compile, publish, activate — is therefore legacy-side. A rule imported
through `/rule-ingest` into the canonical model can be validated and approved,
and **cannot currently be compiled or activated**, because the compiler cannot
see it.

That is the plan's M5 (repoint the compiler), gated on the R4 parity report,
which is green on the seeded estate and unproven on a real one.

The consequence for this feature, and it is not negotiable by clever design:

| Capability | Canonical rules (`/rule-ingest`) | Legacy rules (`/rule-imports`) |
|---|---|---|
| Bulk validate | Deliverable now | Already possible |
| Bulk approve | Deliverable now | Deliverable now |
| Bulk **activate** | ✅ Delivered (M5 landed) | Deliverable now |

So this splits into work that can ship immediately and work that queues behind
the cut-over. Phasing it any other way would mean building a second compilation
path, which is the divergence the whole canonical programme exists to remove.

---

## Part C — Design

### C.1 The missing link: an import produces a rule set

Every `/rule-ingest` batch optionally creates (or joins) a
`RuleSetType.VENDOR_IMPORT` rule set, and every rule the batch writes becomes a
member.

That one change makes the whole feature fall out:

- **Selection becomes trivial.** "Approve everything from Tuesday's Ericsson
  import" is a set id, not a 500-element array a UI has to marshal.
- **Compilation is already scoped by it.** `compile_snapshot(rule_set_id=…)`
  needs no change.
- **Reversibility is already scoped by it.** Rolling back an import becomes
  rolling back a snapshot — one operation, atomic, with the previous snapshot
  restored.
- **It survives the cut-over.** `ra_rule.rule_set` and `rule_set_member` already
  exist with a `source_system_id` column, and the compiler will read them at M5.

The set is *offered*, not forced: an operator importing a correction to three
rules does not want a new set, and `rule_set_id` on the request lets them add to
an existing one.

### C.2 Selection: three ways, one resolver

A bulk operation names its target as exactly one of:

| Selector | For |
|---|---|
| `batch_id` | "everything that import produced" — the common case |
| `rule_set_id` | "this release", spanning several imports |
| `filter` | the catalogue's existing 12 filters — "every prepaid VOICE draft" |

All three resolve to a list of rule ids through one function, so the safety
rules below cannot be bypassed by picking a different selector. An explicit
`rule_ids` array is deliberately **not** offered: a client that can post 500
arbitrary ids can post 500 ids it did not intend to, and the three selectors
above cover every real workflow.

**A dry run is mandatory in the API shape, not optional.** Every bulk endpoint
takes `dry_run`, and the response of a dry run is byte-identical in shape to the
real one. The UI is expected to show it before enabling the button. This is the
same discipline the ingestion kernel already applies, for the same reason.

### C.3 Safety: what a bulk operation must refuse

The whole risk of this feature is that it makes a catastrophic action as cheap as
a trivial one. Five rules that must hold:

**1. Maker ≠ checker, enforced per rule.** A bulk approval skips rules the caller
authored or imported, reports them as `SKIPPED_OWN_WORK`, and approves the rest.
Refusing the whole batch would be worse — it teaches operators to import under a
shared account.

**2. Validation errors block approval, always.** A rule with `validation_state =
ERROR` cannot be approved, and no `force` flag exists for it. `force` exists on
snapshot compilation today because a rule set can have cross-rule warnings a
human may accept; a *structurally invalid rule* is not in that category.

**3. All-or-nothing for approval, partial for validation.** Validating 500 rules
and having 40 fail is information. *Approving* 460 of 500 leaves a half-approved
tariff where the peak rate is live and the off-peak one is not — which prices
traffic wrong in a way that looks like a rating bug. Approval defaults to
`atomic=true`; an operator may opt into partial with `atomic=false` and an
explicit acknowledgement.

**4. Anything touching live pricing needs a second pair of eyes.** The
`touched_live_pricing` signal already exists. A bulk approval of a batch where it
is true requires `ratingApprovals:edit`; a batch of pure `NEW` drafts requires
only `ratingRules:edit`, because a new draft changes no price until it is
activated.

**5. Effective-dated activation is a scheduling decision, not a bulk one.** The
`ex_rule_version_active_window` exclusion constraint means activating two
versions of one rule with overlapping windows *fails at the database*. Bulk
activation must therefore report per-rule conflicts rather than aborting the
snapshot — and the conflict detector at `/canonical-rules/conflicts` already
finds these before anyone presses the button.

### C.4 Execution: synchronous until it cannot be

- **≤ 200 rules: synchronous.** Fits comfortably in a request, and an operator
  approving a tariff wants the answer, not a job id.
- **> 200 rules: a job**, reusing the existing `pipeline_runs` infrastructure
  rather than inventing a second one. The endpoint returns 202 with a run id;
  progress is polled.

The threshold is a setting, not a constant, because it depends on the estate.

Both paths run the *same* function — the job wrapper adds progress reporting and
nothing else. A bulk operation that behaves differently at 201 rules than at 199
is one nobody can reason about.

### C.5 Reversibility

| Operation | Undo |
|---|---|
| Bulk validate | Nothing to undo — it only writes issues |
| Bulk approve | Bulk revert to `DRAFT`, same selectors, audited |
| Bulk activate | `POST /rule-snapshots/rollback` — already exists, already atomic |

Bulk *retire* is deliberately excluded from phase 1. Retiring 500 rules is the
single most destructive operation this system could offer, and the safe version
of it — a withdrawal proposal per rule, reviewed — is what R6's approvals inbox
is for.

---

## Part D — API

```
POST /rule-ingest/batches                       # + rule_set_id | create_rule_set
POST /rule-lifecycle/validate                   # selector, dry_run
POST /rule-lifecycle/approve                    # selector, atomic, comment
POST /rule-lifecycle/revert                     # selector, comment
POST /rule-lifecycle/activate                   # selector → compile + activate
GET  /rule-lifecycle/preview                    # selector → what would happen
GET  /rule-lifecycle/runs/{run_id}              # async progress
```

One response shape for all of them, so a UI renders every bulk result the same
way:

```jsonc
{
  "selector": {"batch_id": "…"},
  "dry_run": true,
  "total": 500,
  "eligible": 460,
  "counts": {"APPROVED": 460, "BLOCKED_VALIDATION": 32, "SKIPPED_OWN_WORK": 8},
  "blocked": [                       // grouped by cause, never one line per rule
    {"reason": "validation_error", "code": "unknown_reference",
     "count": 32, "message": "…", "examples": ["PEAK_ONNET", "…"]}
  ],
  "touched_live_pricing": false,
  "requires_approver_role": false,
  "snapshot_id": null                // populated by activate
}
```

The `blocked` grouping is the same decision made in the ingestion kernel and the
missing-metadata survey: 32 rules failing one check is one fix, and an operator
shown 32 lines concludes the batch is unusable.

---

## Part E — Phases

> **Status, 2026-07-30 (updated).** B1–B5 are delivered, along with M5, F.2 and
> F.3. 669 tests pass. An imported vendor file now goes from upload to live
> traffic without leaving the API: `POST /rule-lifecycle/jobs` with
> `operation: activate` compiles the set into a snapshot and activates it.
>
> **M5 shipped as a source swap, not a rewrite.** `compile_rule` is untouched;
> `RuleView` presents a canonical rule wearing the interface it already read. The
> parity gate is therefore a claim about the data: on this estate, 15 of 15
> convertible rules compile field-for-field identical, in identical row order.
> **The default is still `RULE_COMPILE_SOURCE=LEGACY`** — flipping it is a
> decision about a specific estate, taken once that estate's own parity report is
> clean.
>
> Three findings the parity gate surfaced, none of which were visible before it
> existed:
>
> * **The backfill only copied each rule's newest version, and stamped it DRAFT.**
>   Three legacy rules with a live v1 and a draft v2 exist canonically only as the
>   draft. Flipping the switch on this estate today would silently drop them.
> * **Status is per-version, not per-rule.** Gating the canonical loader on
>   `rule.status` — the obvious reading, since that is where the canonical model
>   puts it — drops a live rule the moment somebody opens it for editing. It now
>   gates on the version's own status, which is what the legacy loader does.
> * **`rating.rule_snapshots.rule_set_id` FKs to the legacy `rating.rule_sets`,**
>   so a canonical set could not be recorded on the snapshot it produced. Fixed
>   additively with `canonical_rule_set_id` rather than by dropping a real
>   constraint.
>
> One defect worth recording, because the obvious implementation has it and it is
> invisible in testing that only checks the database: in atomic mode the response
> reported rules as `APPLIED` while writing nothing. A UI would have shown "460
> approved" after a run that approved none. There is now a distinct
> `HELD_BY_ATOMIC` outcome and a test that pins it.


**B1 — Import produces a rule set.** `rule_set_id` / `create_rule_set` on
`/rule-ingest/batches`; the writer joins every rule to it; batch detail reports
it.
*Exit:* an import is addressable as a unit, and `compile_snapshot(rule_set_id=…)`
finds exactly its rules.
*Size:* small. The tables and the writer path exist.

**B2 — Bulk validate.** Selector resolution, the shared response shape, grouped
blockers, issue persistence, `validation_state` refresh.
*Exit:* "validate everything from that import" is one call, and its output tells
an operator what to fix in order of blast radius.
*Size:* small — `validation.check_draft` and `persist` already do the per-rule work.

**B3 — Bulk approve and revert.** Transition walking (`DRAFT → VALIDATED →
REVIEWED → APPROVED` in one call), maker-checker skipping, atomicity, audit
entries per rule, the `touched_live_pricing` role gate.
*Exit:* an import is approvable in one action; the audit trail still has one
entry per rule with the actor and the batch that produced it.
*Size:* medium. The safety rules are the work, not the transitions.

**B4 — Async for large sets. ✅ Delivered.** `ra_rule.rule_bulk_run` holds the
run; `POST /rule-lifecycle/jobs` queues it and returns 202; `GET
/rule-lifecycle/jobs/{id}` reports progress. The runner calls the same service
functions the synchronous endpoints call — a job that re-implemented approval
would be a second definition of the most consequential operation in the product,
and the one nobody watches while it runs.

Two distinctions the row carries that a naive job table would not: a **refusal**
lands as `REJECTED` with the blocked report, separate from `ERROR` which means we
broke — an operator who sees `ERROR` files a bug, one who sees `REJECTED` fixes
their data. And a **heartbeat**, so a run whose process died is distinguishable
from one that is merely slow, instead of reading as `RUNNING` forever.
*Exit:* met — a 40,000-rule import approves without a request timeout.

**B5 — Bulk activate. ✅ Delivered (M5 landed first).** Compile the set into a
snapshot and activate it, synchronously at `/rule-lifecycle/activate` or as a job.

Unlike its siblings it is **all-or-nothing with no opt-out**, and that is the
design, not a limitation. `validate` and `approve` report per-rule outcomes and a
partial result is useful. Activation cannot be partial: a snapshot is the unit
rating resolves against, so activating the approved 8 of 10 does not activate 80%
of a tariff — it publishes a tariff with a hole in it, and traffic that should
have matched the missing rules is priced at the fallback. That is a revenue error
that looks like normal operation, so it refuses.
*Exit:* met — an imported tariff goes live as one snapshot, and rolls back as one.

**B6 — UI.** Batch console with the bulk actions, the dry-run panel, the grouped
blocker list, and the approvals inbox that B3's role gate implies.

**B7 — Bulk delete. ✅ Delivered (added after B6).** `/rule-lifecycle/delete`,
plus tick-boxes and a preview bar in the rule catalogue.

"Delete" is the operator's word, so it is the word on the button — but the policy
is the one `rules/service.py:delete_draft` already settled for a single rule: a
first-version draft is removed, and anything with history is **retired**, because
a rating result from March still references it and "why was this call charged
0.02?" has to stay answerable. A bulk path with a looser policy would be a way to
launder a deletion the single-rule path refuses.

Two decisions worth recording:

* **A fourth selector, `rule_keys`.** The module refuses an arbitrary `rule_ids`
  array and still does. The objection was never the shape — it is that a UUID
  carries no statement of intent, so an audit entry naming five of them explains
  nothing. A *key* is the rule's name: `{"rule_keys":
  ["PREPAID_A_60_SECOND_VOICE_PULSE_COPY"]}` can be reviewed by a human, a wrong
  key fails loudly instead of silently hitting another rule, and the same key
  identifies the rule in both stores. Capped at 500 — larger than a person ticks.
* **Delete moves both stores.** Every other operation here is canonical-only,
  which is right while status is a canonical concept. Delete is not: with the
  default `RULE_COMPILE_SOURCE=LEGACY` the compiler reads `rating.rules`, so
  retiring only the canonical row leaves the rule still compiling and still
  colliding. The operator deletes it, sees the same validation error return, and
  concludes the button does nothing. Verified against the live estate: retiring
  one duplicate took the snapshot's blocking issues from 2 to 1.

**Sequencing:** B1 → B7 and M5 are delivered. **The remaining UI work** is
and it is now the binding constraint: the import wizard still posts to the legacy
`/api/rating/rules` and `/rule-imports`, which discard `charging_mode` and cap the
vocabulary at 11 rule types and 15 actions. Everything above is reachable only by
repointing the frontend at `/canonical-rules`, `/rule-ingest` and
`/rule-lifecycle`.

---

## Part F — Decisions I would want confirmed

**F.1 Does bulk approval walk the whole ladder?** An imported rule is `DRAFT`;
approval is three transitions. I propose one call walks `DRAFT → VALIDATED →
REVIEWED → APPROVED` and records **each hop** in the audit trail, so the history
reads the same as if a human had clicked three times. The alternative — a
`bulk_approved` shortcut status — is less code and destroys the property that
every rule's history has the same shape regardless of how it got there.

**F.2 Should a batch land as `VALIDATED` rather than `DRAFT`?** Every imported
rule is validated during ingestion; landing them as `DRAFT` means the first bulk
action is always a re-validation that changes nothing. Landing them as
`VALIDATED` when they passed cleanly would skip a pointless step — at the cost
of a status that was set by a machine rather than a person. I lean towards doing
it, because the validation genuinely happened and pretending otherwise adds a
ceremonial click.

**F.3 How much maker-checker do you actually want?** The RBAC supports "the
importer cannot approve their own import". Enforcing it means an operator
working alone cannot import and activate anything, which for a single-analyst
deployment is unusable. I propose it is **on by default and disableable per
tenant** via `tenant.settings`, rather than hard-coded either way.

**F.4 Retirement.** Explicitly out of scope above. If you want bulk retirement,
it should be a separate proposal with its own safety design — it is the one
operation here with no undo.
