"""The control-rule catalog loaded into `assurance.control_rules` on first boot.

These are the rules the Rule Explorer lists and the engine executes. Case
seeding derives from this list, so every demo case traces back to a real rule
id, category and entity scope rather than to invented metadata.

Each entry: (id, name, category, entity scope, severity, frequency, last status,
source feed, target feed, tolerance %, intent).
"""

from __future__ import annotations

from typing import Any

Row = tuple[str, str, str, str, str, str, str, str, str, float | None, str]

# --- Usage Assurance — mirrors the Rule Explorer listing -------------------
UA: list[Row] = [
    ("UA001", "Missing CDR", "Completeness", "Billing", "critical", "Cycle", "FAIL",
     "MSC switch export", "Mediation output", 0.5,
     "Usage events exported by the switch that never reach mediation are never billed."),
    ("UA002", "Duplicate CDR", "Duplicate", "Usage Events", "high", "Daily", "FAIL",
     "Mediation output", "Rating input", 0.0,
     "The same event rated twice over-bills the subscriber and corrupts the revenue baseline."),
    ("UA003", "Missing Rating", "Existence", "Rating", "critical", "Cycle", "PASS",
     "Mediation output", "Rated events", None,
     "Mediated usage with no rated counterpart is revenue that will never be invoiced."),
    ("UA004", "Late Mediation", "Temporal", "MSC", "medium", "Hourly", "PASS",
     "MSC switch export", "Mediation output", None,
     "Events mediated after the bill cycle closes slip into the next invoice or are dropped."),
    ("UA005", "Sequence Validation", "Sequence", "Billing", "high", "Cycle", "PASS",
     "Mediation output", "Billing feed", None,
     "A gap in the record sequence means a block of usage was lost between stages."),
    ("UA006", "Switch vs Mediation Count", "Reconciliation", "CDR", "critical", "Real-time", "WARNING",
     "MSC switch export", "Mediation output", 0.1,
     "Volume divergence between the switch and mediation is the earliest sign of leakage."),
    ("UA007", "Daily Volume Deviation", "Statistical", "Usage Events", "medium", "Real-time", "PASS",
     "Usage events", "", None,
     "A day far off the rolling baseline points at a feed outage or a silent collector."),
    ("UA008", "Zero Duration Spike", "Threshold", "Usage Events", "medium", "Cycle", "PASS",
     "Usage events", "", None,
     "A spike in zero-duration calls indicates switch or decoder misbehaviour."),
    ("UA009", "Missing CDR — variant 2", "Completeness", "Subscriber", "critical", "Hourly", "PASS",
     "Subscriber usage feed", "Mediation output", 0.5,
     "Per-subscriber completeness catches losses that net out at the aggregate level."),
    ("UA010", "Usage aggregate vs event sum", "Aggregation", "Usage Events", "high", "Daily", "PASS",
     "Usage events", "Daily usage summary", 0.1,
     "A summary table that disagrees with its underlying events misstates reported revenue."),
    ("UA011", "Roaming CDR completeness", "Completeness", "CDR", "high", "Daily", "WARNING",
     "Roaming partner feed", "Mediation output", 0.5,
     "Inbound roaming usage lost before mediation is unbillable and unrecoverable."),
    ("UA012", "Mediation throughput drop", "Statistical", "Mediation", "medium", "Hourly", "PASS",
     "Mediation output", "", None,
     "A sustained throughput drop signals a stalled mediation node."),
]

# --- Rating Assurance ------------------------------------------------------
RA: list[Row] = [
    ("RA001", "Unrated usage events", "Existence", "Usage Events", "critical", "Cycle", "FAIL",
     "Mediation output", "Rated events", None,
     "Events that never enter rating are pure revenue loss."),
    ("RA002", "Tariff version drift", "Comparison", "Tariff", "high", "Daily", "PASS",
     "Tariff catalogue", "Applied tariff", 0.0,
     "Rating against a superseded tariff under- or over-charges every affected event."),
    ("RA003", "Rated amount vs tariff floor", "Calculation", "Rating", "critical", "Cycle", "FAIL",
     "Tariff catalogue v12", "Rated events", 0.0,
     "A rated price below the published floor is a systematic under-charge."),
    ("RA004", "Discount over-application", "Threshold", "Discount", "high", "Daily", "PASS",
     "Rated events", "", None,
     "Discounts beyond the campaign ceiling erode margin silently."),
    ("RA005", "Zero-rated event spike", "Statistical", "Rating", "medium", "Hourly", "PASS",
     "Rated events", "", None,
     "A jump in zero-rated events usually means a broken rating rule."),
    ("RA006", "Duplicate rating pass", "Duplicate", "Rating", "high", "Cycle", "PASS",
     "Rated events", "", None,
     "An event rated twice inflates both the invoice and the revenue baseline."),
    ("RA007", "Rating latency breach", "Temporal", "Rating", "medium", "Hourly", "PASS",
     "Mediation output", "Rated events", None,
     "Usage rated after cycle close misses the invoice it belongs to."),
    ("RA008", "Subscriber plan mismatch", "Referential Integrity", "Subscriber", "high", "Daily", "PASS",
     "Subscriber master", "Rated events", None,
     "Rating against a plan the subscriber no longer holds bills the wrong price."),
]

