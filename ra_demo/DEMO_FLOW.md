# RADONaix — Demo Flow

A click-by-click runbook for the live demo. **You** drive the screen; **your
manager** narrates from the "Say" column. ~20 minutes.

> Location: this file is kept out of the public GitHub repo on purpose.

**The story arc:** see the numbers → see the evidence → see how the data is
produced → act on a finding → configure the platform → monitor it → govern it.

---

## Before you start (2-min pre-flight)

- [ ] Open `https://10.200.37.142:8443/` and log in **once** so the session is warm.
- [ ] Confirm the **Dashboard** panel loads (see caution below). If it's blank,
      plan to open with **Reports** instead and mention the dashboard verbally.
- [ ] Confirm **System Monitoring** shows a live Grafana panel.
- [ ] Set the theme you want (sun/moon icon, top-right) and close DevTools.
- [ ] Log in as a normal analyst if possible — avoids accidental admin writes.

**Three things your manager should know (not for the audience):**
1. **Reports, Pipelines, Downloads, System Monitoring, Users/Roles/Audit are
   live** — real data from the platform.
2. **Data Sources, Recon Workflows, Database Connections, Case Management are
   configuration/illustrative surfaces** — they show the product's intended
   workflow. Narrate them as capability, not as "this just wrote to the database."
3. **Do NOT create/delete a user or change System Configuration during the demo** —
   those write to the real platform.

---

## 1 · Sign in & orientation  ·  *live*  ·  1 min

- **Click:** land on the dashboard after login.
- **Show:** the left module rail, and the **Assurance Scope** selector top-centre
  (Mediation / Usage / Billing / Rating / Roaming).
- **Say:** "One platform for revenue assurance across every stream. The scope
  selector lets a team focus on one assurance domain at a time."

## 2 · Dashboard & KPIs  ·  *live*  ·  2 min

- **Click:** Dashboard & KPIs (top of the rail).
- **Show:** the embedded analytics — headline KPIs and trends.
- **Say:** "The executive view — match rates, leakage, throughput at a glance,
  powered by the live analytics layer."
- **⚠ Caution:** this embeds Superset. If it shows *"Couldn't load the embedded
  dashboard,"* skip it and open with Reports — that's the mixed-content issue we
  noted, not a data problem.

## 3 · Reports & Certified Exports  ·  *live*  ·  4 min  ·  **anchor**

- **Click:** Reports & Certified Exports → the catalog expands → **AIR
  Reconciliation Report**.
- **Show:** the reconciliation table — statuses **MATCHED / AMOUNT_MISMATCH /
  RAW_ONLY / PROC_ONLY**. Use a filter; sort a column.
- **Click:** **Export CSV** → then open the **Download Centre** (top-right tray).
- **Say:** "This is real reconciliation output — every raw CDR matched against its
  processed counterpart, mismatches and gaps surfaced. Any view becomes a
  certified export, tracked in the Download Centre — resumable even for very
  large files."
- **Also worth a click:** Record Sequence Check and File Sequence Check — "we
  catch missing records and missing files, not just amount mismatches."

## 4 · Pipelines & Job Monitor  ·  *live*  ·  3 min

- **Click:** Pipelines & Job Monitor.
- **Show:** pipeline stages, recent runs, KPIs; expand a run to see its stages.
- **Say:** "This is how the data in those reports gets produced — ingest, decode,
  mediate, reconcile — with per-stage health and alerts. If a job fails, an
  analyst sees it here."
- *(Point out retry/replay controls — don't click them.)*

## 5 · Case Management  ·  *illustrative*  ·  5 min  ·  **highlight**

- **Click:** Case Management. Stay on the **Cases** tab.
- **Show:** the count tiles — **Unassigned / Assigned / In Progress / Closed**.
  Click **Unassigned** to filter, then **Assign to me** on that row.
- **Say:** "Findings from those reports become trackable cases. A lead triages the
  queue by these tiles and picks up work."
- **Click:** **Investigate** on any case → the investigation view opens.
- **Show, top to bottom:** Case Summary (linked batch, revenue at risk) →
  **AI Impact Insight** (confidence + revenue at risk + signals) → **Record
  Trace** (the exact records behind the finding) → **Update Case** (change status
  / priority / action and save).
- **Click:** in the assistant on the right, **Analyze linked records**.
- **Say:** "Everything for the investigation in one place — the impacted records,
  the estimated exposure, and an assistant that summarises the pattern. The
  analyst resolves and updates the case without leaving the screen."
- **Click:** the **Self Assigned Cases** tab → note the tiles switch to the status
  lifecycle (Open / In Progress / Resolved / Closed).
- **⚠ Presenter note:** the assistant returns prepared analysis for the demo
  scenario — frame it as "the assistant summarises the case," not as a live model
  answering arbitrary questions.

## 6 · Operations — Data Sources & Recon Workflows  ·  *illustrative*  ·  3 min

- **Click:** Operations → **Data Sources**.
- **Show:** the connected feeds (AIR, SDP, MSC…), each pointing at a database
  connection; the summary tiles.
- **Say:** "Onboarding a new stream is a guided step — pick its type, use case and
  the database it reads from."
- **Click:** Operations → **Recon Workflows** → expand the **AIR_vs_SDP_CDR** row.
- **Show:** the **Comparison Metric** and **Comparison Keys** it reconciles on.
- **Say:** "The reconciliation rules themselves are configurable — which tables to
  compare, on which keys, measuring which values."

## 7 · System Monitoring  ·  *live*  ·  2 min

- **Click:** System Monitoring → **Applications** (then peek at Databases).
- **Show:** the live Grafana panels — CPU, memory, host health per server.
- **Say:** "Platform health is built in — every app and database server monitored
  in the same pane, no separate tool to open."

## 8 · Administration  ·  *live (read-only in the demo)*  ·  1 min

- **Click:** the **gear icon** (top-right) → **Roles**, then **Audit Logs**.
- **Show:** role-based permissions per module; the audit trail of who did what.
- **Say:** "Full RBAC and a complete audit trail — enterprise governance out of
  the box."
- **⚠ Do not** add/edit users or change System Configuration — live writes.

---

## Close  ·  30 sec

"So — from a leaking transaction all the way to a resolved, audited case:
reconciliation on real data, certified exports, pipeline and platform monitoring,
and case management, in one governed platform."

## If something misbehaves

| Symptom | Likely cause | On-the-fly line |
|---|---|---|
| Dashboard panel blank | Superset embed (mixed content) | "Let's go straight to the reconciliation reports." |
| A Grafana tab says "not configured" | that host isn't wired yet | "That server comes online in the next rollout." |
| A demo module looks reset | browser storage cleared | harmless — just re-seeds; carry on |
| Anything 401s / logs out | session expired | re-login (pre-flight avoids this) |
