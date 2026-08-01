"""The executive dashboard for Charging Assurance.

The companion of `service.py`, which does the same job for Rating. Both return
the shape in `schemas.AssuranceDashboardOut`, so the UI renders either with no
branch of its own — the router picks the provider from the assurance id.

EVERY FIGURE ON THIS DASHBOARD COMES FROM TWO TABLES:

    assurance.recon_ca910   AIR raw       vs AIR processed
    assurance.recon_ca913   AIR processed vs SDP processed

both in `rafms_rating`. Nothing else is read — not the source feeds, not the
file-sequence trackers, not the duplicate or record-sequence checks. If a panel
cannot be answered from those two tables it is not on this dashboard.

Money is in whole rupees, not crores, and `currency` says so. The AIR/SDP feeds
carry single transactions — the whole exposure is a few hundred rupees — so a
crore-scaled figure would round to zero and read as an empty dashboard.

See `queries.py` for why the SQL is built rather than written. Briefly: the
output columns are text and only partly castable, and a rule's "metric" is not
necessarily money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import ra_postgres
from app.modules.assurance_dashboard import queries as Q
from app.modules.reconciliation import repository
from app.modules.reconciliation.plan import STATUS_MATCH

log = get_logger("assurance.dashboard.charging")

ASSURANCE_ID = "charging"

#: The only two rules this dashboard reads, in the order their panels list them.
#: Named explicitly rather than derived from the assurance's rule list: the
#: charging assurance also has file-sequence, record-sequence and duplicate
#: checks, and those are deliberately NOT part of these figures.
SOURCE_RULES = ("CA910", "CA913")

#: The Exception Rate card is the exception: it reports the DUPLICATE-FILE rate
#: from this rule instead of the reconciliation exception rate.
#:
#: `assurance.recon_ca912` holds only the duplicates it found — every row is
#: status DUPLICATE — so a percentage taken from that table alone is 100% and
#: says nothing. The denominator comes from the rule's own execution record,
#: which stores how many files it examined to find them.
DUPLICATE_RULE = "CA912"

#: Every figure here is in whole rupees.
CURRENCY = "INR"

#: Whether to count only each rule's newest run.
#:
#: The output tables retain the last few executions (recon_keep_executions), so
#: with this False the same charging transaction is counted once per run that
#: produced it — recon_ca913 currently holds two runs of the SAME 292 rows, and
#: recon_ca910 four runs, giving 1,144 rows for 414 distinct records.
#:
#: False is what a plain `SELECT count(*) FROM recon_ca910 UNION ALL ...` in a
#: SQL client reports, and is what this dashboard is specified to match. Set it
#: True to count each transaction once, from the newest run only.
SCOPE_TO_LATEST_EXECUTION = False

#: How many days the trend panels show, counted back from the most recent EVENT
#: rather than from today — the feeds are loaded in batches, so wall-clock "now"
#: is routinely ahead of the newest record and a today-anchored window is empty.
TREND_DAYS = 30

#: The comparison window for each KPI's delta: this many days against the
#: preceding equal-length window. Matches service.py so the two dashboards'
#: deltas mean the same thing.
_DELTA_DAYS = 7

#: Daily buckets carried by the KPI sparklines.
_SPARK_DAYS = 14

_FINDING_LIMIT = 5
_CATEGORY_LIMIT = 6

#: Column-name fragments that mark a key column as naming a system rather than
#: identifying a record — what the "Top Charging Systems" panel groups by.
_NODE_HINTS = ("host", "node", "system", "source")


# --- The two rules -----------------------------------------------------------


async def _definitions() -> list[dict[str, Any]]:
    """The two source rules, in SOURCE_RULES order, skipping any that has never
    run — a rule with no execution has no output table rows to read."""
    everything = await repository.list_definitions(assurance_id=ASSURANCE_ID)
    by_id = {d["rule_id"]: d for d in everything}
    return [
        by_id[rule_id]
        for rule_id in SOURCE_RULES
        if rule_id in by_id and by_id[rule_id].get("output_table")
    ]


async def _output_columns(definitions: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The real columns of each rule's output table, keyed by rule id.

    Read from information_schema rather than inferred from the definition: when
    both sides of a comparison have the same source column name the compiler
    suffixes one (`msisdn` / `msisdn_t2`), so the definition's names and the
    table's names are not always the same.
    """
    wanted = {
        (d["output_schema"], d["output_table"]): d["rule_id"] for d in definitions
    }
    if not wanted:
        return {}
    pairs = ", ".join(
        f"({Q.literal(schema)}, {Q.literal(table)})" for schema, table in wanted
    )
    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE (table_schema, table_name) IN ({pairs})
        ORDER BY ordinal_position
        """,
    )
    columns: dict[str, list[str]] = {}
    for row in rows:
        rule_id = wanted.get((row["table_schema"], row["table_name"]))
        if rule_id:
            columns.setdefault(rule_id, []).append(row["column_name"])
    return columns


def _database(definitions: list[dict[str, Any]]) -> str:
    """Both output tables live in the same database — the one their rules read."""
    return definitions[0]["source_database"]


def _scan(definition: dict[str, Any], *projections: str) -> str:
    """One SELECT over a rule's output, projecting the given expressions
    alongside its id and status."""
    schema, table = definition["output_schema"], definition["output_table"]
    extra = "".join(f", {p}" for p in projections)
    where = ""
    if SCOPE_TO_LATEST_EXECUTION and definition.get("last_execution_id"):
        where = (
            "WHERE execution_id = CAST("
            f"{Q.literal(definition['last_execution_id'])} AS uuid)"
        )
    return f"""
        SELECT {Q.literal(definition["rule_id"])} AS rule_id, status{extra}
        FROM {Q.quote(schema)}.{Q.quote(table)}
        {where}
    """


#: Per-rule extra columns for a union: rule id -> [(output alias, expression)].
#: Each rule contributes its OWN expression under a common alias, because the
#: two output tables name the same concept differently — CA910's timestamp is
#: `air_rfl_rec_timestamp`, CA913's is `sdp_proc_timestamp`.
Projections = dict[str, list[tuple[str, str]]]


def _union(definitions: list[dict[str, Any]], projections: Projections) -> str:
    """The two rules' output as one relation."""
    return " UNION ALL ".join(
        _scan(d, *(f"{expr} AS {alias}" for alias, expr in projections[d["rule_id"]]))
        for d in definitions
    )


