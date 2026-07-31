"""Celery broker (Redis) + worker liveness checks.

Readiness-only signals: the API works fine without them, but if the broker is
unreachable or **no worker is running**, exports silently sit at `Queued` forever.
Surfacing both in /health/ready turns that into an obvious, diagnosable state.

Both calls are blocking (kombu/celery are sync), so they run in a thread and use
short timeouts — a health probe must never hang the event loop.
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger

log = get_logger("broker")

_BROKER_TIMEOUT_SECONDS = 2.0
_WORKER_PING_TIMEOUT_SECONDS = 1.0


def _ping_broker_sync() -> bool:
    from app.workers.celery_app import celery

    conn = None
    try:
        conn = celery.connection()
        conn.ensure_connection(max_retries=0, timeout=_BROKER_TIMEOUT_SECONDS)
        return True
    except Exception:  # noqa: BLE001 — any failure means "not reachable"
        return False
    finally:
        if conn is not None:
            try:
                conn.release()
            except Exception:  # noqa: BLE001
                pass


def _ping_workers_sync() -> bool:
    from app.workers.celery_app import celery

    try:
        replies = celery.control.ping(timeout=_WORKER_PING_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — broker down → no workers reachable
        return False
    return bool(replies)


async def ping_broker() -> bool:
    """Is the Celery broker (Redis) reachable?"""
    return await asyncio.to_thread(_ping_broker_sync)


async def ping_workers() -> bool:
    """Is at least one Celery worker consuming? False → exports stay Queued."""
    return await asyncio.to_thread(_ping_workers_sync)