# --- Partner Assurance -----------------------------------------------------
PA: list[Row] = [
    ("PA001", "Interconnect volume reconciliation", "Reconciliation", "Interconnect", "critical", "Daily", "PASS",
     "Internal interconnect log", "Partner statement", 1.0,
     "Volume disputes with carriers are only defensible with a reconciled record."),
    ("PA002", "Settlement file completeness", "Completeness", "Settlement", "high", "Monthly", "PASS",
     "Partner TAP file", "Settlement ledger", 0.5,
     "A missing settlement file means an unbilled or unpaid partner period."),
    ("PA003", "Roaming TAP rejects", "Existence", "Roaming", "high", "Daily", "WARNING",
     "Partner TAP file", "Accepted TAP records", None,
     "Rejected TAP records are roaming revenue that will never settle."),
    ("PA004", "Partner settlement vs internal usage", "Comparison", "Settlement", "high", "Monthly", "FAIL",
     "Internal rated roaming", "Partner TAP file", 1.0,
     "A declared partner total below our own is an under-settlement to chase."),
    ("PA005", "Partner invoice duplication", "Duplicate", "Invoice", "medium", "Monthly", "PASS",
     "Partner invoices", "", None,
     "Paying the same partner invoice twice is a straight cash loss."),
    ("PA006", "Interconnect rate card drift", "Calculation", "Partner", "high", "Monthly", "PASS",
     "Agreed rate card", "Applied rates", 0.0,
     "Settling at the wrong rate silently changes the margin on every minute."),
]

# --- Migration Assurance ---------------------------------------------------
MA: list[Row] = [
    ("MA001", "Subscriber count reconciliation", "Reconciliation", "Subscriber", "critical", "Daily", "PASS",
     "Legacy BSS extract", "Target BSS", 0.0,
     "Subscribers lost in a migration wave stop being billed entirely."),
    ("MA002", "Legacy vs migrated balance integrity", "Referential Integrity", "Balance", "critical", "Daily", "FAIL",
     "Legacy BSS extract", "Target BSS", 0.0,
     "Balances that do not carry over are customer money the platform has forgotten."),
    ("MA003", "Product catalog mapping gaps", "Existence", "Product Catalog", "high", "Daily", "PASS",
     "Legacy catalogue", "Target catalogue", None,
     "An unmapped product leaves migrated subscribers on no billable plan."),
    ("MA004", "Duplicate subscriber records", "Duplicate", "Subscriber", "high", "Daily", "PASS",
     "Target BSS", "", None,
     "A subscriber migrated twice double-bills and corrupts churn reporting."),
    ("MA005", "Account hierarchy integrity", "Graph Relationship", "Account", "medium", "Weekly", "PASS",
     "Target BSS", "", None,
     "Broken parent/child links misroute invoices for corporate accounts."),
    ("MA006", "Migration cut-over latency", "Temporal", "Legacy Feed", "medium", "Daily", "PASS",
     "Legacy feed", "Target BSS", None,
     "Usage arriving after cut-over lands on a dead account unless it is redirected."),
]