# --- Sections ----------------------------------------------------------------


async def _totals_and_daily(
    definitions: list[dict[str, Any]], columns: dict[str, list[str]]
) -> tuple[dict[str, float], dict[str, dict[str, float]], bool]:
    """Counts and exposure, in total and bucketed by event day.

    One pass over both tables feeds the KPI row, the validation trend and the
    risk trend, so the three can never disagree.
    """
    projections: Projections = {}
    has_amount = False
    for d in definitions:
        available = columns.get(d["rule_id"], [])
        pair = Q.amount_columns(d, available)
        has_amount = has_amount or pair is not None
        projections[d["rule_id"]] = [
            ("day", Q.event_date_sql(Q.key_columns(d, available))),
            ("exposure", Q.exposure_sql(pair)),
        ]

    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(definitions, projections)})
        SELECT day, status, count(*) AS rows, sum(exposure) AS exposure
        FROM recon GROUP BY day, status
        """,
    )

    totals = {"records": 0.0, "matched": 0.0, "exceptions": 0.0, "exposure": 0.0}
    daily: dict[str, dict[str, float]] = {}
    for row in rows:
        count = float(row["rows"] or 0)
        exposure = float(row["exposure"] or 0)
        matched = row["status"] == STATUS_MATCH

        totals["records"] += count
        totals["matched" if matched else "exceptions"] += count
        if not matched:
            totals["exposure"] += exposure

        # Rows whose timestamp columns are blank have day IS NULL. They count in
        # the totals — they are real records — but cannot be placed on a time
        # axis, so they are absent from the series rather than piled into an
        # arbitrary bucket.
        if row["day"] is None:
            continue
        bucket = daily.setdefault(
            row["day"], {"healthy": 0.0, "exceptions": 0.0, "exposure": 0.0}
        )
        bucket["healthy" if matched else "exceptions"] += count
        if not matched:
            bucket["exposure"] += exposure

    return totals, daily, has_amount


async def _exception_categories(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exceptions by what went wrong, named in the rules' own table terms.

    The categories are the reconciliation statuses: a record missing from one
    side, or present on both with amounts that disagree. Labels merge across the
    two rules, so both failing to find a record in `air_processed` is one
    category rather than two.
    """
    projections: Projections = {d["rule_id"]: [] for d in definitions}
    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(definitions, projections)})
        SELECT rule_id, status, count(*) AS rows FROM recon
        WHERE status <> {Q.literal(STATUS_MATCH)}
        GROUP BY rule_id, status
        """,
    )
    by_rule = {d["rule_id"]: d for d in definitions}
    totals: dict[str, float] = {}
    for row in rows:
        definition = by_rule[row["rule_id"]]
        label = Q.status_phrase(
            row["status"],
            Q.short_table(definition.get("left_table")),
            Q.short_table(definition.get("right_table")),
        )
        totals[label] = totals.get(label, 0.0) + float(row["rows"] or 0)

    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:_CATEGORY_LIMIT]
    return [{"name": name, "value": value} for name, value in ranked]


async def _leakage_categories(
    definitions: list[dict[str, Any]], columns: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """The same categories as above, weighted by money rather than by count.

    These values sum to the Revenue at Risk KPI.
    """
    projections: Projections = {
        d["rule_id"]: [
            ("exposure", Q.exposure_sql(Q.amount_columns(d, columns.get(d["rule_id"], []))))
        ]
        for d in definitions
    }
    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(definitions, projections)})
        SELECT rule_id, status, sum(exposure) AS exposure FROM recon
        WHERE status <> {Q.literal(STATUS_MATCH)}
        GROUP BY rule_id, status
        """,
    )
    by_rule = {d["rule_id"]: d for d in definitions}
    totals: dict[str, float] = {}
    for row in rows:
        definition = by_rule[row["rule_id"]]
        label = Q.status_phrase(
            row["status"],
            Q.short_table(definition.get("left_table")),
            Q.short_table(definition.get("right_table")),
        )
        totals[label] = totals.get(label, 0.0) + float(row["exposure"] or 0)

    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:_CATEGORY_LIMIT]
    return [{"name": name, "value": round(value, 2)} for name, value in ranked]


