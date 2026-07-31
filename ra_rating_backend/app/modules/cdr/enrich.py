"""Enrichment (§10) and the rating-context key (§11).

Enrichment answers "what situation is this CDR in?" — which product, which
destination zone, which time band. Those answers are what rule selection joins
on, so a wrong answer here becomes a false exception downstream rather than a
crash. Every lookup therefore records *why* it failed rather than defaulting
silently: ``DESTINATION_NOT_FOUND`` is actionable, a null zone is not.

Reference data is loaded once per batch into an in-memory index and reused for
every row. At 1 crore rows a per-row query is not an optimisation problem, it is
an impossibility.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.cdr.models import NetworkPrefix, SubscriberProduct


class QualityStatus:
    OK = "OK"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    SUBSCRIBER_NOT_FOUND = "SUBSCRIBER_NOT_FOUND"
    DESTINATION_NOT_FOUND = "DESTINATION_NOT_FOUND"
    TIME_BAND_NOT_FOUND = "TIME_BAND_NOT_FOUND"
    INVALID_DURATION = "INVALID_DURATION"
    REFERENCE_DATA_MISSING = "REFERENCE_DATA_MISSING"


#: The dimensions that make up the rating context, in key order. Fixed, because
#: the key is hashed — reordering it would invalidate every cached mapping.
CONTEXT_DIMENSIONS: tuple[str, ...] = (
    "service_type",
    "product_code",
    "offer_code",
    "tariff_plan_code",
    "destination_zone",
    "origin_zone",
    "time_band",
    "account_type",
    "roaming",
    "network_type",
    "rating_group",
    "on_net",
)


@dataclass
class TimeBandEntry:
    code: str
    days: frozenset[str]
    start: time
    end: time
    timezone: str
    priority: int

    def covers(self, moment: datetime) -> bool:
        try:
            local = moment.astimezone(ZoneInfo(self.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            local = moment
        weekday = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[local.weekday()]
        if weekday not in self.days:
            return False
        clock = local.time()
        if self.start <= self.end:
            return self.start <= clock < self.end
        # A band that wraps midnight (22:00 → 06:00) is two intervals in one row.
        return clock >= self.start or clock < self.end


@dataclass
class ReferenceIndex:
    """Everything enrichment needs, loaded once per batch."""

    #: (prefix, zone_code) sorted longest-first, so the first match is the
    #: longest match — 447 (UK mobile) must beat 44 (UK).
    prefixes: list[tuple[str, str]] = field(default_factory=list)
    zone_types: dict[str, str] = field(default_factory=dict)
    home_prefixes: list[str] = field(default_factory=list)
    time_bands: list[TimeBandEntry] = field(default_factory=list)
    #: msisdn -> its effective-dated product assignments.
    subscriber_products: dict[str, list[SubscriberProduct]] = field(default_factory=dict)
    product_account_type: dict[str, str] = field(default_factory=dict)
    known_products: set[str] = field(default_factory=set)

    def zone_for(self, number: str | None) -> str | None:
        if not number:
            return None
        for prefix, zone in self.prefixes:
            if number.startswith(prefix):
                return zone
        return None

    def is_on_net(self, number: str | None) -> bool | None:
        if not number:
            return None
        return any(number.startswith(prefix) for prefix in self.home_prefixes)

    def band_for(self, moment: datetime) -> str | None:
        # Highest priority wins where bands overlap — a holiday band over PEAK.
        for band in self.time_bands:
            if band.covers(moment):
                return band.code
        return None

    def product_for(self, msisdn: str | None, on: date) -> SubscriberProduct | None:
        if not msisdn:
            return None
        for assignment in self.subscriber_products.get(msisdn, ()):
            if assignment.effective_from <= on and (
                assignment.effective_to is None or assignment.effective_to >= on
            ):
                return assignment
        return None


async def load_reference_index(db: AsyncSession) -> ReferenceIndex:
    index = ReferenceIndex()

    zones = (await db.execute(select(cm.DestinationZone))).scalars().all()
    index.zone_types = {z.id: z.zone_type for z in zones}
    zone_code_by_id = {z.id: z.code for z in zones}

    prefixes = (await db.execute(select(cm.DestinationPrefix))).scalars().all()
    index.prefixes = sorted(
        (
            (p.prefix, zone_code_by_id[p.zone_id])
            for p in prefixes
            if p.zone_id in zone_code_by_id
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    home = (
        await db.execute(select(NetworkPrefix).where(NetworkPrefix.is_home_network.is_(True)))
    ).scalars().all()
    index.home_prefixes = sorted((p.prefix for p in home), key=len, reverse=True)

    bands = (
        await db.execute(
            select(cm.TimeBand).where(cm.TimeBand.status == "ACTIVE")
        )
    ).scalars().all()
    index.time_bands = sorted(
        (
            TimeBandEntry(
                code=b.code,
                days=frozenset(str(d).upper() for d in (b.days or [])),
                start=b.start_time,
                end=b.end_time,
                timezone=b.timezone or "UTC",
                priority=b.priority,
            )
            for b in bands
        ),
        key=lambda b: b.priority,
        reverse=True,
    )

    assignments = (await db.execute(select(SubscriberProduct))).scalars().all()
    for assignment in assignments:
        index.subscriber_products.setdefault(assignment.msisdn, []).append(assignment)

    products = (await db.execute(select(cm.Product))).scalars().all()
    index.known_products = {p.code.upper() for p in products}
    index.product_account_type = {p.code.upper(): p.account_type for p in products}
    return index


def enrich(values: dict[str, Any], index: ReferenceIndex) -> dict[str, Any]:
    """Add the enrichment dimensions to one normalized record, in place.

    Returns the same dict with the dimensions, ``quality_status`` and the
    rating context set. Never raises: a record that cannot be enriched is still
    a record we must report on.
    """
    out = dict(values)
    problems: list[str] = []

    event_dt: datetime = out["event_timestamp"]
    event_day: date = out["event_date"]

    # --- Subscriber and product -------------------------------------------
    assignment = index.product_for(out.get("msisdn"), event_day)
    if assignment is None:
        # The product may still be carried on the CDR itself; some billing
        # systems stamp it, and that is better than nothing.
        stamped = (out.get("product_code") or "").upper() or None
        if stamped and stamped in index.known_products:
            out["product_code"] = stamped
            out["account_type"] = index.product_account_type.get(stamped)
        elif stamped:
            out["product_code"] = stamped
            problems.append(QualityStatus.PRODUCT_NOT_FOUND)
        else:
            problems.append(
                QualityStatus.SUBSCRIBER_NOT_FOUND
                if out.get("msisdn")
                else QualityStatus.REFERENCE_DATA_MISSING
            )
    else:
        out["subscriber_id"] = out.get("subscriber_id") or assignment.subscriber_id
        out["account_id"] = out.get("account_id") or assignment.account_id
        out["product_code"] = assignment.product_code.upper()
        out["offer_code"] = (assignment.offer_code or "").upper() or None
        out["tariff_plan_code"] = (assignment.tariff_plan_code or "").upper() or None
        out["account_type"] = assignment.account_type
        out["rating_group"] = out.get("rating_group") or assignment.rating_group
        if out["product_code"] not in index.known_products:
            problems.append(QualityStatus.PRODUCT_NOT_FOUND)

    # --- Destination and origin -------------------------------------------
    called = out.get("called_number")
    if out["service_type"] in {"VOICE", "SMS", "MMS", "ROAMING"}:
        zone = index.zone_for(called)
        if zone is None:
            problems.append(QualityStatus.DESTINATION_NOT_FOUND)
        out["destination_zone"] = zone
        out["on_net"] = index.is_on_net(called)
    out["origin_zone"] = index.zone_for(out.get("calling_number"))

    # --- Time band ---------------------------------------------------------
    band = index.band_for(event_dt)
    if band is None and index.time_bands:
        problems.append(QualityStatus.TIME_BAND_NOT_FOUND)
    out["time_band"] = band

    # --- Roaming -----------------------------------------------------------
    if out.get("roaming") is None:
        out["roaming"] = bool(out.get("visited_operator"))

    # --- Usage sanity ------------------------------------------------------
    if out["service_type"] == "VOICE":
        duration = out.get("duration_seconds")
        if duration is None or duration <= 0:
            problems.append(QualityStatus.INVALID_DURATION)

    out["quality_status"] = problems[0] if problems else QualityStatus.OK
    out["quality_detail"] = ", ".join(problems) if len(problems) > 1 else None

    key, digest = context_key(out)
    out["context_key"] = key
    out["context_hash"] = digest
    return out


def context_key(values: dict[str, Any]) -> tuple[str, str]:
    """The rating context and its hash (§11).

    Only the dimensions rule selection actually joins on go in. Anything else
    (msisdn, duration, charge) would fragment the context space and destroy the
    collapse from 1 crore rows to ~100k decisions, which is the whole basis of
    the performance design.
    """
    parts: list[str] = []
    for dimension in CONTEXT_DIMENSIONS:
        value = values.get(dimension)
        if isinstance(value, bool):
            parts.append("Y" if value else "N")
        elif value in (None, ""):
            parts.append("*")
        else:
            parts.append(str(value).upper())
    key = "|".join(parts)
    return key, hashlib.sha1(key.encode()).hexdigest()


def context_values(key: str) -> dict[str, Any]:
    """Inverse of ``context_key`` — used when resolving rules per context."""
    parts = key.split("|")
    out: dict[str, Any] = {}
    for dimension, raw in zip(CONTEXT_DIMENSIONS, parts, strict=False):
        if raw == "*":
            out[dimension] = None
        elif dimension in {"roaming", "on_net"}:
            out[dimension] = raw == "Y"
        else:
            out[dimension] = raw
    return out
