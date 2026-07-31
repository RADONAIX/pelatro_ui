"""Bundle balances API: buckets and the consumption ledger behind them.

Read-only. Balances are written exclusively by rating runs — an endpoint that
could edit a bucket by hand would turn the ledger from evidence into opinion.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.deps import DbSession, PageParams, require
from app.core.errors import NotFoundError
from app.core.rbac import RatingPermKey
from app.modules.balances.models import BalanceBucket, BalanceLedgerEntry

router = APIRouter(prefix="/balances", tags=["balances"])

_view = require(RatingPermKey.RUNS, "view")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BucketRead(Base):
    id: str
    owner_key: str
    owner_type: str
    subscriber_id: str | None
    account_id: str | None
    msisdn: str | None
    bundle_code: str
    service_type: str
    quota_unit: str
    shared: bool
    period_start: date
    period_end: date
    reset_period: str
    allocated: float
    consumed: float
    overflow: float
    remaining: float
    consumption_count: int
    last_consumed_at: datetime | None
    source_system: str


class LedgerRead(Base):
    id: str
    run_id: str
    cdr_enriched_id: str
    cdr_id: str
    event_timestamp: datetime
    requested: float
    consumed: float
    overflow: float
    balance_before: float
    balance_after: float
    unit: str
    rule_key: str | None


@router.get(
    "/buckets",
    response_model=list[BucketRead],
    summary="Bundle balance buckets, most recently consumed first",
    dependencies=[Depends(_view)],
)
async def list_buckets(
    db: DbSession,
    page: PageParams,
    owner: str | None = Query(None, description="Subscriber, account or MSISDN"),
    bundle_code: str | None = Query(None),
    exhausted: bool | None = Query(
        None, description="true = only buckets with nothing left"
    ),
) -> list[BalanceBucket]:
    stmt = select(BalanceBucket)
    if owner:
        stmt = stmt.where(
            (BalanceBucket.owner_key == owner)
            | (BalanceBucket.subscriber_id == owner)
            | (BalanceBucket.account_id == owner)
            | (BalanceBucket.msisdn == owner)
        )
    if bundle_code:
        stmt = stmt.where(BalanceBucket.bundle_code == bundle_code.upper())
    if exhausted is True:
        stmt = stmt.where(BalanceBucket.consumed >= BalanceBucket.allocated)
    elif exhausted is False:
        stmt = stmt.where(BalanceBucket.consumed < BalanceBucket.allocated)
    stmt = (
        stmt.order_by(
            BalanceBucket.last_consumed_at.desc().nulls_last(),
            BalanceBucket.created_at.desc(),
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/buckets/stats",
    summary="Bucket totals for the balances screen header",
    dependencies=[Depends(_view)],
)
async def bucket_stats(db: DbSession) -> dict:
    total = int(
        (await db.execute(select(func.count()).select_from(BalanceBucket))).scalar_one()
    )
    exhausted = int(
        (
            await db.execute(
                select(func.count())
                .select_from(BalanceBucket)
                .where(BalanceBucket.consumed >= BalanceBucket.allocated)
            )
        ).scalar_one()
    )
    entries = int(
        (
            await db.execute(select(func.count()).select_from(BalanceLedgerEntry))
        ).scalar_one()
    )
    return {
        "total_buckets": total,
        "exhausted_buckets": exhausted,
        "ledger_entries": entries,
    }


@router.get(
    "/buckets/{bucket_id}",
    response_model=BucketRead,
    summary="One bucket",
    dependencies=[Depends(_view)],
)
async def get_bucket(db: DbSession, bucket_id: str) -> BalanceBucket:
    row = await db.get(BalanceBucket, bucket_id)
    if row is None:
        raise NotFoundError(f"Bucket '{bucket_id}' was not found.")
    return row


@router.get(
    "/buckets/{bucket_id}/ledger",
    response_model=list[LedgerRead],
    summary="The consumption trail for one bucket, in event order",
    dependencies=[Depends(_view)],
)
async def bucket_ledger(
    db: DbSession, bucket_id: str, page: PageParams
) -> list[BalanceLedgerEntry]:
    rows = (
        await db.execute(
            select(BalanceLedgerEntry)
            .where(BalanceLedgerEntry.bucket_id == bucket_id)
            .order_by(BalanceLedgerEntry.event_timestamp)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars().all()
    return list(rows)