def _node_columns(definition: dict[str, Any], available: list[str]) -> list[str]:
    """The key columns that name a charging system rather than identify a record."""
    return [
        c
        for c in Q.key_columns(definition, available)
        if any(hint in c.lower() for hint in _NODE_HINTS)
    ]


async def _entities(
    definitions: list[dict[str, Any]], columns: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Exceptions attributed to the charging node that produced the record.

    Only rules that joined on a host or node column can attribute anything —
    CA910 carries the AIR node name, CA913 joins on account and subscriber and
    so contributes nothing here. A rule with no node column is skipped rather
    than bucketed as "unknown", which would dominate the panel.
    """
    usable = [
        d for d in definitions if _node_columns(d, columns.get(d["rule_id"], []))
    ]
    if not usable:
        return []

    projections: Projections = {
        d["rule_id"]: [("node", Q.subject_sql(_node_columns(d, columns[d["rule_id"]])))]
        for d in usable
    }
    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(usable, projections)})
        SELECT node, count(*) AS rows FROM recon
        WHERE status <> {Q.literal(STATUS_MATCH)} AND node IS NOT NULL
        GROUP BY node ORDER BY count(*) DESC LIMIT {_CATEGORY_LIMIT}
        """,
    )
    return [{"name": row["node"], "value": float(row["rows"] or 0)} for row in rows]


