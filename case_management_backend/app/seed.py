"""First-boot data load: the control-rule catalog, then cases raised by it.

Every seeded case goes through the same path a real control run takes
(`rule_service.record_run`), so it inherits its assurance, module, issue type,
feeds and tolerance from an actual rule row rather than from invented metadata.

Set SEED_DEMO_DATA=false to start with an empty database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import rule_service, schemas
from app.models import Case, ControlRule
from app.rules_seed import rule_dicts


def _ago(days: int, hour: int = 9) -> datetime:
    """A UTC timestamp `days` back at `hour` — keeps the demo list fresh."""
    base = datetime.now(timezone.utc) - timedelta(days=days)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def _m(**kw: Any) -> schemas.MismatchIn:
    return schemas.MismatchIn(**kw)


def _amount_rows(prefix: str, n: int, node: str, drift: float, field: str = "charged_amount"):
    """Value-variance rows: expected vs actual diverging by a constant drift."""
    rows = []
    for i in range(n):
        expected = round(12.5 + i * 3.25, 2)
        rows.append(_m(
            record_ref=f"{prefix}{100234 + i * 17}", entity=node,
            subscriber=f"2547{10045678 + i * 311}", field=field,
            expected_value=f"{expected:.2f}", actual_value=f"{expected - drift:.2f}",
            delta=f"-{drift:.2f}", status="AMOUNT_MISMATCH",
        ))
    return rows


def _missing_rows(prefix: str, n: int, node: str, field: str = "record"):
    """Present-upstream / absent-downstream rows."""
    return [
        _m(record_ref=f"{prefix}{100251 + i * 13}", entity=node,
           subscriber=f"2547{10061234 + i * 407}", field=field,
           expected_value="present", actual_value="missing", delta="1 record",
           status="SOURCE_ONLY")
        for i in range(n)
    ]


# Each entry drives one control run. `triage` is applied after the case opens,
# so the seeded queue spans the whole lifecycle (open work, work in flight and
# closed history) instead of being 20 identical Open rows.
#   (rule_id, run result, triage)
SeedCase = tuple[str, dict[str, Any], dict[str, Any]]


def demo_runs() -> list[SeedCase]:
    return [
        # ---------------- Mediation Assurance ----------------
        ("ME014", dict(
            title="AIR raw vs processed amount variance — batch AIR_0620_03",
            description=(
                "9,100 AIR records were under-rated — the processed amount fell below the raw "
                "charged amount after mediation. Concentrated on NODE-2 between 02:00 and 04:00, "
                "avg under-charge $3.80/record."
            ),
            run_id="RUN-ME-90231", severity="critical", stream="AIR", node_id="NODE-2",
            linked_batch="AIR_20250620_03", linked_txn_id="AIR100234", sub_module="Post-mediation",
            expected_value="1,284,322.40", actual_value="1,249,722.40",
            variance="-34,600.00", variance_pct=-2.69,
            estimated_impact=34600, affected_count=9100, detected_at=_ago(0, 2),
            dedupe_key="ME014:2025-06-20:NODE-2",
            mismatches=_amount_rows("AIR", 6, "NODE-2", 1.75),
        ), dict(reference="CASE-2031", evidence={
            "name": "air_variance_0620.csv", "kind": "CSV extract", "size": "84 KB"})),

        ("ME007", dict(
            title="Missing file sequence — SDP 20250619 (seq 41–43)",
            description=(
                "Three consecutive SDP CDR files absent from the ingest window (≈1,220 records). "
                "Upstream mediation reported successful export for all three."
            ),
            run_id="RUN-ME-90228", severity="high", stream="SDP", node_id="NODE-1",
            linked_batch="SDP_20250619_41", linked_txn_id="SDP100251", sub_module="SDP ingest",
            expected_value="seq 40–44", actual_value="seq 40, 44", variance="3 files missing",
            estimated_impact=22300, affected_count=1220, detected_at=_ago(1, 3),
            owner="super_admin", dedupe_key="ME007:2025-06-19:NODE-1",
            mismatches=[
                _m(record_ref=f"SDP_20250619_{n}", entity="NODE-1", field="file",
                   expected_value="delivered", actual_value="absent",
                   delta="~407 records", status="MISSING_FILE")
                for n in (41, 42, 43)
            ],
        ), dict(reference="CASE-2030", status="In Progress", action="Escalated to carrier",
                evidence={"name": "sdp_seq_gap.png", "kind": "Screenshot", "size": "212 KB"},
                comments=[("Priya Shah", "Raised with the mediation team — they are re-exporting 41–43.")])),

        ("ME021", dict(
            title="AIR vs SDP cross-recon mismatch — 2,180 unmatched",
            description=(
                "Cross correlation between AIR and SDP shows 2,180 transactions present in AIR "
                "with no SDP counterpart for the 18 Jun window — potentially unbilled roaming usage."
            ),
            run_id="RUN-ME-90224", severity="critical", stream="AIR", node_id="NODE-3",
            linked_batch="AIR_20250618_11", linked_txn_id="AIR100268", sub_module="Cross-stream",
            expected_value="864,220 matched", actual_value="862,040 matched",
            variance="2,180 unmatched", variance_pct=-0.25,
            estimated_impact=39800, affected_count=2180, detected_at=_ago(2, 4),
            owner="super_admin", dedupe_key="ME021:2025-06-18:NODE-3",
            mismatches=_missing_rows("AIR", 8, "NODE-3", field="sdp_counterpart"),
        ), dict(reference="CASE-2029")),

        ("ME009", dict(
            title="Record sequence gap — AIR NODE-4 (515 records)",
            description=(
                "Record sequence check found a contiguous 515-record gap on NODE-4. Suspected "
                "collector restart during the 17 Jun maintenance window; records recovered on re-pull."
            ),
            run_id="RUN-ME-90219", severity="medium", stream="AIR", node_id="NODE-4",
            linked_batch="AIR_20250617_22", linked_txn_id="AIR100302", sub_module="Collector",
            expected_value="seq 118400–118914", actual_value="gap 118400–118914",
            variance="515 records", estimated_impact=9400, affected_count=515,
            detected_at=_ago(3, 8), owner="super_admin", dedupe_key="ME009:2025-06-17:NODE-4",
            mismatches=_missing_rows("AIR", 3, "NODE-4", field="sequence_no"),
        ), dict(reference="CASE-2028", status="Closed", action="Config fix raised",
                evidence={"name": "node4_seq_report.csv", "kind": "CSV extract", "size": "41 KB"},
                comments=[("super_admin", "Collector restart confirmed in the ops log. Config change raised as CHG-4471.")])),

        ("ME003", dict(
            title="File exception — malformed header on SDP_20250617_08",
            description=(
                "Decoder rejected the file on a malformed header record. File quarantined; "
                "0 records ingested, 860 pending re-delivery."
            ),
            run_id="RUN-ME-90215", severity="high", stream="SDP", node_id="NODE-1",
            linked_batch="SDP_20250617_08", linked_txn_id="SDP100318", sub_module="Decoder",
            expected_value="860 records", actual_value="0 records", variance="860 records",
            estimated_impact=15700, affected_count=860, detected_at=_ago(3, 5),
            owner="super_admin", dedupe_key="ME003:2025-06-17:SDP_08",
            mismatches=[_m(record_ref="SDP_20250617_08", entity="NODE-1", field="header",
                           expected_value="valid ASN.1 header",
                           actual_value="malformed at offset 0x1C", status="DECODE_ERROR")],
        ), dict(reference="CASE-2027", status="In Progress",
                evidence={"name": "decoder_reject.log", "kind": "Log excerpt", "size": "6 KB"})),

        ("ME024", dict(
            title="Duplicate file ingest — AIR_20250616_09 processed twice",
            description=(
                "The same AIR file was ingested in the 06:00 and the 06:40 retry cycle, "
                "duplicating 2,140 records downstream."
            ),
            run_id="RUN-ME-90202", severity="high", stream="AIR", node_id="NODE-2",
            linked_batch="AIR_20250616_09", sub_module="Ingest", expected_value="1 ingest",
            actual_value="2 ingests", variance="2,140 duplicate records",
            estimated_impact=7800, affected_count=2140, detected_at=_ago(6, 7),
            owner="Priya Shah", dedupe_key="ME024:2025-06-16:AIR_09",
            mismatches=[_m(record_ref="AIR_20250616_09", entity="NODE-2", field="ingest_count",
                           expected_value="1", actual_value="2", delta="+1", status="DUPLICATE")],
        ), dict(status="Resolved", action="Adjusted & rebilled",
                comments=[("Priya Shah", "Duplicate batch reversed; downstream rating re-run for the window.")])),

        # ---------------- Usage Assurance ----------------
        ("UA001", dict(
            title="Missing CDR — switch vs mediation count breach",
            description=(
                "MSC switch export reported 1,284,322 CDRs for the 04:00 cycle; mediation output "
                "landed 1,281,904. 2,418 usage events unaccounted for, above the 0.5% tolerance."
            ),
            run_id="RUN-UA-77412", severity="critical", stream="MSC", node_id="MSC-EU-1",
            linked_batch="BATCH-441A", linked_txn_id="CDR8841002", sub_module="Voice",
            expected_value="1,284,322", actual_value="1,281,904", variance="-2,418",
            variance_pct=-0.19, estimated_impact=18450, affected_count=2418,
            detected_at=_ago(0, 4), dedupe_key="UA001:2026-07-30:MSC-EU-1",
            mismatches=_missing_rows("CDR", 5, "MSC-EU-1", field="mediation_record"),
        ), {}),

        ("UA002", dict(
            title="Duplicate CDR — 412 events ingested twice",
            description=(
                "Duplicate detection found 412 usage events with an identical "
                "(msisdn, start_time, duration) signature across two mediation runs — "
                "a replayed batch after the 03:10 retry."
            ),
            run_id="RUN-UA-77410", severity="high", stream="MSC", node_id="MSC-EU-2",
            linked_batch="BATCH-441B", linked_txn_id="CDR8842119", sub_module="Data",
            expected_value="0 duplicates", actual_value="412 duplicates", variance="412",
            estimated_impact=6100, affected_count=412, detected_at=_ago(1, 6),
            owner="super_admin", dedupe_key="UA002:2026-07-29:MSC-EU-2",
            mismatches=[
                _m(record_ref=f"CDR884{2119 + i}", entity="MSC-EU-2",
                   subscriber=f"2547{10077001 + i * 53}", field="event_signature",
                   expected_value="1 occurrence", actual_value="2 occurrences",
                   delta="+1", status="DUPLICATE")
                for i in range(4)
            ],
        ), dict(status="In Progress", action="Under review",
                comments=[("Aarav Mehta", "Confirmed against the retry log — batch 441B was submitted twice at 03:10.")])),

        ("UA006", dict(
            title="Switch vs mediation count drift — MSC-APAC 06:00 cycle",
            description=(
                "Real-time reconciliation flagged a 0.14% shortfall between the switch export and "
                "mediation output. Below the alerting floor individually, but sustained across "
                "four consecutive cycles."
            ),
            run_id="RUN-UA-77419", severity="critical", stream="MSC", node_id="MSC-APAC",
            linked_batch="BATCH-442C", sub_module="Voice", expected_value="982,110",
            actual_value="980,735", variance="-1,375", variance_pct=-0.14,
            estimated_impact=9600, affected_count=1375, detected_at=_ago(0, 6),
            dedupe_key="UA006:2026-07-30:MSC-APAC",
            mismatches=_missing_rows("CDR", 4, "MSC-APAC", field="mediation_record"),
        ), {}),

        ("UA007", dict(
            title="Daily usage volume 14% below the 30-day baseline",
            description=(
                "Statistical control flagged the 29 Jul voice volume at 14.2% below the rolling "
                "30-day mean — outside the 3-sigma band. No feed outage recorded."
            ),
            run_id="RUN-UA-77401", severity="medium", stream="MSC", node_id="MSC-APAC",
            linked_batch="BATCH-440Z", sub_module="Voice", expected_value="1,742,000 ± 3σ",
            actual_value="1,494,600", variance="-247,400", variance_pct=-14.2,
            estimated_impact=11200, affected_count=247400, detected_at=_ago(1, 8),
            owner="super_admin", dedupe_key="UA007:2026-07-29:MSC-APAC",
        ), dict(action="Under review")),

        ("UA010", dict(
            title="Daily aggregate vs event sum drift — 1,204 minutes",
            description=(
                "The daily usage summary disagrees with the sum of its underlying events by "
                "1,204 minutes for the 28 Jul partition."
            ),
            run_id="RUN-UA-77398", severity="high", stream="MSC", node_id="MSC-EU-1",
            linked_batch="AGG_20260728", sub_module="Voice", expected_value="482,110",
            actual_value="480,906", variance="-1,204", variance_pct=-0.25,
            estimated_impact=4300, affected_count=1204, detected_at=_ago(2, 5),
            owner="Priya Shah", dedupe_key="UA010:2026-07-28:MSC-EU-1",
            mismatches=[_m(record_ref="AGG-20260728-01", entity="MSC-EU-1", field="minutes",
                           expected_value="482110", actual_value="480906", delta="-1204",
                           status="AMOUNT_MISMATCH")],
        ), dict(status="Resolved", action="Config fix raised",
                comments=[("Priya Shah", "Aggregator window was off by one hour; corrected and back-filled.")])),

        ("UA011", dict(
            title="Roaming CDR completeness breach — partner EU-3 inbound",
            description=(
                "Inbound roaming records from partner EU-3 are 0.9% short against the received "
                "TAP volume for the 27 Jul window."
            ),
            run_id="RUN-UA-77390", severity="high", stream="CDR", node_id="MSC-EU-3",
            linked_batch="ROAM_20260727", sub_module="Roaming", expected_value="204,880",
            actual_value="203,036", variance="-1,844", variance_pct=-0.9,
            estimated_impact=13400, affected_count=1844, detected_at=_ago(3, 10),
            dedupe_key="UA011:2026-07-27:MSC-EU-3",
            mismatches=_missing_rows("ROM", 3, "MSC-EU-3", field="mediation_record"),
        ), dict(status="Closed", action="Escalated to carrier",
                comments=[("Aarav Mehta", "Partner re-sent the missing TAP batch; volumes reconcile.")])),

        # ---------------- Rating Assurance ----------------
        ("RA003", dict(
            title="Rated amount below tariff floor — 1,842 events",
            description=(
                "Rated charges for the off-peak data bundle fell below the published tariff floor "
                "after the 28 Jul tariff version change. Rating applied v11 pricing to events that "
                "should have used v12."
            ),
            run_id="RUN-RA-33120", severity="critical", stream="Rating", node_id="RATE-02",
            linked_batch="RATE_20250729_06", linked_txn_id="RTE770021", sub_module="Tariff version",
            expected_value="0.085 /MB", actual_value="0.062 /MB", variance="-0.023 /MB",
            variance_pct=-27.06, estimated_impact=27400, affected_count=1842,
            detected_at=_ago(1, 11), dedupe_key="RA003:2026-07-29:RATE-02",
            mismatches=[
                _m(record_ref=f"RTE77{20 + i}", entity="RATE-02",
                   subscriber=f"2547{10088123 + i * 211}", field="rate_per_mb",
                   expected_value="0.085", actual_value="0.062", delta="-0.023",
                   status="AMOUNT_MISMATCH")
                for i in range(5)
            ],
        ), {}),

        ("RA001", dict(
            title="Unrated usage events — 640 records on the 26 Jul cycle",
            description=(
                "640 mediated events have no rated counterpart. All carry a product code that was "
                "retired mid-cycle without a replacement mapping."
            ),
            run_id="RUN-RA-33101", severity="critical", stream="Rating", node_id="RATE-01",
            linked_batch="RATE_20250726_02", sub_module="Product mapping",
            expected_value="640 rated", actual_value="0 rated", variance="640 unrated",
            estimated_impact=16800, affected_count=640, detected_at=_ago(4, 9),
            owner="super_admin", dedupe_key="RA001:2026-07-26:RATE-01",
            mismatches=_missing_rows("RTE", 3, "RATE-01", field="rated_event"),
        ), dict(status="Closed", action="Adjusted & rebilled",
                comments=[("super_admin", "Product mapping restored and the 640 events re-rated into the next cycle.")])),

        # ---------------- Billing Assurance ----------------
        ("BA006", dict(
            title="Bill run total vs rated usage variance — cycle 07",
            description=(
                "Invoiced revenue for bill cycle 07 is $41,200 below the rated usage it should have "
                "drawn from. 620 accounts show usage rated but not invoiced."
            ),
            run_id="RUN-BA-51044", severity="critical", stream="Billing", node_id="BILL-01",
            linked_batch="BILLRUN_2025_07", linked_txn_id="INV9910442", sub_module="Cycle 07",
            expected_value="4,982,300.00", actual_value="4,941,100.00", variance="-41,200.00",
            variance_pct=-0.83, estimated_impact=41200, affected_count=620,
            detected_at=_ago(2, 7), owner="Priya Shah", dedupe_key="BA006:2026-07-28:BILL-01",
            mismatches=[
                _m(record_ref=f"ACC{4410 + i}", entity="BILL-01", field="invoiced_amount",
                   expected_value=f"{120.40 + i * 11:.2f}", actual_value="0.00",
                   delta=f"-{120.40 + i * 11:.2f}", status="NOT_INVOICED")
                for i in range(4)
            ],
        ), dict(status="In Progress", action="Adjusted & rebilled",
                evidence={"name": "cycle07_variance.xlsx", "kind": "Workbook", "size": "310 KB"},
                comments=[("Priya Shah", "Rebill queued for the 620 accounts in the next cycle.")])),

        ("BA003", dict(
            title="Duplicate invoice lines — 88 accounts on cycle 06",
            description=(
                "88 accounts carry a duplicated recurring-charge line, over-billing a total of "
                "$3,960 before credits."
            ),
            run_id="RUN-BA-51020", severity="high", stream="Billing", node_id="BILL-02",
            linked_batch="BILLRUN_2025_06", sub_module="Cycle 06", expected_value="1 line/charge",
            actual_value="2 lines/charge", variance="+3,960.00",
            estimated_impact=3960, affected_count=88, detected_at=_ago(7, 10),
            owner="super_admin", dedupe_key="BA003:2026-07-23:BILL-02",
            mismatches=[
                _m(record_ref=f"INV99{1050 + i}", entity="BILL-02", field="charge_line",
                   expected_value="1", actual_value="2", delta="+45.00", status="DUPLICATE")
                for i in range(3)
            ],
        ), dict(status="Closed", action="Adjusted & rebilled",
                comments=[("super_admin", "Credits issued on all 88 accounts; the recurring-charge job was patched.")])),

        # ---------------- Charging Assurance ----------------
        ("CA001", dict(
            title="Balance deduction without matching charge — 96 sessions",
            description=(
                "OCS deducted balance on 96 data sessions that have no corresponding charging "
                "record. Suspected session-teardown race on the OCS-3 node."
            ),
            run_id="RUN-CA-20988", severity="high", stream="Charging", node_id="OCS-3",
            linked_batch="OCS_20250728_14", linked_txn_id="SES551027", sub_module="Data",
            expected_value="96 charges", actual_value="0 charges", variance="96",
            estimated_impact=3120, affected_count=96, detected_at=_ago(2, 13),
            dedupe_key="CA001:2026-07-28:OCS-3",
            mismatches=[
                _m(record_ref=f"SES5510{27 + i}", entity="OCS-3",
                   subscriber=f"2547{10099887 + i * 91}", field="charge_record",
                   expected_value="present", actual_value="missing", delta="32.50",
                   status="SOURCE_ONLY")
                for i in range(3)
            ],
        ), {}),

        ("CA004", dict(
            title="Session vs charge reconciliation drift — OCS-1",
            description=(
                "0.3% of data sessions on OCS-1 closed without a charging record during the "
                "26 Jul evening peak."
            ),
            run_id="RUN-CA-20970", severity="critical", stream="Charging", node_id="OCS-1",
            linked_batch="OCS_20250726_20", sub_module="Data", expected_value="412,880 charged",
            actual_value="411,640 charged", variance="-1,240", variance_pct=-0.3,
            estimated_impact=8900, affected_count=1240, detected_at=_ago(4, 20),
            owner="Aarav Mehta", dedupe_key="CA004:2026-07-26:OCS-1",
            mismatches=_missing_rows("SES", 3, "OCS-1", field="charge_record"),
        ), dict(status="In Progress", action="Under review")),

        # ---------------- Partner Assurance ----------------
        ("PA004", dict(
            title="Partner settlement variance — roaming out, EU-3",
            description=(
                "Outbound roaming settlement file from partner EU-3 declares 18,220 minutes "
                "against our 18,914 rated minutes for the July window."
            ),
            run_id="RUN-PA-14022", severity="high", stream="Partner", node_id="PTR-EU3",
            linked_batch="TAP_EU3_202507", linked_txn_id="TAP330912", sub_module="Roaming out",
            expected_value="18,914 min", actual_value="18,220 min", variance="-694 min",
            variance_pct=-3.67, estimated_impact=12800, affected_count=694,
            detected_at=_ago(4, 10), owner="Aarav Mehta", dedupe_key="PA004:2026-07-26:PTR-EU3",
            mismatches=[
                _m(record_ref=f"TAP3309{12 + i}", entity="PTR-EU3", field="minutes",
                   expected_value=f"{240 + i * 30}", actual_value=f"{210 + i * 30}",
                   delta="-30", status="AMOUNT_MISMATCH")
                for i in range(3)
            ],
        ), dict(status="In Progress", action="Escalated to carrier")),

        ("PA003", dict(
            title="Roaming TAP rejects — 1,120 records from partner APAC-1",
            description=(
                "The 25 Jul TAP batch from APAC-1 rejected 1,120 records on a subscriber-identity "
                "validation error. Rejected roaming usage cannot settle."
            ),
            run_id="RUN-PA-14010", severity="high", stream="Partner", node_id="PTR-APAC1",
            linked_batch="TAP_APAC1_202507", sub_module="Roaming in",
            expected_value="0 rejects", actual_value="1,120 rejects", variance="1,120",
            estimated_impact=9400, affected_count=1120, detected_at=_ago(5, 11),
            owner="super_admin", dedupe_key="PA003:2026-07-25:PTR-APAC1",
            mismatches=_missing_rows("TAP", 3, "PTR-APAC1", field="tap_record"),
        ), dict(status="Resolved", action="Escalated to carrier",
                comments=[("super_admin", "Partner corrected the IMSI prefix and re-sent the batch.")])),

        # ---------------- Migration Assurance ----------------
        ("MA002", dict(
            title="Subscriber balances not carried over — migration wave 3",
            description=(
                "Wave 3 migration moved 12,400 prepaid subscribers; 318 landed with a zero balance "
                "against a non-zero legacy balance."
            ),
            run_id="RUN-MA-9004", severity="critical", stream="Migration", node_id="MIG-W3",
            linked_batch="MIG_W3_20250725", linked_txn_id="MIG441002", sub_module="Wave 3",
            expected_value="318 balances > 0", actual_value="318 balances = 0",
            variance="-8,940.00", estimated_impact=8940, affected_count=318,
            detected_at=_ago(5, 9), owner="super_admin", dedupe_key="MA002:2026-07-25:MIG-W3",
            mismatches=[
                _m(record_ref=f"MIG4410{2 + i}", entity="MIG-W3",
                   subscriber=f"2547{10033445 + i * 77}", field="balance",
                   expected_value=f"{28.10 + i * 4:.2f}", actual_value="0.00",
                   delta=f"-{28.10 + i * 4:.2f}", status="AMOUNT_MISMATCH")
                for i in range(4)
            ],
        ), dict(status="Resolved", action="Adjusted & rebilled",
                comments=[("super_admin", "Balances replayed from the legacy extract; verified on 318/318.")])),

        ("MA001", dict(
            title="Subscriber count reconciliation — wave 4 short by 96",
            description=(
                "The wave 4 extract carried 8,420 subscribers; 8,324 arrived in the target BSS. "
                "96 subscribers are not billable until they are re-migrated."
            ),
            run_id="RUN-MA-9011", severity="critical", stream="Migration", node_id="MIG-W4",
            linked_batch="MIG_W4_20250729", sub_module="Wave 4", expected_value="8,420",
            actual_value="8,324", variance="-96", variance_pct=-1.14,
            estimated_impact=5200, affected_count=96, detected_at=_ago(1, 15),
            dedupe_key="MA001:2026-07-29:MIG-W4",
            mismatches=_missing_rows("MIG", 3, "MIG-W4", field="subscriber"),
        ), {}),

        # ---------------- Network Assurance ----------------
        ("NA005", dict(
            title="Probe feed silent for 3h — SGSN-APAC-2",
            description=(
                "No usage records received from SGSN-APAC-2 between 01:00 and 04:00. The node is "
                "reachable, so the collector — not the element — is suspect."
            ),
            run_id="RUN-NA-6621", severity="high", stream="Network", node_id="SGSN-APAC-2",
            linked_batch="PROBE_20250729_01", sub_module="APAC",
            expected_value="≥ 1 file / 15 min", actual_value="0 files / 3h",
            variance="12 intervals", estimated_impact=21500, affected_count=0,
            detected_at=_ago(1, 4), dedupe_key="NA005:2026-07-29:SGSN-APAC-2",
            mismatches=[
                _m(record_ref=f"interval-{h:02d}:00", entity="SGSN-APAC-2", field="file_count",
                   expected_value="≥1", actual_value="0", status="NO_DATA")
                for h in (1, 2, 3)
            ],
        ), {}),

        ("NA004", dict(
            title="MSC file sequence gap — MSC-EU-2 (4 files)",
            description=(
                "Four export files are missing from the MSC-EU-2 hourly sequence during the "
                "24 Jul maintenance window."
            ),
            run_id="RUN-NA-6605", severity="high", stream="Network", node_id="MSC-EU-2",
            linked_batch="MSC_20250724_11", sub_module="EU", expected_value="seq 110–120",
            actual_value="seq 110–115, 120", variance="4 files missing",
            estimated_impact=14200, affected_count=3180, detected_at=_ago(6, 14),
            owner="Priya Shah", dedupe_key="NA004:2026-07-24:MSC-EU-2",
            mismatches=_missing_rows("MSC", 3, "MSC-EU-2", field="export_file"),
        ), dict(status="Closed", action="Re-ingest requested",
                comments=[("Priya Shah", "Files recovered from the node buffer and re-ingested; sequence is complete.")])),

        # ---------------- Collection Assurance ----------------
        ("CL003", dict(
            title="Payments posted without a matching receivable — 74 records",
            description=(
                "74 bank payments cleared with no open receivable to allocate against, leaving "
                "$18,900 unapplied on the 28 Jul bank feed."
            ),
            run_id="RUN-CL-4410", severity="medium", stream="Collection", node_id="FIN-01",
            linked_batch="BANK_20250728", linked_txn_id="PAY220145", sub_module="Bank feed",
            expected_value="74 allocations", actual_value="0 allocations", variance="18,900.00",
            estimated_impact=18900, affected_count=74, detected_at=_ago(6, 12),
            owner="Priya Shah", dedupe_key="CL003:2026-07-24:FIN-01",
            mismatches=[
                _m(record_ref=f"PAY2201{45 + i}", entity="FIN-01", field="receivable_ref",
                   expected_value="open receivable", actual_value="none",
                   delta=f"{255.40 + i * 20:.2f}", status="UNALLOCATED")
                for i in range(3)
            ],
        ), dict(status="Closed", action="Config fix raised",
                comments=[("Priya Shah", "Allocation rule corrected — reference matching now trims the branch prefix.")])),

        ("CL002", dict(
            title="Aged receivables above threshold — 212 accounts past 90 days",
            description=(
                "212 accounts hold receivables older than 90 days totalling $146,300, above the "
                "$100,000 collection-risk threshold."
            ),
            run_id="RUN-CL-4422", severity="high", stream="Collection", node_id="FIN-01",
            linked_batch="AGEING_202507", sub_module="Ageing", expected_value="≤ 100,000.00",
            actual_value="146,300.00", variance="+46,300.00", estimated_impact=146300,
            affected_count=212, detected_at=_ago(2, 16), owner="Aarav Mehta",
            dedupe_key="CL002:2026-07-28:FIN-01",
        ), dict(status="In Progress", action="Under review")),

        *scenario_runs(),
    ]


def scenario_runs() -> list[SeedCase]:
    """Extra depth on the four scenarios analysts triage most.

    File Sequence, Reconciliation, Threshold and Completeness each get several
    cases across different assurances and lifecycle states, so filtering the
    list by any one issue type lands on a queue worth working rather than a
    single row.
    """
    return [
        # ================= FILE SEQUENCE =================
        ("ME007", dict(
            title="AIR file sequence gap — NODE-3 (seq 88–90 absent)",
            description=(
                "Three AIR export files are missing from the NODE-3 hourly sequence. The collector "
                "logged a successful pull for 87 and 91 but nothing in between."
            ),
            run_id="RUN-ME-90240", severity="high", stream="AIR", node_id="NODE-3",
            linked_batch="AIR_20250728_88", sub_module="AIR ingest",
            expected_value="seq 87–91", actual_value="seq 87, 91", variance="3 files missing",
            estimated_impact=18900, affected_count=1465, detected_at=_ago(2, 9),
            dedupe_key="ME007:2026-07-28:NODE-3",
            mismatches=[
                _m(record_ref=f"AIR_20250728_{n}", entity="NODE-3", field="file",
                   expected_value="delivered", actual_value="absent",
                   delta="~488 records", status="MISSING_FILE")
                for n in (88, 89, 90)
            ],
        ), {}),

        ("UA005", dict(
            title="Billing record sequence break — 1,940 records on cycle 07",
            description=(
                "The billing feed sequence jumps from 442100 to 444040 with no intervening "
                "records. Mediation shows a clean hand-off, so the loss is downstream."
            ),
            run_id="RUN-UA-77425", severity="high", stream="Billing", node_id="BILL-01",
            linked_batch="BILLFEED_2025_07", sub_module="Cycle 07",
            expected_value="seq 442100–444040", actual_value="gap 442101–444039",
            variance="1,940 records", estimated_impact=16200, affected_count=1940,
            detected_at=_ago(1, 12), owner="super_admin", dedupe_key="UA005:2026-07-29:BILL-01",
            mismatches=_missing_rows("BIL", 4, "BILL-01", field="sequence_no"),
        ), dict(status="In Progress", action="Under review")),

        ("NA004", dict(
            title="MSC file sequence gap — MSC-APAC hourly export (2 files)",
            description=(
                "Two hourly export files never arrived from MSC-APAC during the 22 Jul window. "
                "Recovered from the node buffer after the collector was restarted."
            ),
            run_id="RUN-NA-6612", severity="medium", stream="Network", node_id="MSC-APAC",
            linked_batch="MSC_20250722_14", sub_module="APAC", expected_value="seq 13–16",
            actual_value="seq 13, 16", variance="2 files missing",
            estimated_impact=6400, affected_count=1120, detected_at=_ago(8, 14),
            owner="super_admin", dedupe_key="NA004:2026-07-22:MSC-APAC",
            mismatches=_missing_rows("MSC", 2, "MSC-APAC", field="export_file"),
        ), dict(status="Closed", action="Re-ingest requested",
                comments=[("super_admin", "Both files recovered and re-ingested; the sequence is contiguous again.")])),

        # ================= RECONCILIATION =================
        ("ME018", dict(
            title="SDP pre vs post reconciliation variance — batch SDP_0727_12",
            description=(
                "Processed SDP amounts are $12,800 below the raw collected values for the 27 Jul "
                "batch. The drift is uniform across the batch, pointing at a mediation rule change."
            ),
            run_id="RUN-ME-90236", severity="critical", stream="SDP", node_id="NODE-2",
            linked_batch="SDP_20250727_12", linked_txn_id="SDP100420", sub_module="Post-mediation",
            expected_value="642,180.00", actual_value="629,380.00", variance="-12,800.00",
            variance_pct=-1.99, estimated_impact=12800, affected_count=3410,
            detected_at=_ago(3, 3), dedupe_key="ME018:2026-07-27:NODE-2",
            mismatches=_amount_rows("SDP", 5, "NODE-2", 3.75),
        ), {}),

        ("PA001", dict(
            title="Interconnect volume reconciliation — carrier IX-2 short by 3,120 min",
            description=(
                "Our interconnect log records 214,880 outbound minutes to IX-2 for July; their "
                "statement declares 211,760. The 1.45% gap is above the 1% dispute tolerance."
            ),
            run_id="RUN-PA-14031", severity="critical", stream="Partner", node_id="PTR-IX2",
            linked_batch="IX2_202507", sub_module="Outbound", expected_value="214,880 min",
            actual_value="211,760 min", variance="-3,120 min", variance_pct=-1.45,
            estimated_impact=24600, affected_count=3120, detected_at=_ago(2, 11),
            owner="Aarav Mehta", dedupe_key="PA001:2026-07-28:PTR-IX2",
            mismatches=[
                _m(record_ref=f"IX2-{20250701 + i}", entity="PTR-IX2", field="minutes",
                   expected_value=f"{7160 + i * 40}", actual_value=f"{7060 + i * 40}",
                   delta="-100", status="AMOUNT_MISMATCH")
                for i in range(4)
            ],
        ), dict(status="In Progress", action="Escalated to carrier",
                comments=[("Aarav Mehta", "Dispute pack sent to IX-2 with our CDR extract for the period.")])),

        ("CL001", dict(
            title="Payment vs bank statement reconciliation — 14 unmatched credits",
            description=(
                "14 credits totalling $32,400 appear on the 26 Jul bank statement with no matching "
                "payment in the ledger. Suspected same-day settlement batch not yet imported."
            ),
            run_id="RUN-CL-4431", severity="critical", stream="Collection", node_id="FIN-02",
            linked_batch="BANK_20250726", sub_module="Bank feed", expected_value="1,284 credits",
            actual_value="1,270 matched", variance="14 unmatched / 32,400.00",
            estimated_impact=32400, affected_count=14, detected_at=_ago(4, 8),
            owner="Priya Shah", dedupe_key="CL001:2026-07-26:FIN-02",
            mismatches=[
                _m(record_ref=f"BNK{88120 + i}", entity="FIN-02", field="payment_ref",
                   expected_value="ledger match", actual_value="none",
                   delta=f"{2314.00 + i * 180:.2f}", status="UNMATCHED")
                for i in range(3)
            ],
        ), dict(status="Resolved", action="Config fix raised",
                comments=[("Priya Shah", "Settlement batch imported; all 14 credits matched on re-run.")])),

        ("MA001", dict(
            title="Subscriber count reconciliation — wave 2 reconciles clean after re-run",
            description=(
                "Wave 2 initially reported 42 subscribers short. A second extract showed the gap "
                "was a timing artefact of the cut-over window, not lost data."
            ),
            run_id="RUN-MA-8990", severity="high", stream="Migration", node_id="MIG-W2",
            linked_batch="MIG_W2_20250718", sub_module="Wave 2", expected_value="6,180",
            actual_value="6,138", variance="-42", variance_pct=-0.68,
            estimated_impact=2100, affected_count=42, detected_at=_ago(12, 10),
            owner="super_admin", dedupe_key="MA001:2026-07-18:MIG-W2",
            mismatches=_missing_rows("MIG", 2, "MIG-W2", field="subscriber"),
        ), dict(status="Closed", action="Waived",
                comments=[("super_admin", "Re-extract reconciles 6,180/6,180. Closed as a cut-over timing artefact.")])),

        # ================= THRESHOLD =================
        ("UA008", dict(
            title="Zero-duration call spike — 8.4% of MSC-EU-1 voice events",
            description=(
                "Zero-duration calls rose to 8.4% of voice events on the 29 Jul evening peak "
                "against a 2% ceiling. A switch or decoder defect is the likely cause."
            ),
            run_id="RUN-UA-77421", severity="medium", stream="MSC", node_id="MSC-EU-1",
            linked_batch="BATCH-441D", sub_module="Voice", expected_value="≤ 2.0%",
            actual_value="8.4%", variance="+6.4pp", variance_pct=6.4,
            estimated_impact=5400, affected_count=14820, detected_at=_ago(1, 19),
            dedupe_key="UA008:2026-07-29:MSC-EU-1",
            mismatches=[
                _m(record_ref=f"CDR885{1200 + i}", entity="MSC-EU-1",
                   subscriber=f"2547{10102233 + i * 67}", field="duration_sec",
                   expected_value="> 0", actual_value="0", status="ZERO_DURATION")
                for i in range(4)
            ],
        ), {}),

        ("CA002", dict(
            title="Negative balances above threshold — 640 prepaid subscribers",
            description=(
                "640 prepaid subscribers hold a negative balance totalling -$14,200, above the "
                "-$5,000 exposure ceiling. Credit control on OCS-2 is not rejecting sessions."
            ),
            run_id="RUN-CA-21004", severity="high", stream="Charging", node_id="OCS-2",
            linked_batch="OCS_20250729_18", sub_module="Prepaid", expected_value="≥ -5,000.00",
            actual_value="-14,200.00", variance="-9,200.00", estimated_impact=14200,
            affected_count=640, detected_at=_ago(1, 18), owner="Aarav Mehta",
            dedupe_key="CA002:2026-07-29:OCS-2",
            mismatches=[
                _m(record_ref=f"BAL{55210 + i}", entity="OCS-2",
                   subscriber=f"2547{10115566 + i * 83}", field="balance",
                   expected_value="≥ 0.00", actual_value=f"-{22.20 + i * 5:.2f}",
                   delta=f"-{22.20 + i * 5:.2f}", status="NEGATIVE_BALANCE")
                for i in range(3)
            ],
        ), dict(status="In Progress", action="Escalated to carrier")),

        ("BA005", dict(
            title="Credit note volume above threshold — cycle 06",
            description=(
                "Credits raised against cycle 06 reached 3.1% of invoiced revenue against a 1% "
                "ceiling, traced to the duplicated recurring-charge defect."
            ),
            run_id="RUN-BA-51032", severity="medium", stream="Billing", node_id="BILL-02",
            linked_batch="BILLRUN_2025_06", sub_module="Adjustments", expected_value="≤ 1.0%",
            actual_value="3.1%", variance="+2.1pp", variance_pct=2.1,
            estimated_impact=28700, affected_count=412, detected_at=_ago(9, 11),
            owner="super_admin", dedupe_key="BA005:2026-07-21:BILL-02",
        ), dict(status="Closed", action="Config fix raised",
                comments=[("super_admin", "Root cause was the duplicate charge lines; credits back within tolerance on cycle 07.")])),

        # ================= COMPLETENESS =================
        ("UA009", dict(
            title="Per-subscriber CDR completeness breach — 1,260 subscribers",
            description=(
                "1,260 subscribers with active sessions on the switch produced no mediated usage "
                "for the 30 Jul 02:00 window. The aggregate volume looks healthy, so this only "
                "shows at subscriber grain."
            ),
            run_id="RUN-UA-77428", severity="critical", stream="MSC", node_id="MSC-EU-2",
            linked_batch="BATCH-442A", sub_module="Voice", expected_value="1,260 subscribers",
            actual_value="0 mediated", variance="1,260 subscribers",
            estimated_impact=14800, affected_count=1260, detected_at=_ago(0, 3),
            dedupe_key="UA009:2026-07-30:MSC-EU-2",
            mismatches=_missing_rows("SUB", 4, "MSC-EU-2", field="mediated_usage"),
        ), {}),

        ("BA002", dict(
            title="Invoice line completeness — 340 rated events not invoiced",
            description=(
                "340 rated events for cycle 07 have no corresponding invoice line. All belong to "
                "accounts that changed plan mid-cycle."
            ),
            run_id="RUN-BA-51048", severity="critical", stream="Billing", node_id="BILL-01",
            linked_batch="BILLRUN_2025_07", sub_module="Cycle 07", expected_value="340 lines",
            actual_value="0 lines", variance="340 lines / 9,780.00",
            estimated_impact=9780, affected_count=340, detected_at=_ago(2, 8),
            owner="Priya Shah", dedupe_key="BA002:2026-07-28:BILL-01",
            mismatches=[
                _m(record_ref=f"RTE88{300 + i}", entity="BILL-01", field="invoice_line",
                   expected_value="present", actual_value="missing",
                   delta=f"{28.75 + i * 6:.2f}", status="NOT_INVOICED")
                for i in range(3)
            ],
        ), dict(status="In Progress", action="Adjusted & rebilled")),

        ("NA001", dict(
            title="Element feed completeness — SGSN-EU-4 short by 4,180 records",
            description=(
                "SGSN-EU-4 delivered 4,180 fewer records than the element counters report for the "
                "24 Jul window — a 1.8% shortfall against a 0.5% tolerance."
            ),
            run_id="RUN-NA-6618", severity="high", stream="Network", node_id="SGSN-EU-4",
            linked_batch="ELEM_20250724", sub_module="EU", expected_value="232,400",
            actual_value="228,220", variance="-4,180", variance_pct=-1.8,
            estimated_impact=11900, affected_count=4180, detected_at=_ago(6, 9),
            owner="Priya Shah", dedupe_key="NA001:2026-07-24:SGSN-EU-4",
            mismatches=_missing_rows("ELM", 3, "SGSN-EU-4", field="collector_record"),
        ), dict(status="Resolved", action="Re-ingest requested",
                comments=[("Priya Shah", "Element buffer flushed and re-collected; counts reconcile to 232,400.")])),

        ("ME001", dict(
            title="AIR ingest completeness — 6 files collected but never decoded",
            description=(
                "Six AIR files were collected on 23 Jul but never reached the decoder. They are "
                "still sitting in the landing directory."
            ),
            run_id="RUN-ME-90238", severity="critical", stream="AIR", node_id="NODE-1",
            linked_batch="AIR_20250723", sub_module="AIR ingest", expected_value="184 files",
            actual_value="178 files decoded", variance="6 files", estimated_impact=20400,
            affected_count=2940, detected_at=_ago(7, 6), owner="super_admin",
            dedupe_key="ME001:2026-07-23:NODE-1",
            mismatches=[
                _m(record_ref=f"AIR_20250723_{100 + i}", entity="NODE-1", field="decode_status",
                   expected_value="decoded", actual_value="pending", delta="~490 records",
                   status="NOT_DECODED")
                for i in range(3)
            ],
        ), dict(status="Closed", action="Re-ingest requested",
                comments=[("super_admin", "Landing directory drained and all six files decoded; counts reconcile.")])),
    ]


def seed_rules(db: Session) -> int:
    """Load any control rules from the catalog that aren't in the table yet.

    Idempotent per rule rather than "skip if the table is non-empty", so adding
    a rule to `rules_seed` picks it up on the next boot without a wipe. Existing
    rows are never overwritten — an operator may have edited them.
    """
    existing = set(db.execute(select(ControlRule.id)).scalars().all())
    missing = [ControlRule(**data) for data in rule_dicts() if data["id"] not in existing]
    if not missing:
        return 0
    db.add_all(missing)
    db.commit()
    return len(missing)


def seed_cases(db: Session) -> int:
    """Run each demo control and triage the case it raises.

    Idempotent per case via its dedupe key: a demo run whose case already exists
    is skipped, so new scenarios can be appended to `demo_runs()` and picked up
    on the next boot without disturbing the cases already being worked.
    """
    from app import case_service

    seen = {
        key for key in db.execute(select(Case.dedupe_key)).scalars().all() if key
    }

    created = 0
    # Oldest first, so the generated CASE-#### references ascend with time.
    for rule_id, run, triage in reversed(demo_runs()):
        if run.get("dedupe_key") in seen:
            continue
        mismatches = run.pop("mismatches", [])
        result = schemas.RuleRunResult(status="FAIL", mismatches=mismatches, **run)
        response = rule_service.record_run(db, rule_id, result)
        if response.case is None:
            continue

        case = db.get(Case, response.case.id)
        if case is None:
            continue

        # A case is cut when its finding is detected, so backdate created_at to
        # match — otherwise every demo row carries the same boot timestamp and
        # the newest-first list comes out in an arbitrary order.
        case.created_at = case.detected_at
        case.updated_at = case.detected_at
        if reference := triage.get("reference"):
            case.reference = reference
        if evidence := triage.get("evidence"):
            case.evidence = evidence
        db.commit()

        if status := triage.get("status"):
            case_service.update_case(
                db, case.id,
                schemas.CaseUpdate(status=status, action=triage.get("action"), actor="super_admin"),
            )
        elif action := triage.get("action"):
            case_service.update_case(db, case.id, schemas.CaseUpdate(action=action, actor="super_admin"))

        for author, body in triage.get("comments", []):
            case_service.add_comment(db, case.id, schemas.CommentIn(author=author, body=body))

        # Triage moved updated_at to now; put it back on the demo timeline so
        # "Updated 8h ago" reads sensibly.
        case = db.get(Case, case.id)
        if case is not None:
            case.updated_at = case.detected_at + timedelta(hours=6)
            if case.closed_at is not None:
                case.closed_at = case.updated_at
            db.commit()
        created += 1
    return created


def seed_all(db: Session) -> tuple[int, int]:
    """Rules first — cases are raised through them. Returns (rules, cases)."""
    rules = seed_rules(db)
    cases = seed_cases(db)
    return rules, cases