# --- Billing Assurance -----------------------------------------------------
BA: list[Row] = [
    ("BA001", "Rated usage vs invoiced amount", "Reconciliation", "Bill Run", "critical", "Cycle", "FAIL",
     "Rated events", "Invoice lines", 0.2,
     "Rated usage that never reaches an invoice is unbilled revenue."),
    ("BA002", "Invoice line completeness", "Completeness", "Invoice", "critical", "Cycle", "PASS",
     "Rated events", "Invoice lines", 0.1,
     "A missing invoice line is revenue quietly written off."),
    ("BA003", "Duplicate invoice lines", "Duplicate", "Invoice", "high", "Cycle", "PASS",
     "Invoice lines", "", None,
     "Duplicated lines over-bill the customer and trigger credits and churn."),
    ("BA004", "Tax calculation accuracy", "Calculation", "Tax", "high", "Cycle", "PASS",
     "Tax rules", "Invoice tax lines", 0.0,
     "Mis-computed tax is a compliance exposure, not just a revenue one."),
    ("BA005", "Credit note threshold", "Threshold", "Adjustment", "medium", "Daily", "PASS",
     "Adjustments", "", None,
     "A surge in credits usually traces back to an upstream billing defect."),
    ("BA006", "Bill run vs rated usage reconciliation", "Reconciliation", "Bill Run", "critical", "Cycle", "FAIL",
     "Rated events", "Bill run output", 0.2,
     "A cycle total below the rated usage it drew from means accounts were skipped."),
    ("BA007", "Account without invoice", "Existence", "Account", "high", "Cycle", "PASS",
     "Active accounts", "Invoices", None,
     "An active account with no invoice for the cycle is a billing miss."),
    ("BA008", "Bill cycle completion latency", "Temporal", "Bill Run", "medium", "Cycle", "PASS",
     "Bill run", "", None,
     "A late cycle delays cash collection and breaches the billing SLA."),
]

# --- Charging Assurance ----------------------------------------------------
CA: list[Row] = [
    ("CA001", "Balance deduction vs charge existence", "Existence", "Session", "high", "Real-time", "FAIL",
     "OCS balance log", "Charging records", None,
     "Balance taken with no charge record is money removed without a billable basis."),
    ("CA002", "Negative balance threshold", "Threshold", "Balance", "high", "Real-time", "PASS",
     "OCS balance", "", None,
     "Balances driven negative indicate a failed credit check or a rating defect."),
    ("CA003", "Voucher redemption duplication", "Duplicate", "Voucher", "critical", "Real-time", "PASS",
     "Voucher redemptions", "", None,
     "A voucher redeemed twice credits value that was never sold."),
    ("CA004", "Session vs charge reconciliation", "Reconciliation", "Session", "critical", "Hourly", "WARNING",
     "Session records", "Charging records", 0.1,
     "Sessions without charges are unbilled data usage."),
    ("CA005", "Bundle allowance over-grant", "Calculation", "Bundle", "high", "Daily", "PASS",
     "Bundle catalogue", "Granted allowances", 0.0,
     "Granting more allowance than the bundle defines gives away revenue."),
    ("CA006", "Charging latency breach", "Temporal", "OCS", "medium", "Hourly", "PASS",
     "Session records", "Charging records", None,
     "Late charging lets a subscriber consume beyond their balance."),
]

# --- Network Assurance -----------------------------------------------------
NA: list[Row] = [
    ("NA001", "Element feed completeness", "Completeness", "Element Feed", "high", "Hourly", "PASS",
     "Network elements", "Collector", 0.5,
     "A silent element means its traffic is invisible to billing."),
    ("NA002", "Node record volume deviation", "Statistical", "Node", "medium", "Hourly", "PASS",
     "Node feed", "", None,
     "Volume off the node baseline points at a degraded or restarted element."),
    ("NA003", "Probe vs switch record match", "Reconciliation", "Probe", "high", "Daily", "PASS",
     "Probe capture", "Switch export", 1.0,
     "A probe/switch divergence is the independent check on switch honesty."),
    ("NA004", "MSC file sequence gaps", "Sequence", "MSC", "high", "Hourly", "PASS",
     "MSC export", "Collector", None,
     "A file sequence gap is a block of usage the platform never saw."),
    ("NA005", "Feed heartbeat / silence detection", "Temporal", "Probe", "high", "Real-time", "FAIL",
     "SGSN probe", "Collector", None,
     "A feed that goes quiet is the fastest-moving form of leakage."),
    ("NA006", "SGSN throughput floor", "Threshold", "SGSN", "medium", "Hourly", "PASS",
     "SGSN feed", "", None,
     "Throughput below the floor indicates a partial outage rather than low traffic."),
]