async def _business_segments(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records evaluated per reconciliation — which comparison they came from.

    The segments are the two rules, named after the systems each one compares,
    so the donut answers "where did the evaluated records come from" using only
    the two tables this dashboard reads.
    """
    projections: Projections = {d["rule_id"]: [] for d in definitions}
    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(definitions, projections)})
        SELECT rule_id, count(*) AS rows FROM recon GROUP BY rule_id
        """,
    )
    by_rule = {d["rule_id"]: d for d in definitions}
    segments = []
    for row in rows:
        definition = by_rule[row["rule_id"]]
        left = Q.short_table(definition.get("left_table"))
        right = Q.short_table(definition.get("right_table"))
        segments.append({"name": f"{left} ↔ {right}", "value": float(row["rows"] or 0)})
    segments.sort(key=lambda s: -s["value"])
    return segments


async def _findings(
    definitions: list[dict[str, Any]], columns: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """The individual exception rows costing the most, across both rules."""
    usable = []
    projections: Projections = {}
    for d in definitions:
        available = columns.get(d["rule_id"], [])
        pair = Q.amount_columns(d, available)
        if not pair:
            # Without an amount there is no impact to rank the row by, and this
            # panel is ordered by financial exposure.
            continue
        usable.append(d)
        projections[d["rule_id"]] = [
            ("subject", Q.subject_sql(Q.key_columns(d, available))),
            ("exposure", Q.exposure_sql(pair)),
        ]
    if not usable:
        return []

    rows = await ra_postgres.query_database(
        _database(definitions),
        f"""
        WITH recon AS ({_union(usable, projections)})
        SELECT rule_id, status, subject, exposure FROM recon
        WHERE status <> {Q.literal(STATUS_MATCH)} AND exposure > 0
        ORDER BY exposure DESC LIMIT {_FINDING_LIMIT}
        """,
    )
    by_rule = {d["rule_id"]: d for d in definitions}
    findings = []
    for row in rows:
        definition = by_rule[row["rule_id"]]
        left = Q.short_table(definition.get("left_table"))
        right = Q.short_table(definition.get("right_table"))
        phrase = Q.status_phrase(row["status"], left, right)
        findings.append({
            "finding": f"{phrase} · {row['subject']}" if row["subject"] else phrase,
            "source": left,
            "category": Q.rule_label(definition),
            # Named impactCr by the wire contract; the value is whole rupees, as
            # `currency` on the payload says.
            "impactCr": round(float(row["exposure"] or 0), 2),
        })
    return findings


async def _duplicate_rate() -> tuple[float, int, int] | None:
    """How much of the delivered AIR raw feed was a redundant copy.

    Returns (percentage, duplicates, files examined), or None when the rule has
    never run.

    The numerator is the duplicate rows the rule wrote; the denominator is the
    file count it scanned to find them, recorded on the execution. Both come
    from the duplicate rule itself — nothing else is read.

    When the execution's scanned count is missing or implausible (older runs of
    this rule recorded the row count of a different table), the denominator is
    rebuilt from the rows' own `remarks`, which say "occurrence 3 of 4" and so
    carry the size of each duplicate group.
    """
    definition = await repository.get_definition(DUPLICATE_RULE)
    if not definition or not definition.get("last_execution_id"):
        return None

    execution_id = definition["last_execution_id"]
    schema, table = definition["output_schema"], definition["output_table"]

    rows = await ra_postgres.query_database(
        definition["source_database"],
        f"""
        SELECT count(*) AS duplicates,
               -- "occurrence 3 of 4" -> 4. One row per duplicate, so a group of
               -- N contributes N-1 rows and the originals are never listed.
               coalesce(sum(
                   CASE WHEN (regexp_match(remarks, 'of ([0-9]+)'))[1] ~ '^[0-9]+$'
                        THEN (regexp_match(remarks, 'of ([0-9]+)'))[1]::int
                   END
               ), 0) AS group_total
        FROM {Q.quote(schema)}.{Q.quote(table)}
        WHERE execution_id = CAST({Q.literal(execution_id)} AS uuid)
        """,
    )
    duplicates = int(rows[0]["duplicates"] or 0) if rows else 0
    if not duplicates:
        return None

    scanned_rows = await ra_postgres.query_database(
        settings.app_rules_db_name,
        f"""
        SELECT rows_scanned FROM {settings.app_rules_schema}.recon_execution
        WHERE execution_id = CAST({Q.literal(execution_id)} AS uuid)
        """,
    )
    scanned = int(scanned_rows[0]["rows_scanned"] or 0) if scanned_rows else 0

    if scanned < duplicates:
        # Fall back to the files that took part in a duplicate group. A group of
        # N appears as N-1 rows each saying "of N", so the distinct files
        # involved are sum(N)/(N-1) per group — recovered here as the summed
        # group sizes divided by the occurrences seen.
        group_total = int(rows[0]["group_total"] or 0)
        scanned = round(group_total / 2) if group_total else duplicates

    return round(duplicates / scanned * 100, 2), duplicates, scanned


# --- Assembly ----------------------------------------------------------------


def _kpi(value: str, *, delta: float, higher_is_better: bool, spark: list[float]) -> dict[str, Any]:
    return {
        "value": value,
        "delta": delta,
        "higherIsBetter": higher_is_better,
        "spark": spark,
    }


def _title(day: str) -> str:
    """`2026-07-02` -> `02 Jul`, the axis label service.py also uses.

    The days are ISO strings rather than dates because they are cut out of text
    columns in SQL; anything unparseable is shown as-is rather than dropped.
    """
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b")
    except ValueError:
        return day


def _pct_delta(current: float, prior: float) -> float:
    """Percentage change, with no prior activity reported as 0 rather than
    +100% — a first day of data is not an improvement on anything."""
    return round((current - prior) / prior * 100, 2) if prior else 0.0


def _window(days: list[str], daily: dict[str, dict[str, float]], key: str,
            offset: int) -> float:
    """One measure summed over a window of `_DELTA_DAYS` ending `offset` days
    before the newest event — the same anchoring as service.py, for the same
    reason: the feeds are loaded in batches, so wall-clock windows are empty."""
    end = len(days) - offset
    return sum(daily[day][key] for day in days[max(0, end - _DELTA_DAYS):end])


def _empty() -> dict[str, Any]:
    """The payload when neither rule has produced output yet."""
    blank = _kpi("0", delta=0.0, higher_is_better=True, spark=[])
    return {
        "id": ASSURANCE_ID,
        "name": "Charging Assurance",
        "subtitle": "No charging reconciliation output yet",
        "recordUnit": "Transactions",
        "currency": CURRENCY,
        "kpis": {
            "revenueAtRisk": blank, "recordsEvaluated": blank, "exceptions": blank,
            "exceptionRate": blank, "reconciliation": blank,
        },
        "trendTitle": "Charging Validation Trend",
        "trend": [], "revenueAtRisk": [], "exceptionCategories": [],
        "entitiesTitle": "Top Charging Systems", "entities": [],
        "leakageCategories": [], "businessSegments": [], "findings": [],
    }


async def build_dashboard() -> dict[str, Any]:
    """The whole dashboard payload for Charging Assurance."""
    definitions = await _definitions()
    if not definitions:
        return _empty()
    columns = await _output_columns(definitions)

    totals, daily, has_amount = await _totals_and_daily(definitions, columns)

    records, matched = totals["records"], totals["matched"]
    exceptions, exposure = totals["exceptions"], totals["exposure"]
    exception_rate = round(exceptions / records * 100, 2) if records else 0.0
    reconciliation = round(matched / records * 100, 2) if records else 0.0

    # The window ends at the newest event, and trimming to it also drops the
    # epoch-dated rows the AIR raw feed carries for records whose timestamp
    # never survived decoding. Those still count in the totals above.
    days = sorted(daily)[-TREND_DAYS:]

    r_records = _window(days, daily, "healthy", 0) + _window(days, daily, "exceptions", 0)
    p_records = _window(days, daily, "healthy", _DELTA_DAYS) + _window(days, daily, "exceptions", _DELTA_DAYS)
    r_exceptions = _window(days, daily, "exceptions", 0)
    p_exceptions = _window(days, daily, "exceptions", _DELTA_DAYS)
    r_rate = (r_exceptions / r_records * 100) if r_records else 0.0
    p_rate = (p_exceptions / p_records * 100) if p_records else 0.0
    r_recon = ((r_records - r_exceptions) / r_records * 100) if r_records else 0.0
    p_recon = ((p_records - p_exceptions) / p_records * 100) if p_records else 0.0

    # The Exception Rate card reports the duplicate-file rate, not the
    # reconciliation exception rate. It falls back to the reconciliation figure
    # only if the duplicate rule has never run.
    duplicates = await _duplicate_rate()

    recent = days[-_SPARK_DAYS:]
    spark = lambda key: [round(daily[d][key], 2) for d in recent]  # noqa: E731
    span = f"{_title(days[0])} – {_title(days[-1])}" if days else ""

    return {
        "id": ASSURANCE_ID,
        "name": "Charging Assurance",
        "subtitle": (
            f"{int(records):,} charging transactions reconciled across the AIR and"
            " SDP feeds" + (f" · {span}" if span else "")
        ),
        "recordUnit": "Transactions",
        "currency": CURRENCY,
        "kpis": {
            # A rule that compares no amount gets no exposure figure rather than
            # a zero — "no amount column" and "nothing at risk" are different
            # facts. The dash is what the card shows for the former.
            "revenueAtRisk": _kpi(
                f"{exposure:,.2f}" if has_amount else "—",
                delta=_pct_delta(
                    _window(days, daily, "exposure", 0),
                    _window(days, daily, "exposure", _DELTA_DAYS),
                ),
                higher_is_better=False,
                spark=spark("exposure"),
            ),
            "recordsEvaluated": _kpi(
                f"{int(records):,}",
                delta=_pct_delta(r_records, p_records),
                higher_is_better=True,
                spark=[round(daily[d]["healthy"] + daily[d]["exceptions"], 2) for d in recent],
            ),
            "exceptions": _kpi(
                f"{int(exceptions):,}",
                delta=_pct_delta(r_exceptions, p_exceptions),
                higher_is_better=False,
                spark=spark("exceptions"),
            ),
            # Duplicate-file rate, from CA912 — see DUPLICATE_RULE. No delta or
            # sparkline: the duplicate check produces one figure per run, not a
            # daily series, and an invented trend beside a real number is worse
            # than no trend.
            "exceptionRate": _kpi(
                f"{duplicates[0]:.2f}%" if duplicates else f"{exception_rate:.2f}%",
                delta=0.0 if duplicates else _pct_delta(r_rate, p_rate),
                higher_is_better=False,
                spark=[] if duplicates else spark("exceptions"),
            ),
            "reconciliation": _kpi(
                f"{reconciliation:.2f}%",
                delta=_pct_delta(r_recon, p_recon),
                higher_is_better=True,
                spark=spark("healthy"),
            ),
        },
        "trendTitle": "Charging Validation Trend",
        "trend": [
            {
                "label": _title(day),
                "healthy": daily[day]["healthy"],
                "exceptions": daily[day]["exceptions"],
                "leakage": round(daily[day]["exposure"], 2),
            }
            for day in days
        ],
        "revenueAtRisk": [
            {"label": _title(day), "value": round(daily[day]["exposure"], 2)}
            for day in days
        ],
        "exceptionCategories": await _exception_categories(definitions),
        "entitiesTitle": "Top Charging Systems",
        "entities": await _entities(definitions, columns),
        "leakageCategories": await _leakage_categories(definitions, columns),
        "businessSegments": await _business_segments(definitions),
        "findings": await _findings(definitions, columns),
    }
