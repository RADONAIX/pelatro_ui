"""The mirror's only contact surface with existing code.

Five call sites in the whole service import from here, and every one of them
calls a ``record_*`` function that does three things: check the flag, append a
plain tuple to a list on ``session.info``, return ``None``. No I/O, no
validation, no exception path. That is what keeps the primary write path's
behaviour identical whether the mirror exists or not.

The actual work happens in :func:`drain`, called from ``get_session()`` *after*
the primary transaction has committed. A mirrored row therefore can never
describe a transaction that rolled back — the failure mode that makes naive
dual-write designs untrustworthy.

Everything in :func:`drain` is inside a blanket ``except Exception``. That is
deliberate and is the documented consistency posture: the mirror is
eventually-consistent and best-effort, and ``scripts/mirror_backfill.py`` is the
repair path. A mirror failure must never turn a successful rule save into a 500.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("mirror")

#: Key under which pending events live on ``AsyncSession.info``.
_QUEUE = "_mirror_queue"

# Event kinds.
RULE_VERSION = "rule_version"
LEGACY_RULE = "legacy_rule"
LEGACY_RULE_DELETE = "legacy_rule_delete"
TIME_BAND = "time_band"
PREFIX = "destination_prefix"
PREFIX_DELETE = "destination_prefix_delete"
SUBSCRIBER_OFFER = "subscriber_offer"
BUNDLE_BALANCE = "subscriber_bundle_balance"


def is_enabled() -> bool:
    """Exposed so a call site that would otherwise do work *before* recording —
    an extra query, a serialisation — can skip it too."""
    return settings.mirror_enabled


def rule_source() -> str:
    """Which rule store feeds the mirror, with AUTO resolved.

    AUTO follows `rule_compile_source` so the mirror reflects whichever model
    actually prices traffic. Mirroring both puts one logical rule in the target
    twice under two `rule_id`s.
    """
    configured = settings.mirror_rule_source
    if configured != "AUTO":
        return configured
    return "CANONICAL" if settings.rule_compile_source == "CANONICAL" else "LEGACY"


def _record(db: AsyncSession, kind: str, payload: Any) -> None:
    if not settings.mirror_enabled:
        return
    try:
        db.info.setdefault(_QUEUE, []).append((kind, payload))
    except Exception:  # pragma: no cover - defensive; info is a plain dict
        log.warning("mirror_record_failed", kind=kind)


# --- Recorders (called from existing code) -----------------------------------


def record_rule_version(db: AsyncSession, rule_version_id: str) -> None:
    """Called at the end of `rules/ingest/writer.py:write()`."""
    if not settings.mirror_enabled or rule_source() not in ("CANONICAL", "BOTH"):
        return
    _record(db, RULE_VERSION, rule_version_id)


def record_legacy_rule(db: AsyncSession, rule_id: str) -> None:
    """Called from `rules/service.py` — create, update, new version, clone, status.

    The legacy model is what the rule-management UI writes in a default
    deployment (`RULE_COMPILE_SOURCE=LEGACY`), so this is the hook that carries
    most real traffic.
    """
    if not settings.mirror_enabled or rule_source() not in ("LEGACY", "BOTH"):
        return
    _record(db, LEGACY_RULE, rule_id)


def record_legacy_rule_delete(db: AsyncSession, rule_id: str) -> None:
    """`delete_draft` hard-deletes a first-version draft; the mirror follows."""
    if not settings.mirror_enabled or rule_source() not in ("LEGACY", "BOTH"):
        return
    _record(db, LEGACY_RULE_DELETE, rule_id)


def record_catalog_entity(db: AsyncSession, model: type[Any], entity_id: str) -> None:
    """Called from `catalog/service.py` create / update / retire.

    The catalogue service is generic over 32 entities; only `TimeBand` has a
    target table, so the filter lives here rather than putting a mirror-shaped
    conditional into the shared CRUD.
    """
    if not settings.mirror_enabled:
        return
    if getattr(model, "__tablename__", None) == "time_bands":
        _record(db, TIME_BAND, entity_id)


def record_prefix(db: AsyncSession, entity_id: str) -> None:
    _record(db, PREFIX, entity_id)


def record_prefix_delete(db: AsyncSession, prefix: str, zone_code: str) -> None:
    """A hard delete has to carry its data: after the commit the row is gone."""
    _record(db, PREFIX_DELETE, (prefix, zone_code))


def record_subscriber_offer(db: AsyncSession, entity_id: str) -> None:
    if settings.mirror_subscriber_enabled:
        _record(db, SUBSCRIBER_OFFER, entity_id)


def record_bundle_balance(db: AsyncSession, entity_id: str) -> None:
    if settings.mirror_subscriber_enabled:
        _record(db, BUNDLE_BALANCE, entity_id)


# --- Drain (called from get_session, post-commit) ----------------------------


async def drain(db: AsyncSession) -> int:
    """Flush the queue into the mirror. Never raises. Returns rows attempted."""
    if not settings.mirror_enabled:
        return 0
    queue = db.info.pop(_QUEUE, None)
    if not queue:
        return 0

    # Imported here, not at module scope: with the mirror disabled this module is
    # still imported by the hooked files, and nothing should pull in an engine or
    # a table definition that will never be used.
    from app.modules.mirror import sync

    handlers = {
        RULE_VERSION: lambda p: sync.push_rule_version(db, p),
        LEGACY_RULE: lambda p: sync.push_legacy_rule(db, p),
        LEGACY_RULE_DELETE: lambda p: sync.delete_legacy_rule(p),
        TIME_BAND: lambda p: sync.push_time_band(db, p),
        PREFIX: lambda p: sync.push_destination_prefix(db, p),
        PREFIX_DELETE: lambda p: sync.delete_destination_prefix(p[0], p[1]),
        SUBSCRIBER_OFFER: lambda p: sync.push_subscriber_offer(db, p),
        BUNDLE_BALANCE: lambda p: sync.push_bundle_balance(db, p),
    }

    # De-duplicate: one request can write the same version several times (a
    # lifecycle walk touches rule and version separately), and mirroring it twice
    # is wasted round trips against an upsert that would produce the same row.
    seen: set[tuple[str, Any]] = set()
    attempted = 0
    for kind, payload in queue:
        key = (kind, payload)
        if key in seen:
            continue
        seen.add(key)
        handler = handlers.get(kind)
        if handler is None:
            continue
        attempted += 1
        try:
            await handler(payload)
        except Exception as exc:
            # The entire fail-open guarantee is this block. Nothing propagates.
            log.warning(
                "mirror_write_failed", kind=kind, payload=str(payload), error=repr(exc)
            )
    return attempted


async def reconcile_vocabulary() -> dict[str, int]:
    """Startup reconcile of the three lookup tables. Never raises.

    Best-effort by design: if the mirror is unreachable at boot this logs and
    moves on, and the first rule write re-seeds the vocabulary itself (see
    ``vocabulary.ensure`` and the retry in ``sync._write_rule``). Startup is a
    head start, not the only chance.
    """
    if not settings.mirror_enabled:
        return {}
    try:
        from app.modules.mirror import vocabulary
        from app.modules.mirror.engine import mirror_session

        async with mirror_session() as mirror:
            counts = await vocabulary.reconcile(mirror)
        log.info("mirror_vocabulary_reconciled", **counts)
        return counts
    except Exception as exc:
        log.warning("mirror_vocabulary_failed", error=repr(exc))
        # Leave the ensure-flag unset so the next write retries rather than
        # assuming a reconcile that never landed.
        return {}
