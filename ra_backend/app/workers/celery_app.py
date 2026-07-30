"""Celery application instance.

Runs the async export tasks (`app.workers.tasks`) that back the Download Center.
Exports can run for hours (billion-row reports), so the config is tuned for long
tasks: a dedicated ``exports`` queue, a Redis ``visibility_timeout`` that exceeds
the longest task (else Redis re-delivers a still-running task → duplicate export),
and generous per-task time limits.

Run the export worker with:
    celery -A app.workers.celery_app:celery worker -Q exports --concurrency=8 --loglevel=info
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery = Celery(
    "radonaix",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    # All export tasks land on a dedicated queue so a multi-hour export never
    # blocks (or is blocked by) other work, and can be served by a tuned worker.
    task_default_queue="exports",
    task_routes={"exports.*": {"queue": "exports"}},
    # CRITICAL for long tasks: with task_acks_late, Redis re-queues an un-acked
    # message after visibility_timeout. Default is 1 h → a >1 h export would be
    # picked up by a second worker while the first still runs. Raise it well above
    # the longest single task (each multi-part sub-task is bounded to one date part).
    broker_transport_options={
        "visibility_timeout": settings.export_visibility_timeout_seconds,
    },
    # A wedged task fails cleanly instead of hanging a worker slot forever. The
    # soft limit raises SoftTimeLimitExceeded (caught → Failed); hard limit is a
    # backstop kill 5 min later. This GLOBAL default is the JOB limit (single-file
    # run_export + finalize_export); run_export_part overrides it down to the
    # tighter PART limit in its decorator. Both stay under visibility_timeout.
    task_soft_time_limit=settings.export_job_soft_limit_seconds,
    task_time_limit=settings.export_job_soft_limit_seconds + 300,
)
