"""Batch enrichment (§8, §9, §10).

Reference data is read **per chunk, scoped to the subscribers in it** — not
per CDR, and not by loading every subscriber in the estate. One chunk of 5,000
usage rows costs a fixed handful of queries whatever the size of the subscriber
base.

**The group-fan-out trap (§9).** A subscriber can be in twenty-five groups. The
obvious query — join usage to membership — returns twenty-five rows for one
call, and every one of them goes on to be rated. The result is twenty-five
expected charges, twenty-five results and a reported revenue figure twenty-five
times too large, all of it looking like a pricing defect rather than a join
defect. So membership is aggregated to one array per subscriber *before* it
meets a usage row, and the enriched record is one row per ``usage_id`` by
construction. ``assign()`` returns exactly one output per input, and the test
suite asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.cdr.enrich import CONTEXT_DIMENSIONS, TimeBandEntry
from app.modules.cdr.models import (
    HolidayCalendar,
    NetworkPrefix,
    SubscriberGroupMembership,
    SubscriberProduct,
)


class EnrichmentStatus(StrEnum):
    """How completely a usage record could be placed in a rating situation (§10)."""

    PENDING = "PENDING"
    ENRICHED = "ENRICHED"
    #: Everything mandatory resolved, something optional did not. Still rateable.
    PARTIALLY_ENRICHED = "PARTIALLY_ENRICHED"
    SUBSCRIBER_NOT_FOUND = "SUBSCRIBER_NOT_FOUND"
    TARIFF_NOT_FOUND = "TARIFF_NOT_FOUND"
    PREFIX_NOT_FOUND = "PREFIX_NOT_FOUND"
    #: Two tariff subscriptions covering the same instant. Never resolved by
    #: picking one: which of two prices is correct is a data question, and
    #: guessing produces a confident wrong answer.
    MULTIPLE_ACTIVE_TARIFFS = "MULTIPLE_ACTIVE_TARIFFS"
    INVALID_USAGE_RECORD = "INVALID_USAGE_RECORD"


#: Statuses from which rating may still proceed.
RATEABLE_STATUSES: frozenset[str] = frozenset(
    {EnrichmentStatus.ENRICHED, EnrichmentStatus.PARTIALLY_ENRICHED}
)

#: Without every one of these a charge cannot be computed (§10). Checked after
#: enrichment rather than assumed, so the failure names the missing field.
MANDATORY_FIELDS: tuple[str, ...] = (
    "subscriber_type",
    "tariff_plan_code",
    "service_type",
    "call_direction",
    "event_timestamp",
    "duration_seconds",
    "destination_type",
)

#: Voice needs a duration; a message does not. Applying one rule to both would
#: reject every SMS in the estate.
_DURATION_REQUIRED_SERVICES = frozenset({"VOICE"})


class NetworkRelation(StrEnum):
    ON_NET = "ON_NET"
    OFF_NET = "OFF_NET"
    INTERNATIONAL = "INTERNATIONAL"


class DayType(StrEnum):
    WEEKDAY = "WEEKDAY"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"


@dataclass
class BatchReference:
    """Reference data for one chunk, keyed for O(1) lookup per row."""

    #: (prefix, zone_code, zone_type) sorted longest-first (§26).
    prefixes: list[tuple[str, str, str]] = field(default_factory=list)
    home_prefixes: list[str] = field(default_factory=list)
    time_bands: list[TimeBandEntry] = field(default_factory=list)
    holidays: dict[date, str] = field(default_factory=dict)
    #: msisdn -> effective-dated product/tariff subscriptions.
    subscriptions: dict[str, list[SubscriberProduct]] = field(default_factory=dict)
    #: msisdn -> its memberships. Aggregated per row, never joined.
    memberships: dict[str, list[SubscriberGroupMembership]] = field(default_factory=dict)
    known_products: set[str] = field(default_factory=set)
    product_account_type: dict[str, str] = field(default_factory=dict)
    product_segment: dict[str, str] = field(default_factory=dict)
    known_tariffs: set[str] = field(default_factory=set)
    currency_scale: dict[str, int] = field(default_factory=dict)

    # --- Longest-prefix matching (§26) --------------------------------------

    def destination(self, number: str | None) -> tuple[str | None, str | None]:
        """``(zone_code, zone_type)`` for the longest matching prefix.

        Longest, not first: with 233, 2335 and 23350 all configured, a call to
        233501112222 belongs to 23350. Matching on the shortest would price
        every Ghanaian mobile call as a generic national one.
        """
        if not number:
            return None, None
        for prefix, zone, zone_type in self.prefixes:
            if number.startswith(prefix):
                return zone, zone_type
        return None, None

    def is_on_net(self, number: str | None) -> bool | None:
        if not number:
            return None
        return any(number.startswith(prefix) for prefix in self.home_prefixes)

    def time_band(self, moment: datetime) -> str | None:
        for band in self.time_bands:
            if band.covers(moment):
                return band.code
        return None

    def day_type(self, day: date) -> str:
        holiday = self.holidays.get(day)
        if holiday:
            return holiday
        return DayType.WEEKEND if day.weekday() >= 5 else DayType.WEEKDAY

    def subscriptions_at(self, msisdn: str | None, on: date) -> list[SubscriberProduct]:
        """Every subscription covering the event date — plural on purpose.

        Returning a list rather than the first match is what makes
        MULTIPLE_ACTIVE_TARIFFS detectable instead of silently resolved.
        """
        if not msisdn:
            return []
        return [
            s
            for s in self.subscriptions.get(msisdn, ())
            if s.effective_from <= on and (s.effective_to is None or s.effective_to >= on)
        ]

    def groups_at(self, msisdn: str | None, on: date) -> list[str]:
        """The subscriber's groups on the event date, de-duplicated and sorted.

        Sorted so the value is deterministic: an unordered array would make two
        identical situations produce two different context keys, fragmenting the
        context space and defeating the per-context rule resolution.
        """
        if not msisdn:
            return []
        codes = {
            m.group_code.upper()
            for m in self.memberships.get(msisdn, ())
            if m.effective_from <= on and (m.effective_to is None or m.effective_to >= on)
        }
        return sorted(codes)


async def load_reference(
    db: AsyncSession, *, msisdns: set[str], event_days: set[date]
) -> BatchReference:
    """Load everything this chunk needs, in a fixed number of queries.

    Subscriber-scoped tables are filtered to the chunk's own msisdns; the small
    global tables (prefixes, bands, holidays) are loaded whole. Neither grows
    with the size of the batch.
    """
    reference = BatchReference()
    numbers = {m for m in msisdns if m}

    zones = (await db.execute(select(cm.DestinationZone))).scalars().all()
    zone_code = {z.id: z.code for z in zones}
    zone_type = {z.id: z.zone_type for z in zones}

    prefixes = (await db.execute(select(cm.DestinationPrefix))).scalars().all()
    reference.prefixes = sorted(
        (
            (p.prefix, zone_code[p.zone_id], zone_type.get(p.zone_id, ""))
            for p in prefixes
            if p.zone_id in zone_code
        ),
        key=lambda entry: len(entry[0]),
        reverse=True,
    )

    home = (
        await db.execute(select(NetworkPrefix).where(NetworkPrefix.is_home_network.is_(True)))
    ).scalars().all()
    reference.home_prefixes = sorted((p.prefix for p in home), key=len, reverse=True)

    bands = (
        await db.execute(select(cm.TimeBand).where(cm.TimeBand.status == "ACTIVE"))
    ).scalars().all()
    reference.time_bands = sorted(
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

    if event_days:
        holidays = (
            await db.execute(
                select(HolidayCalendar).where(HolidayCalendar.holiday_date.in_(event_days))
            )
        ).scalars().all()
        reference.holidays = {h.holiday_date: h.day_type for h in holidays}

    if numbers:
        # Effective-dated and scoped to this chunk's subscribers. The date
        # filter is deliberately wide (any window overlapping the chunk's
        # days); the exact per-event check happens in `subscriptions_at`,
        # because one chunk can span several days.
        earliest, latest = min(event_days), max(event_days)
        subscriptions = (
            await db.execute(
                select(SubscriberProduct).where(
                    SubscriberProduct.msisdn.in_(numbers),
                    SubscriberProduct.effective_from <= latest,
                    or_(
                        SubscriberProduct.effective_to.is_(None),
                        SubscriberProduct.effective_to >= earliest,
                    ),
                )
            )
        ).scalars().all()
        for subscription in subscriptions:
            reference.subscriptions.setdefault(subscription.msisdn, []).append(subscription)

        memberships = (
            await db.execute(
                select(SubscriberGroupMembership).where(
                    SubscriberGroupMembership.msisdn.in_(numbers),
                    SubscriberGroupMembership.effective_from <= latest,
                    or_(
                        SubscriberGroupMembership.effective_to.is_(None),
                        SubscriberGroupMembership.effective_to >= earliest,
                    ),
                )
            )
        ).scalars().all()
        for membership in memberships:
            reference.memberships.setdefault(membership.msisdn, []).append(membership)

    products = (await db.execute(select(cm.Product))).scalars().all()
    reference.known_products = {p.code.upper() for p in products}
    reference.product_account_type = {p.code.upper(): p.account_type for p in products}
    # Segment is not a first-class column on Product; operators model it in the
    # free-form attributes bag, so read it from there rather than adding a
    # column the catalogue module does not own.
    reference.product_segment = {
        p.code.upper(): str((p.attributes or {}).get("segment"))
        for p in products
        if (p.attributes or {}).get("segment")
    }

    tariffs = (await db.execute(select(cm.TariffPlan))).scalars().all()
    reference.known_tariffs = {t.code.upper() for t in tariffs}

    currencies = (await db.execute(select(cm.Currency))).scalars().all()
    reference.currency_scale = {c.code.upper(): c.decimals for c in currencies}

    return reference


async def group_counts(db: AsyncSession, msisdns: set[str], on: date) -> dict[str, int]:
    """How many groups each subscriber belongs to — the §9 aggregation, in SQL.

    Exposed separately because it is the direct expression of the rule: one row
    per subscriber, groups collapsed with ``array_agg``, never a join that
    multiplies the usage row.
    """
    if not msisdns:
        return {}
    rows = (
        await db.execute(
            select(
                SubscriberGroupMembership.msisdn,
                func.count(func.distinct(SubscriberGroupMembership.group_code)),
            )
            .where(
                SubscriberGroupMembership.msisdn.in_(msisdns),
                SubscriberGroupMembership.effective_from <= on,
                or_(
                    SubscriberGroupMembership.effective_to.is_(None),
                    SubscriberGroupMembership.effective_to >= on,
                ),
            )
            .group_by(SubscriberGroupMembership.msisdn)
        )
    ).all()
    return {msisdn: count for msisdn, count in rows}


def _destination_type(zone_type: str | None, relation: str | None, service: str) -> str | None:
    """The rateable class of the destination.

    Kept distinct from the zone: a zone is a place, a destination type is what
    it costs to reach. Rules are written against the latter.
    """
    if relation == NetworkRelation.INTERNATIONAL:
        return "INTERNATIONAL"
    if zone_type:
        return zone_type.upper()
    if relation == NetworkRelation.ON_NET:
        return f"NATIONAL_{'SMS' if service == 'SMS' else 'MOBILE'}_ONNET"
    if relation == NetworkRelation.OFF_NET:
        return f"NATIONAL_{'SMS' if service == 'SMS' else 'MOBILE'}"
    return None


def assign(row: Any, reference: BatchReference) -> dict[str, Any]:
    """Enrich ONE usage record. Returns exactly one dict — never a list.

    The signature is the guarantee: there is no code path here that can turn one
    usage record into several, whatever the subscriber's group count.
    """
    problems: list[str] = []
    event_time: datetime = row.event_timestamp
    event_day: date = row.event_date
    service = (row.service_type or "").upper()

    out: dict[str, Any] = {
        "usage_id": row.usage_id,
        "service_type": service,
        "call_direction": row.call_direction,
        "duration_seconds": row.duration_seconds,
        "roaming": bool(row.roaming),
    }

    # --- Subscriber, product and tariff (event-dated) -----------------------
    msisdn = row.msisdn
    subscriptions = reference.subscriptions_at(msisdn, event_day)

    if not subscriptions:
        problems.append(
            EnrichmentStatus.SUBSCRIBER_NOT_FOUND
            if msisdn
            else EnrichmentStatus.INVALID_USAGE_RECORD
        )
    elif len({s.tariff_plan_code for s in subscriptions if s.tariff_plan_code}) > 1:
        # Two live tariffs for one instant. Which price is right is a data
        # question with a real answer; picking one here would replace it with a
        # confident guess.
        problems.append(EnrichmentStatus.MULTIPLE_ACTIVE_TARIFFS)
        out["tariff_plan_code"] = None
    else:
        subscription = subscriptions[0]
        out["subscriber_id"] = subscription.subscriber_id
        out["account_id"] = subscription.account_id
        out["product_code"] = (subscription.product_code or "").upper() or None
        out["offer_code"] = (subscription.offer_code or "").upper() or None
        out["tariff_plan_code"] = (subscription.tariff_plan_code or "").upper() or None
        out["subscriber_type"] = subscription.account_type
        out["account_type"] = subscription.account_type
        out["rating_group"] = subscription.rating_group
        out["customer_segment"] = reference.product_segment.get(out["product_code"] or "")
        if not out["tariff_plan_code"] or out["tariff_plan_code"] not in reference.known_tariffs:
            problems.append(EnrichmentStatus.TARIFF_NOT_FOUND)

    # --- Subscriber groups: a LIST on this row, never extra rows (§9) -------
    groups = reference.groups_at(msisdn, event_day)
    out["subscriber_groups"] = groups
    if not out.get("customer_segment") and groups:
        # The segment is a single value; groups are many. Falling back to the
        # first group keeps segment-scoped rules working for estates that model
        # segment as a group rather than as a product attribute.
        out["customer_segment"] = groups[0]

    # --- Destination (§26) --------------------------------------------------
    counterparty = row.called_number if row.call_direction != "MT" else row.calling_number
    zone, zone_type = reference.destination(counterparty)
    on_net = reference.is_on_net(counterparty)

    if counterparty and zone is None:
        problems.append(EnrichmentStatus.PREFIX_NOT_FOUND)

    if on_net:
        relation = NetworkRelation.ON_NET
    elif zone_type and "INTERNATIONAL" in zone_type.upper():
        relation = NetworkRelation.INTERNATIONAL
    elif counterparty:
        relation = NetworkRelation.OFF_NET
    else:
        relation = None

    out["destination_zone"] = zone
    out["on_net"] = on_net
    out["network_relation"] = relation
    out["destination_type"] = _destination_type(zone_type, relation, service)
    out["origin_zone"], _ = reference.destination(row.calling_number)

    # --- Time (§8) ----------------------------------------------------------
    out["time_band"] = reference.time_band(event_time)
    out["day_type"] = reference.day_type(event_day)

    # --- Allowances ---------------------------------------------------------
    out["bundle_ids"] = [g for g in groups if g.endswith("_BUNDLE")]
    out["offer_ids"] = [out["offer_code"]] if out.get("offer_code") else []

    # --- Mandatory-field check (§10) ---------------------------------------
    missing = [
        name
        for name in MANDATORY_FIELDS
        if _missing(name, out, row, service)
    ]
    if missing and not problems:
        problems.append(EnrichmentStatus.PARTIALLY_ENRICHED)

    out["enrichment_status"] = problems[0] if problems else EnrichmentStatus.ENRICHED
    out["enrichment_error"] = (
        "; ".join(
            [*(f"missing {name}" for name in missing), *problems[1:]]
        )
        or None
    )
    out["rateable"] = out["enrichment_status"] in RATEABLE_STATUSES and not missing

    key, digest = context_key_for(out)
    out["context_key"] = key
    out["context_hash"] = digest
    return out


def _missing(name: str, out: dict[str, Any], row: Any, service: str) -> bool:
    if name == "duration_seconds":
        if service not in _DURATION_REQUIRED_SERVICES:
            return False
        return row.duration_seconds is None
    if name == "event_timestamp":
        return row.event_timestamp is None
    if name == "service_type":
        return not service
    if name == "call_direction":
        return not out.get("call_direction")
    return out.get(name) in (None, "")


def context_key_for(values: dict[str, Any]) -> tuple[str, str]:
    """The rating context for an enriched record (§11).

    ``subscriber_groups`` is deliberately **not** a context dimension. Including
    it would give every distinct combination of groups its own context — a
    subscriber in six groups would share a context with almost nobody — and the
    collapse from a million CDRs to a few thousand rule decisions, which is what
    makes the batch tractable, would disappear. Group membership is matched by
    rule *conditions* against the list on the row, not by the context key.
    """
    from app.modules.cdr.enrich import context_key

    return context_key(values)


__all__ = [
    "CONTEXT_DIMENSIONS",
    "MANDATORY_FIELDS",
    "RATEABLE_STATUSES",
    "BatchReference",
    "DayType",
    "EnrichmentStatus",
    "NetworkRelation",
    "assign",
    "context_key_for",
    "group_counts",
    "load_reference",
]