# --- Collection Assurance --------------------------------------------------
CL: list[Row] = [
    ("CL001", "Payment vs bank statement reconciliation", "Reconciliation", "Payment", "critical", "Daily", "PASS",
     "Bank statement feed", "Payments ledger", 0.0,
     "Payments that do not reconcile to the bank are cash unaccounted for."),
    ("CL002", "Aged receivable threshold", "Threshold", "Receivable", "high", "Daily", "PASS",
     "Receivables ledger", "", None,
     "Receivables past the ageing threshold are collection risk."),
    ("CL003", "Payment vs receivable allocation", "Referential Integrity", "Payment", "medium", "Daily", "FAIL",
     "Bank statement feed", "Receivables ledger", 0.0,
     "Unallocated payments overstate debt and trigger wrongful dunning."),
    ("CL004", "Duplicate payment posting", "Duplicate", "Payment", "high", "Daily", "PASS",
     "Payments ledger", "", None,
     "A payment posted twice understates what the customer still owes."),
    ("CL005", "Dunning trigger accuracy", "Comparison", "Dunning", "medium", "Daily", "PASS",
     "Receivables ledger", "Dunning queue", 0.0,
     "Dunning a paid-up customer is a churn risk; not dunning a debtor is a cash risk."),
    ("CL006", "Unapplied cash ageing", "Temporal", "Bank Feed", "medium", "Weekly", "PASS",
     "Bank statement feed", "", None,
     "Cash sitting unapplied distorts both the ledger and the collections view."),
]

# --- Mediation Assurance ---------------------------------------------------
ME: list[Row] = [
    ("ME001", "AIR file ingest completeness", "Completeness", "AIR", "critical", "Cycle", "PASS",
     "AIR collector", "Decoder", 0.5,
     "Files collected but never decoded are usage the platform never sees."),
    ("ME003", "File decode exception", "Existence", "File Feed", "high", "Hourly", "FAIL",
     "SDP export", "Decoder", None,
     "A rejected file quarantines its whole record set until it is re-delivered."),
    ("ME007", "File sequence continuity", "Sequence", "File Feed", "high", "Hourly", "FAIL",
     "SDP export", "Mediation ingest", None,
     "Missing files in a sequence are a contiguous block of lost usage."),
    ("ME009", "Record sequence continuity", "Sequence", "AIR", "medium", "Hourly", "PASS",
     "AIR collector", "Decoder", None,
     "A record-level gap usually traces back to a collector restart."),
    ("ME014", "AIR pre vs post amount reconciliation", "Reconciliation", "AIR", "critical", "Cycle", "FAIL",
     "AIR raw collector", "Mediation output", 0.5,
     "A raw-vs-processed amount delta is under- or over-charging at scale."),
    ("ME018", "SDP pre vs post reconciliation", "Reconciliation", "SDP", "critical", "Cycle", "PASS",
     "SDP raw collector", "Mediation output", 0.5,
     "The SDP counterpart of the AIR value check."),
    ("ME021", "AIR vs SDP cross reconciliation", "Reconciliation", "CDR", "critical", "Cycle", "FAIL",
     "AIR mediation output", "SDP mediation output", 0.1,
     "Transactions present in one stream but not the other are potentially unbilled."),
    ("ME024", "Duplicate file ingest", "Duplicate", "File Feed", "high", "Hourly", "PASS",
     "Mediation ingest", "", None,
     "A file ingested twice duplicates every record it carries."),
    ("ME028", "Mediation output latency", "Temporal", "Mediation", "medium", "Hourly", "PASS",
     "Mediation ingest", "Mediation output", None,
     "Latency here pushes every downstream stage past its cycle window."),
]

ALL_RULE_ROWS: dict[str, list[Row]] = {
    "UA": UA, "RA": RA, "PA": PA, "MA": MA,
    "BA": BA, "CA": CA, "NA": NA, "CL": CL, "ME": ME,
}


def rule_dicts() -> list[dict[str, Any]]:
    """Flatten the tables above into ControlRule kwargs."""
    from app import catalog

    out: list[dict[str, Any]] = []
    for code, rows in ALL_RULE_ROWS.items():
        assurance = catalog.ASSURANCE_BY_CODE[code]
        for (rule_id, name, category, entity, severity, frequency, status,
             source, target, tolerance, intent) in rows:
            out.append({
                "id": rule_id,
                "name": name,
                "intent": intent,
                "assurance_code": assurance.code,
                "assurance_name": assurance.name,
                "assurance_group": assurance.group,
                "entity_scope": entity,
                "primitive_category": category,
                "severity": severity,
                "frequency": frequency,
                "source_feed": source,
                "target_feed": target,
                "tolerance_pct": tolerance,
                "params": {},
                # Everything shipped in the catalog is live; a rule authored in
                # the UI starts as a Draft.
                "lifecycle_state": "Active",
                "last_status": status,
                "created_by": "platform",
            })
    return out
