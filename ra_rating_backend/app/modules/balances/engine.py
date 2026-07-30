"""The stateful side of rating: bundle consumption and usage counters.

Held for the duration of a rating run and mutated in memory, then flushed once.
A per-CDR round trip to the bucket table would make bundles the slowest thing in
the pipeline; instead the run loads the buckets it touches, works on them, and
writes back at the end.

**Ordering is the correctness requirement.** Bundle consumption is
order-dependent: the same three calls in a different order produce different
charges once the allowance runs out. So the rating stage feeds CDRs ordered by
``(owner, event_timestamp)`` and this engine assumes that ordering. It does not
enforce it — enforcement belongs to the query — but every method here is written
as if the next event is genuinely the next event.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.balances.models import BalanceBucket, BalanceLedgerEntry, UsageCounter
from app.modules.catalog.models import BundleDefinition

log = get_logger("balances")

ZERO = Decimal("0")

#: Conversion into the unit a bundle is denominated in. Bundles are quoted in
#: human units (minutes, megabytes) while CDRs carry seconds and bytes.
_TO_UNIT: dict[str, Decimal] = {
    "SECOND": Decimal(1),
    "MINUTE": Decimal(60),
    "MESSAGE": Decimal(1),
    "EVENT": Decimal(1),
    "BYTE": Decimal(1),
    "KILOBYTE": Decimal(1024),
    "MEGABYTE": Decimal(1024 * 1024),
    "GIGABYTE": Decimal(1024 * 1024 * 1024),
}

#: Which raw CDR measure feeds which bundle unit.
_TIME_UNITS = {"SECOND", "MINUTE"}
_VOLUME_UNITS = {"BYTE", "KILOBYTE", "MEGABYTE", "GIGABYTE"}


def _dec(value: Any, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return default


def period_bounds(reset_period: str, on: date) -> tuple[date, date]:
    """The allowance window containing ``on``.

    Anchored to the calendar rather than to a subscription date, because that is
    what "300 minutes a month" means to a customer and what a billing system
    almost always implements.
    """
    period = (reset_period or "MONTHLY").upper()
    if period == "DAILY":
        return on, on
    if period == "WEEKLY":
        start = on - timedelta(days=on.weekday())
        return start, start + timedelta(days=6)
    if period == "NONE":
        # A one-off allowance: no reset, so the window is effectively unbounded.
        return date(1970, 1, 1), date(2999, 12, 31)
    # MONTHLY and BILLING_CYCLE both fall back to the calendar month; a real
    # billing cycle needs the subscriber's cycle date, which lands with the
    # subscription connector.
    return on.replace(day=1), on.replace(day=monthrange(on.year, on.month)[1])


def to_quota_unit(cdr: Any, unit: str) -> Decimal:
    """Express this CDR's usage in the bundle's unit."""
    unit = (unit or "").upper()
    if unit in _TIME_UNITS:
        return _dec(cdr.duration_seconds) / _TO_UNIT[unit]
    if unit in _VOLUME_UNITS:
        return _dec(cdr.usage_volume) / _TO_UNIT[unit]
    # MESSAGE / EVENT: one record is one unit unless the CDR says otherwise.
    volume = _dec(cdr.usage_volume, Decimal(1))
    return volume if volume > 0 else Decimal(1)


def from_quota_unit(quantity: Decimal, unit: str) -> Decimal:
    """Inverse of ``to_quota_unit`` — back to seconds or bytes."""
    unit = (unit or "").upper()
    return quantity * _TO_UNIT.get(unit, Decimal(1))


@dataclass
class Consumption:
    """What a bundle covered for one CDR."""

    bucket: BalanceBucket
    requested: Decimal
    consumed: Decimal
    overflow: Decimal
    balance_before: Decimal
    balance_after: Decimal
    unit: str

    @property
    def fully_covered(self) -> bool:
        return self.overflow <= ZERO


@dataclass
class BalanceEngine:
    """Per-run bundle and counter state."""

    run_id: str
    #: (owner_key, bundle_code, period_start) -> bucket
    buckets: dict[tuple[str, str, date], BalanceBucket] = field(default_factory=dict)
    counters: dict[tuple[str, str, date], UsageCounter] = field(default_factory=dict)
    definitions: dict[str, BundleDefinition] = field(default_factory=dict)
    ledger: list[BalanceLedgerEntry] = field(default_factory=list)
    #: Buckets and counters created during the run, to be inserted on flush.
    created: list[Any] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    async def load(self, db: AsyncSession) -> None:
        """Load the bundle catalogue once; buckets are loaded on first touch."""
        rows = (await db.execute(select(BundleDefinition))).scalars().all()
        self.definitions = {b.code.upper(): b for b in rows}

    def owner_key(self, cdr: Any, shared: bool) -> str | None:
        """Who the allowance belongs to.

        A shared bundle is consumed across an account, so the account is the
        owner; a personal one belongs to the subscriber. Getting this wrong is
        how a family plan silently gives every member the full allowance.
        """
        if shared:
            return cdr.account_id or cdr.subscriber_id or cdr.msisdn
        return cdr.subscriber_id or cdr.msisdn or cdr.account_id

    async def bucket_for(
        self, db: AsyncSession, cdr: Any, bundle_code: str
    ) -> BalanceBucket | None:
        definition = self.definitions.get(bundle_code.upper())
        if definition is None:
            return None

        owner = self.owner_key(cdr, definition.shared)
        if not owner:
            return None

        start, end = period_bounds(definition.reset_period, cdr.event_date)
        key = (owner, definition.code.upper(), start)
        if key in self.buckets:
            return self.buckets[key]

        existing = (
            await db.execute(
                select(BalanceBucket).where(
                    BalanceBucket.owner_key == owner,
                    BalanceBucket.bundle_code == definition.code.upper(),
                    BalanceBucket.period_start == start,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # First usage in the period allocates the allowance. Doing it lazily
            # means no nightly job has to pre-create a bucket for every
            # subscriber who might never use theirs.
            existing = BalanceBucket(
                owner_key=owner,
                owner_type="ACCOUNT" if definition.shared else "SUBSCRIBER",
                subscriber_id=cdr.subscriber_id,
                account_id=cdr.account_id,
                msisdn=cdr.msisdn,
                bundle_code=definition.code.upper(),
                service_type=definition.service_type,
                quota_unit=definition.quota_unit,
                shared=definition.shared,
                period_start=start,
                period_end=end,
                reset_period=definition.reset_period,
                allocated=float(definition.quota_value),
                consumed=0,
                overflow=0,
                source_system="RATING",
            )
            db.add(existing)
            self.created.append(existing)

        self.buckets[key] = existing
        return existing

    def consume(
        self,
        bucket: BalanceBucket,
        cdr: Any,
        *,
        rule_key: str | None = None,
    ) -> Consumption:
        """Draw this CDR's usage from the bucket, in order."""
        unit = bucket.quota_unit
        requested = to_quota_unit(cdr, unit)
        before = _dec(bucket.allocated) - _dec(bucket.consumed)
        available = max(before, ZERO)
        consumed = min(requested, available)
        overflow = requested - consumed

        bucket.consumed = float(_dec(bucket.consumed) + consumed)
        bucket.overflow = float(_dec(bucket.overflow) + overflow)
        # `or 0`: column defaults are applied by the database at INSERT, so a
        # bucket allocated moments ago still has None here.
        bucket.consumption_count = (bucket.consumption_count or 0) + 1
        bucket.last_consumed_at = cdr.event_timestamp or datetime.now(UTC)

        result = Consumption(
            bucket=bucket,
            requested=requested,
            consumed=consumed,
            overflow=overflow,
            balance_before=before,
            balance_after=before - consumed,
            unit=unit,
        )
        # Written even when nothing was consumed: an exhausted bundle still
        # explains why the call was charged.
        self.ledger.append(
            BalanceLedgerEntry(
                # The relationship, not bucket_id: a bucket allocated this run
                # has no id until flush, and a raw None would fail the FK.
                bucket=bucket,
                run_id=self.run_id,
                cdr_enriched_id=cdr.id,
                cdr_id=cdr.cdr_id,
                event_timestamp=cdr.event_timestamp,
                requested=float(requested),
                consumed=float(consumed),
                overflow=float(overflow),
                balance_before=float(before),
                balance_after=float(before - consumed),
                unit=unit,
                rule_key=rule_key,
            )
        )
        return result

    async def counter_for(
        self, db: AsyncSession, cdr: Any, counter_key: str, unit: str
    ) -> UsageCounter:
        """Cumulative usage for tiered charging."""
        owner = cdr.subscriber_id or cdr.msisdn or cdr.account_id or "UNKNOWN"
        start, end = period_bounds("MONTHLY", cdr.event_date)
        key = (owner, counter_key, start)
        if key in self.counters:
            return self.counters[key]

        existing = (
            await db.execute(
                select(UsageCounter).where(
                    UsageCounter.owner_key == owner,
                    UsageCounter.counter_key == counter_key,
                    UsageCounter.period_start == start,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = UsageCounter(
                owner_key=owner,
                counter_key=counter_key,
                service_type=cdr.service_type,
                unit=unit,
                period_start=start,
                period_end=end,
                total=0,
            )
            db.add(existing)
            self.created.append(existing)
        self.counters[key] = existing
        return existing

    def advance_counter(self, counter: UsageCounter, quantity: Decimal) -> Decimal:
        """Record usage and return the cumulative total *before* it.

        The prior total is what decides which tier this usage starts in, so it
        is what the caller needs — returning the new total would price every
        event one tier too high.
        """
        before = _dec(counter.total)
        counter.total = float(before + quantity)
        counter.event_count = (counter.event_count or 0) + 1
        return before

    async def flush(self, db: AsyncSession) -> dict[str, Any]:
        """Persist bucket state and the ledger."""
        for entry in self.ledger:
            db.add(entry)
        await db.flush()
        summary = {
            "buckets_touched": len(self.buckets),
            "buckets_created": sum(1 for b in self.created if isinstance(b, BalanceBucket)),
            "ledger_entries": len(self.ledger),
            "counters_touched": len(self.counters),
            "fully_covered": sum(1 for e in self.ledger if float(e.overflow) <= 0),
            "exhausted_buckets": sum(
                1 for b in self.buckets.values() if b.remaining <= 0
            ),
        }
        log.info("balances_flushed", run_id=self.run_id, **summary)
        self.stats = summary
        return summary


async def rewind_run(db: AsyncSession, run_id: str) -> int:
    """Undo a run's consumption so it can be replayed.

    Without this a replay would double-consume every bundle and report leakage
    that only the replay created.
    """
    entries = (
        await db.execute(
            select(BalanceLedgerEntry).where(BalanceLedgerEntry.run_id == run_id)
        )
    ).scalars().all()
    if not entries:
        return 0

    by_bucket: dict[str, list[BalanceLedgerEntry]] = {}
    for entry in entries:
        by_bucket.setdefault(entry.bucket_id, []).append(entry)

    for bucket_id, rows in by_bucket.items():
        bucket = await db.get(BalanceBucket, bucket_id)
        if bucket is None:
            continue
        bucket.consumed = float(
            max(ZERO, _dec(bucket.consumed) - sum(_dec(r.consumed) for r in rows))
        )
        bucket.overflow = float(
            max(ZERO, _dec(bucket.overflow) - sum(_dec(r.overflow) for r in rows))
        )
        bucket.consumption_count = max(0, (bucket.consumption_count or 0) - len(rows))
        for row in rows:
            await db.delete(row)

    await db.flush()
    return len(entries)
