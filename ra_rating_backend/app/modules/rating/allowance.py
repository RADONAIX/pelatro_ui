"""Read-only bundle allowances for rating assurance (§18).

Assurance answers "what *should* this call have cost?". Answering it needs to
know what allowance the subscriber had — but must not spend it. This service
therefore reads bundle balances and never writes them: running assurance over a
month of history must not drain a live customer's minutes, and it must be
possible to run twice and get the same answer both times.

The consequence is deliberate: within one batch the same starting balance is
offered to every call of a subscriber, so a bundle is not depleted across a
sequence. That is correct for *assurance* — each call is checked against the
allowance the billing system had at the time — and wrong for *charging*, which
is why the existing stateful ``BalanceEngine`` still owns the charging path.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.balances.models import BalanceBucket
from app.modules.rules.constants import ActionType, ExecutionStage

ZERO = Decimal("0")


@dataclass
class _Bucket:
    """The subset of a balance bucket the engine's trace needs."""

    bundle_code: str


@dataclass
class ReadOnlyConsumption:
    """Duck-types ``balances.Consumption`` without touching the ledger."""

    bucket: _Bucket
    requested: Decimal
    consumed: Decimal
    overflow: Decimal
    balance_before: Decimal
    balance_after: Decimal
    unit: str

    @property
    def fully_covered(self) -> bool:
        return self.overflow <= ZERO


class AllowanceReader:
    """Bulk, read-only bundle balances for a chunk of usage records."""

    def __init__(self) -> None:
        self._by_owner: dict[tuple[str, str], BalanceBucket] = {}

    async def load(self, db: AsyncSession, rows: list[Any]) -> None:
        """One query for the whole chunk's owners."""
        owners = {
            key
            for key in (
                r.account_id or r.subscriber_id or r.msisdn for r in rows
            )
            if key
        }
        if not owners:
            return
        buckets = (
            await db.execute(
                select(BalanceBucket).where(BalanceBucket.owner_key.in_(owners))
            )
        ).scalars().all()
        for bucket in buckets:
            self._by_owner[(bucket.owner_key, bucket.bundle_code)] = bucket

    def for_usage(
        self, row: Any, selected: dict[str, Any]
    ) -> ReadOnlyConsumption | None:
        """What the subscriber's bundle would have covered for this record.

        Returns ``None`` when no bundle rule applied or no matching balance
        exists — the engine then charges the full quantity and says so in the
        trace, which is the difference between "covered by an allowance" and
        "we could not find the allowance".
        """
        bundle_rule = selected.get(ExecutionStage.BUNDLE)
        if bundle_rule is None:
            return None

        action = next(
            (
                a
                for a in (bundle_rule.actions or [])
                if a.get("action_type") == ActionType.CONSUME_BUNDLE.value
            ),
            None,
        )
        code = str(((action or {}).get("params") or {}).get("bundle") or "")
        if not code:
            return None

        owner = row.account_id or row.subscriber_id or row.msisdn
        bucket = self._by_owner.get((owner, code)) if owner else None
        if bucket is None:
            return None

        unit = str(bucket.quota_unit or "SECOND").upper()
        requested = _requested(row, unit)
        # There is no stored "balance" column: the remaining allowance is what
        # was allocated less what has been consumed, floored at zero so an
        # over-consumed bucket reads as empty rather than as a negative
        # allowance that would credit the customer.
        available = max(
            ZERO,
            Decimal(str(bucket.allocated or 0)) - Decimal(str(bucket.consumed or 0)),
        )
        consumed = min(requested, available) if available > ZERO else ZERO

        return ReadOnlyConsumption(
            bucket=_Bucket(bundle_code=bucket.bundle_code),
            requested=requested,
            consumed=consumed,
            # Floored at zero on both sides: an allowance bigger than the call
            # leaves no overflow, not a negative one (§29).
            overflow=max(ZERO, requested - consumed),
            balance_before=available,
            balance_after=max(ZERO, available - consumed),
            unit=unit,
        )


#: Seconds/bytes per bundle unit, for expressing the call in the bundle's terms.
_SCALE: dict[str, Decimal] = {
    "SECOND": Decimal(1),
    "MINUTE": Decimal(60),
    "BYTE": Decimal(1),
    "KILOBYTE": Decimal(1024),
    "MEGABYTE": Decimal(1024 * 1024),
    "GIGABYTE": Decimal(1024 * 1024 * 1024),
    "MESSAGE": Decimal(1),
    "EVENT": Decimal(1),
}


def _requested(row: Any, unit: str) -> Decimal:
    """The call expressed in the bundle's own unit.

    A bundle quoted in minutes against a call measured in seconds must be
    converted, not compared: skipping this is a 60x error, always in the
    customer's favour, and it looks like a pricing bug rather than a unit bug.
    """
    scale = _SCALE.get(unit, Decimal(1))
    if unit in {"MESSAGE", "EVENT"}:
        volume = Decimal(str(row.usage_volume or 1))
        return volume if volume > ZERO else Decimal(1)
    if unit in {"BYTE", "KILOBYTE", "MEGABYTE", "GIGABYTE"}:
        return Decimal(str(row.usage_volume or 0)) / scale
    return Decimal(str(row.duration_seconds or 0)) / scale
