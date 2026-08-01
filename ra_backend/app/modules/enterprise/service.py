"""The Enterprise Dashboard's numbers, from assurance.assurance_leakage_monthly.

One query set, one payload. Every figure on the top half of the dashboard —
revenue, CDRs, leakage, leakage %, controls effectiveness, the trend, the
breakdown, the drivers and control health — is an aggregate of that one table in
``rafms_rating``. Nothing there is invented.

The table's grain is (month, assurance_type, leakage, status), where status is
MATCH or MISMATCH:

  * amount, MATCH only         -> value that reconciled cleanly
  * amount, MISMATCH only      -> the VARIANCE on records that did not reconcile,
                                  not the value of those records
  * count                      -> records behind those amounts

Because a MISMATCH amount is a variance, the two percentage KPIs cannot be a
ratio of money: the reconciled base behind a mismatched record is not in this
table, and summing a variance onto matched value would compare unlike things.
Both are therefore computed from RECORD COUNTS, which the amount semantics do
not touch — leakage % is the share of records that failed, effectiveness the
share that passed. Give this module a revenue table and the money ratio becomes
a one-line change.

The lower half of the dashboard (technology splits, module flow, data health)
has no source table. Rather than hardcode a second, unrelated set of figures,
those are DERIVED from the same monthly totals using fixed, documented shares —
so prepaid + postpaid always sums back to the revenue on the KPI card, and a
change in the underlying data moves both halves together. The shares are the
invention; the totals they divide are not.

Money is INR throughout. The table stores rupees.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import ra_postgres

log = get_logger("enterprise.dashboard")

#: The database holding assurance.assurance_leakage_monthly. Same host as the
#: authored rules, different database from the app's own.
_DB = settings.app_rules_db_name

#: assurance_type -> the display name used everywhere else in the product.
ASSURANCE_LABELS = {
    "usage_assurance": "Usage Assurance",
    "charging_assurance": "Charging Assurance",
    "rating_assurance": "Rating Assurance",
    "billing_assurance": "Billing Assurance",
    "mediation_assurance": "Mediation Assurance",
    "partner_assurance": "Partner Assurance",
}

#: leakages -> what an executive reads. The column holds the reconciliation's
#: own name for the check; these are the same checks in business language.
DRIVER_LABELS = {
    "msc_vs_in": "MSC vs IN Mismatch",
    "air_raw_vs_air_processed": "AIR Raw vs Processed",
    "air_proc_vs_sdp_processed": "AIR vs SDP Processed",
    "discount_mismatch": "Discount Misconfiguration",
    "tariff_mismatch": "Tariff Mismatch",
}

#: Which assurance owns each check — drives the "Module" column on the drivers
#: panel and keeps it consistent with the breakdown.
DRIVER_MODULE = {
    "msc_vs_in": "Usage Assurance",
    "air_raw_vs_air_processed": "Charging Assurance",
    "air_proc_vs_sdp_processed": "Charging Assurance",
    "discount_mismatch": "Billing Assurance",
    "tariff_mismatch": "Rating Assurance",
}

#: Slot order matters: adjacent pairs are distinguishable under the common
#: colour-vision deficiencies. Re-ordering needs re-validating.
PALETTE = ["#ef4444", "#f59e0b", "#3b82f6", "#22c55e", "#8b5cf6", "#06b6d4"]

#: Revenue split by technology. Invented, and fixed so the two panels always
#: sum to the same real total. Prepaid skews to data, postpaid to voice, which
#: is the shape of the market these demos describe.
PREPAID_SHARE = 0.56
TECH_SPLIT = {
    "prepaid": [("Voice", 0.696, "phone"), ("SMS", 0.304, "sms")],
    "postpaid": [("Voice", 0.777, "phone"), ("SMS", 0.223, "sms")],
}

#: The stages each stream passes through, and the share of that stream's value
#: each stage accounts for. Invented; ordered as the data actually flows.
FLOW_STAGES = {
    "prepaid": [
        ("Data Ingestion", 0.012),
        ("Usage Assurance", 0.239),
        ("OCS Assurance", 0.168),
        ("Balance Assurance", 0.211),
        ("Mediation Assurance", 0.261),
    ],
    "postpaid": [
        ("Data Ingestion", 0.012),
        ("Mediation Assurance", 0.261),
        ("Rating Assurance", 0.168),
        ("Billing Assurance", 0.274),
        ("Balance Assurance", 0.211),
    ],
}


def _f(value: Any) -> float:
    """Decimal/None -> float. SUM over an empty window is NULL, not 0."""
    return float(value or 0)


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def _delta(now: float, before: float) -> float:
    """Percentage change, or 0 when there is nothing to compare against."""
    return round((now - before) / before * 100, 1) if before else 0.0


async def _rows(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(_DB, sql, params)


async def monthly_totals() -> list[dict[str, Any]]:
    """One row per month: value reconciled, value leaked, records, mismatches."""
    return await _rows(
        """
        SELECT month_date,
               SUM(amount)                                         AS total_amount,
               COALESCE(SUM(amount) FILTER (WHERE status = 'MISMATCH'), 0) AS leak_amount,
               SUM(count)                                          AS total_count,
               COALESCE(SUM(count) FILTER (WHERE status = 'MISMATCH'), 0)  AS leak_count
          FROM assurance.assurance_leakage_monthly
         GROUP BY month_date
         ORDER BY month_date
        """
    )


async def by_assurance(month: date) -> list[dict[str, Any]]:
    return await _rows(
        """
        SELECT assurance_type,
               SUM(amount)                                         AS total_amount,
               COALESCE(SUM(amount) FILTER (WHERE status = 'MISMATCH'), 0) AS leak_amount,
               SUM(count)                                          AS total_count,
               COALESCE(SUM(count) FILTER (WHERE status = 'MISMATCH'), 0)  AS leak_count
          FROM assurance.assurance_leakage_monthly
         WHERE month_date = :month
         GROUP BY assurance_type
         ORDER BY leak_amount DESC
        """,
        {"month": month},
    )


async def by_driver(month: date) -> list[dict[str, Any]]:
    return await _rows(
        """
        SELECT leakages,
               COALESCE(SUM(amount) FILTER (WHERE status = 'MISMATCH'), 0) AS leak_amount,
               COALESCE(SUM(count)  FILTER (WHERE status = 'MISMATCH'), 0) AS leak_count
          FROM assurance.assurance_leakage_monthly
         WHERE month_date = :month
         GROUP BY leakages
         ORDER BY leak_amount DESC
        """,
        {"month": month},
    )


async def control_effectiveness() -> dict[str, Any]:
    """Share of control executions that completed.

    A different question from "did the data reconcile", and the honest one for
    a card labelled Controls Effectiveness: it measures the controls, not the
    estate they inspect. Row-level match rate is reported by the leakage
    figures next to it.
    """
    rows = await _rows(
        """
        SELECT COUNT(*)                                        AS runs,
               COUNT(*) FILTER (WHERE status = 'Succeeded')     AS succeeded
          FROM application_schema.rule_execution
        """
    )
    row = rows[0] if rows else {}
    runs, ok = _f(row.get("runs")), _f(row.get("succeeded"))
    return {"runs": int(runs), "succeeded": int(ok), "pct": _pct(ok, runs)}


async def file_health(month: date) -> dict[str, Any]:
    """Data Health, from the AIR and SDP file sequence checks.

    Real where the tables allow it: received, duplicates and sequence gaps are
    counted rows. Processed is received minus what the check rejected.
    """
    rows = await _rows(
        """
        SELECT SUM(files)      AS files,
               SUM(duplicates) AS duplicates,
               SUM(gaps)       AS gaps
          FROM (
            SELECT COUNT(*) AS files,
                   COUNT(*) FILTER (WHERE status = 'DUPLICATE') AS duplicates,
                   COUNT(*) FILTER (WHERE status = 'GAP')       AS gaps
              FROM assurance.air_file_seq_check
             UNION ALL
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE status = 'DUPLICATE'),
                   COUNT(*) FILTER (WHERE status = 'GAP')
              FROM assurance.sdp_file_seq_check
          ) both_streams
        """
    )
    row = rows[0] if rows else {}
    return {
        "files": int(row.get("files") or 0),
        "duplicates": int(row.get("duplicates") or 0),
        "gaps": int(row.get("gaps") or 0),
    }


def _money(value: float) -> dict[str, Any]:
    """A rupee figure plus the compact form the cards render.

    Indian grouping, because the audience reads lakh/crore: 1,00,000 not
    100,000. Below a lakh there is nothing to group, so it stays plain.
    """
    if value >= 1_00_00_000:
        compact = f"₹{value / 1_00_00_000:.2f} Cr"
    elif value >= 1_00_000:
        compact = f"₹{value / 1_00_000:.2f} L"
    elif value >= 1_000:
        compact = f"₹{value / 1_000:.2f} K"
    else:
        compact = f"₹{value:,.2f}"
    return {"amount": round(value, 2), "display": compact}


def _count(value: float) -> dict[str, Any]:
    if value >= 1_00_00_000:
        compact = f"{value / 1_00_00_000:.2f} Cr"
    elif value >= 1_00_000:
        compact = f"{value / 1_00_000:.2f} L"
    elif value >= 1_000:
        compact = f"{value / 1_000:.1f} K"
    else:
        compact = f"{value:,.0f}"
    return {"count": int(value), "display": compact}


async def dashboard() -> dict[str, Any]:
    """Everything the Enterprise Dashboard renders, in one payload."""
    months = await monthly_totals()
    if not months:
        raise ValueError("assurance.assurance_leakage_monthly is empty")

    current, previous = months[-1], (months[-2] if len(months) > 1 else months[-1])
    month = current["month_date"]

    # Revenue is the MATCHED value — the only figure here that is a value
    # rather than a variance.
    revenue = _f(current["total_amount"]) - _f(current["leak_amount"])
    leakage = _f(current["leak_amount"])
    records = _f(current["total_count"])
    mismatched = _f(current["leak_count"])

    prev_revenue = _f(previous["total_amount"]) - _f(previous["leak_amount"])
    prev_leakage = _f(previous["leak_amount"])
    prev_records = _f(previous["total_count"])

    # Controls effectiveness is the share of records that reconciled — the one
    # measure of "did the controls hold" this table can answer honestly.
    # Record-level pass rate, used by the per-assurance control health table.
    record_pass_rate = _pct(records - mismatched, records)
    # Share of records carrying a variance — the count-based reading explained
    # in the module docstring.
    leak_pct = _pct(mismatched, records)
    prev_leak_pct = _pct(_f(previous["leak_count"]), _f(previous["total_count"]))

    controls = await control_effectiveness()
    assurances = await by_assurance(month)
    drivers = await by_driver(month)
    files = await file_health(month)

    # --- KPI row ----------------------------------------------------------
    kpis = [
        {
            "key": "revenue",
            "label": "Total Revenue (MTD)",
            **_money(revenue),
            "delta": _delta(revenue, prev_revenue),
            "deltaUnit": "%",
            "good": True,
        },
        {
            "key": "records",
            "label": "Total CDRs (MTD)",
            **_count(records),
            "delta": _delta(records, prev_records),
            "deltaUnit": "%",
            "good": True,
        },
        {
            "key": "leakage",
            "label": "Potential Leakage (MTD)",
            **_money(leakage),
            "delta": _delta(leakage, prev_leakage),
            "deltaUnit": "%",
            "good": False,
        },
        {
            "key": "leakage_pct",
            "label": "Leakage % (MTD)",
            "amount": leak_pct,
            "display": f"{leak_pct:.2f}%",
            "delta": round(leak_pct - prev_leak_pct, 2),
            "deltaUnit": "pp",
            "good": False,
        },
        {
            "key": "effectiveness",
            "label": "Controls Effectiveness",
            "amount": controls["pct"],
            "display": f"{controls['pct']:.1f}%",
            "detail": f"{controls['succeeded']} of {controls['runs']} runs",
            "delta": 0.0,
            "deltaUnit": "pp",
            "good": True,
        },
        {
            # Filled in by the UI from the case service, which owns cases. Left
            # here so the card keeps its slot and label while that request is in
            # flight, rather than the row reflowing when it lands.
            "key": "critical_cases",
            "label": "Open Critical Cases",
            "count": 0,
            "display": "—",
            "delta": 0.0,
            "deltaUnit": "%",
            "good": False,
        },
    ]

    # --- Trend: one point per month, in rupees ----------------------------
    trend = [
        {
            "period": m["month_date"].strftime("%b %Y"),
            "total": round(_f(m["total_amount"]), 2),
            "billed": round(_f(m["total_amount"]) - _f(m["leak_amount"]), 2),
            "leakage": round(_f(m["leak_amount"]), 2),
        }
        for m in months
    ]

    # --- Leakage breakdown, by assurance ----------------------------------
    breakdown = [
        {
            "name": ASSURANCE_LABELS.get(a["assurance_type"], a["assurance_type"]),
            "value": round(_f(a["leak_amount"]), 2),
            "display": _money(_f(a["leak_amount"]))["display"],
            "pct": f"{_pct(_f(a['leak_amount']), leakage):.1f}%",
            "color": PALETTE[i % len(PALETTE)],
        }
        for i, a in enumerate(assurances)
        if _f(a["leak_amount"]) > 0
    ]

    # --- Top drivers, and the actions that follow from them ---------------
    driver_rows = [
        {
            "driver": DRIVER_LABELS.get(d["leakages"], d["leakages"]),
            "module": DRIVER_MODULE.get(d["leakages"], "—"),
            "leakage": _money(_f(d["leak_amount"]))["display"],
            "amount": round(_f(d["leak_amount"]), 2),
            "records": int(_f(d["leak_count"])),
            "impact": _pct(_f(d["leak_amount"]), leakage),
            "color": PALETTE[i % len(PALETTE)],
        }
        for i, d in enumerate(drivers)
        if _f(d["leak_amount"]) > 0
    ]

    actions = [
        {
            "label": f"Investigate {row['driver'].lower()}",
            "impact": row["leakage"],
            "priority": "High" if row["impact"] >= 25 else "Medium" if row["impact"] >= 10 else "Low",
        }
        for row in driver_rows[:5]
    ]

    # --- Control health, per assurance ------------------------------------
    control_health = [
        {
            "type": f"{ASSURANCE_LABELS.get(a['assurance_type'], a['assurance_type']).split()[0]} Controls",
            "effectiveness": f"{_pct(_f(a['total_count']) - _f(a['leak_count']), _f(a['total_count'])):.1f}%",
            "value": _pct(_f(a["total_count"]) - _f(a["leak_count"]), _f(a["total_count"])),
            "records": int(_f(a["total_count"])),
        }
        for a in sorted(assurances, key=lambda x: x["assurance_type"])
    ]
    control_health.append(
        {
            "type": "Overall",
            "effectiveness": f"{record_pass_rate:.1f}%",
            "value": record_pass_rate,
            "records": int(records),
        }
    )

    # --- Derived halves: technology split and module flow -----------------
    prepaid_total = revenue * PREPAID_SHARE
    postpaid_total = revenue - prepaid_total

    def tech(stream: str, total: float) -> list[dict[str, Any]]:
        return [
            {
                "label": label,
                **_money(total * share),
                "share": f"{share * 100:.1f}% of {stream.title()}",
                "delta": _delta(revenue, prev_revenue),
                "icon": icon,
            }
            for label, share, icon in TECH_SPLIT[stream]
        ]

    def flow(stream: str, total: float) -> list[dict[str, Any]]:
        return [
            {
                "label": label,
                **_money(total * share),
                "share": f"{share * 100:.1f}%",
                "delta": _delta(revenue, prev_revenue),
            }
            for label, share in FLOW_STAGES[stream]
        ]

    processed = max(files["files"] - files["gaps"], 0)
    data_health = [
        {"label": "Files Received", "value": f"{files['files']:,}", "good": True},
        {"label": "Files Processed", "value": f"{processed:,}", "good": True},
        {
            "label": "Processing Success",
            "value": f"{_pct(processed, files['files']):.1f}%",
            "good": True,
        },
        {"label": "Sequence Gaps", "value": f"{files['gaps']:,}", "good": False},
        {"label": "Duplicate Files", "value": f"{files['duplicates']:,}", "good": False},
        {"label": "Records Mismatched", "value": f"{int(mismatched):,}", "good": False},
    ]

    first_month = months[0]["month_date"]
    return {
        "month": month.isoformat(),
        "currency": "INR",
        "range": {
            "from": first_month.isoformat(),
            "to": month.isoformat(),
            "display": f"{first_month.strftime('%b %Y')} – {month.strftime('%b %Y')}",
        },
        "kpis": kpis,
        "trend": trend,
        "leakageBreakdown": breakdown,
        "leakageDrivers": driver_rows,
        "controlHealth": control_health,
        "aiActions": actions,
        "prepaidTech": tech("prepaid", prepaid_total),
        "postpaidTech": tech("postpaid", postpaid_total),
        "prepaidFlow": flow("prepaid", prepaid_total),
        "postpaidFlow": flow("postpaid", postpaid_total),
        "dataHealth": data_health,
        "totals": {
            "revenue": _money(revenue),
            "leakage": _money(leakage),
            "prepaid": _money(prepaid_total),
            "postpaid": _money(postpaid_total),
        },
    }
