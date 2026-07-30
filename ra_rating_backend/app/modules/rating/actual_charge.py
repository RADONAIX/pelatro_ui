"""Correlating an MSC event with what the billing system actually charged (§21).

**Why this exists at all.** An MSC CDR records what the *network* did. It has no
charged-amount column — this is not a gap in the loader, it is what a switch
record is. Every one of the 137 columns on ``sm_msc01`` that mentions "charge"
is a descriptor (``charge_level = chargeBySecond``, ``charged_party =
callingParty``), never an amount. The billed figure lives in the online charging
system, and assurance means putting the two side by side.

**Until an OCS source is configured, the honest answer is "unknown".** The
result is ``NO_ACTUAL_CHARGE`` — never zero. A missing charge recorded as zero
would classify every unbilled call as correctly billed at nothing, which is the
exact failure this platform exists to detect.

Matching is by ``call_reference`` where the OCS carries it — the switch's own
correlation handle, and an exact key — falling back to the composite of
subscriber, called number, start time and duration with a configurable
tolerance, because two systems clocking the same call rarely agree to the second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import ocs_source_factory
from app.core.logging import get_logger
from app.modules.rating.audit_models import ActualChargeStatus

log = get_logger("rating.actual_charge")

_SAFE_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _identifier(value: str, *, what: str) -> str:
    name = str(value).strip()
    if not name or set(name) - _SAFE_IDENTIFIER:
        raise ValueError(f"{what} '{value}' is not a valid SQL identifier.")
    return name


@dataclass
class ActualCharge:
    """What the billing system charged, and how sure we are it is the same event."""

    status: str
    amount: Decimal | None = None
    currency: str | None = None
    match_count: int = 0
    detail: str = ""

    @property
    def is_usable(self) -> bool:
        """Whether this may be compared against an expected charge.

        A ``MULTIPLE_ACTUAL_MATCHES`` amount is deliberately *not* usable:
        picking one of several candidate charges would manufacture a variance
        (or hide one) depending on which was picked.
        """
        return self.status in {ActualChargeStatus.MATCHED, ActualChargeStatus.PARTIAL_MATCH}


NOT_CONFIGURED = ActualCharge(
    status=ActualChargeStatus.NO_ACTUAL_CHARGE,
    detail=(
        "No OCS/IN source is configured, and MSC records carry no charged "
        "amount. The expected charge was computed but has nothing to be "
        "compared against."
    ),
)


class ActualChargeService:
    """Bulk correlation of usage records against OCS charges.

    Bulk, not per record: one query per chunk keyed on the whole chunk's
    subscribers and time window, then matched in memory. A per-CDR query would
    be 10 lakh round trips.
    """

    def __init__(self) -> None:
        self.enabled = settings.ocs_source_enabled
        self.tolerance = timedelta(seconds=settings.actual_charge_time_tolerance_seconds)

    async def for_chunk(self, rows: list[Any]) -> dict[str, ActualCharge]:
        """Return ``usage_id -> ActualCharge`` for every row in the chunk."""
        if not self.enabled:
            return {row.usage_id: NOT_CONFIGURED for row in rows if row.usage_id}
        if not rows:
            return {}

        try:
            candidates = await self._fetch(rows)
        except SQLAlchemyError as exc:
            # An unreachable OCS must not fail the batch: the expected charges
            # are still correct and still worth storing.
            log.error("ocs_source_unavailable", error=str(exc))
            unavailable = ActualCharge(
                status=ActualChargeStatus.NO_ACTUAL_CHARGE,
                detail=f"The OCS source could not be read: {exc}",
            )
            return {row.usage_id: unavailable for row in rows if row.usage_id}

        by_reference: dict[str, list[dict[str, Any]]] = {}
        by_msisdn: dict[str, list[dict[str, Any]]] = {}
        for record in candidates:
            reference = record.get("call_reference")
            if reference:
                by_reference.setdefault(str(reference).upper(), []).append(record)
            msisdn = record.get("msisdn")
            if msisdn:
                by_msisdn.setdefault(str(msisdn), []).append(record)

        return {
            row.usage_id: self._match(row, by_reference, by_msisdn)
            for row in rows
            if row.usage_id
        }

    async def _fetch(self, rows: list[Any]) -> list[dict[str, Any]]:
        schema = _identifier(settings.ocs_source_schema, what="OCS schema")
        table = _identifier(settings.ocs_source_table, what="OCS table")
        columns = {
            "msisdn": _identifier(settings.ocs_column_msisdn, what="column"),
            "called_number": _identifier(settings.ocs_column_called_number, what="column"),
            "event_time": _identifier(settings.ocs_column_event_time, what="column"),
            "duration": _identifier(settings.ocs_column_duration, what="column"),
            "call_reference": _identifier(settings.ocs_column_call_reference, what="column"),
            "charge": _identifier(settings.ocs_column_charge, what="column"),
            "currency": _identifier(settings.ocs_column_currency, what="column"),
        }

        msisdns = sorted({r.msisdn for r in rows if r.msisdn})
        times = [r.event_timestamp for r in rows if r.event_timestamp]
        if not msisdns or not times:
            return []
        window_start = min(times) - self.tolerance
        window_end = max(times) + self.tolerance

        projection = ", ".join(f'"{source}" AS {alias}' for alias, source in columns.items())
        statement = text(
            f'SELECT {projection} FROM "{schema}"."{table}" '
            f'WHERE "{columns["msisdn"]}" = ANY(:msisdns) '
            f'AND "{columns["event_time"]}" BETWEEN :window_start AND :window_end'
        ).bindparams(
            bindparam("msisdns"), bindparam("window_start"), bindparam("window_end")
        )

        async with ocs_source_factory()() as session:
            result = await session.execute(
                statement,
                {"msisdns": msisdns, "window_start": window_start, "window_end": window_end},
            )
            return [dict(row) for row in result.mappings().all()]

    def _match(
        self,
        row: Any,
        by_reference: dict[str, list[dict[str, Any]]],
        by_msisdn: dict[str, list[dict[str, Any]]],
    ) -> ActualCharge:
        # 1. Exact: the switch's own call reference.
        if row.call_reference:
            exact = by_reference.get(str(row.call_reference).upper())
            if exact:
                return self._verdict(exact, "matched on call reference")

        # 2. Composite: subscriber + called number + start time + duration.
        pool = by_msisdn.get(row.msisdn or "", [])
        if not pool:
            return ActualCharge(
                status=ActualChargeStatus.NO_ACTUAL_CHARGE,
                detail="No OCS charge was found for this subscriber in the event window.",
            )

        near = [
            record
            for record in pool
            if self._within_tolerance(row, record)
        ]
        if not near:
            return ActualCharge(
                status=ActualChargeStatus.NO_ACTUAL_CHARGE,
                detail=(
                    "OCS charges exist for this subscriber but none within "
                    f"{self.tolerance.total_seconds():.0f}s of the event."
                ),
            )

        exact_duration = [
            record
            for record in near
            if record.get("duration") is not None
            and row.duration_seconds is not None
            and int(record["duration"]) == int(row.duration_seconds)
        ]
        if exact_duration:
            return self._verdict(exact_duration, "matched on subscriber, time and duration")
        # Time matched, duration did not: almost certainly the same call, but
        # a duration disagreement is itself a finding, so it is reported as a
        # partial match rather than passed off as clean.
        return self._verdict(near, "matched on subscriber and time only", partial=True)

    def _within_tolerance(self, row: Any, record: dict[str, Any]) -> bool:
        moment = record.get("event_time")
        if moment is None or row.event_timestamp is None:
            return False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=row.event_timestamp.tzinfo)
        return abs(moment - row.event_timestamp) <= self.tolerance

    def _verdict(
        self, records: list[dict[str, Any]], detail: str, *, partial: bool = False
    ) -> ActualCharge:
        if len(records) > 1:
            return ActualCharge(
                status=ActualChargeStatus.MULTIPLE_ACTUAL_MATCHES,
                match_count=len(records),
                detail=(
                    f"{len(records)} OCS charges match this event; which one applies "
                    "cannot be decided without guessing."
                ),
            )
        record = records[0]
        amount = record.get("charge")
        return ActualCharge(
            status=(
                ActualChargeStatus.PARTIAL_MATCH if partial else ActualChargeStatus.MATCHED
            ),
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=record.get("currency"),
            match_count=1,
            detail=detail,
        )
