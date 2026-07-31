"""Monitoring business logic: read the monitored fleet from Prometheus.

Prometheus' scrape targets (``deploy/prometheus/targets/*.json``) are the SINGLE
source of truth for which servers exist. We read the live ``server`` / ``display``
/ ``role`` / ``tier`` / ``feed`` labels off the ``node`` job, so appending one
target object is all it takes for a server to show up in Grafana *and* the UI —
no env var to maintain, no UI rebuild, and no chance of the two lists drifting.

Degrades gracefully: if Prometheus is disabled or unreachable we return an empty
fleet rather than failing the page.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.monitoring import schemas

log = get_logger("monitoring")

# Display order of the fleet: the report servers first (that's where you look),
# then the databases, the Airflow app servers, and finally the archive boxes.
_ROLE_ORDER = {"report": 0, "db": 1, "app": 2, "archive": 3}


async def _instant_query(expr: str) -> list[dict[str, Any]]:
    """Run a Prometheus instant query. Returns [] when disabled/unreachable."""
    if not settings.prometheus_enabled:
        return []
    url = f"{settings.prometheus_url.rstrip('/')}/api/v1/query"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"query": expr})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:  # unreachable / bad JSON
        log.info("prometheus_unavailable", error=str(exc))
        return []
    if payload.get("status") != "success":
        return []
    return payload.get("data", {}).get("result", [])


def _is_up(series: dict[str, Any]) -> bool:
    # Prometheus instant vector: {"metric": {...}, "value": [<ts>, "1"|"0"]}
    value = series.get("value") or [None, "0"]
    return str(value[-1]) == "1"


async def list_servers() -> list[schemas.MonitoredServer]:
    """The monitored fleet, derived from the `node` job's scrape targets. The
    `radonaix_api` job tells us which of them run our API (so the UI can show the
    API-health view only there)."""
    node_series = await _instant_query('up{job="node"}')
    api_series = await _instant_query('up{job="radonaix_api"}')
    api_servers = {s.get("metric", {}).get("server") for s in api_series}

    servers: dict[str, schemas.MonitoredServer] = {}
    for series in node_series:
        labels = series.get("metric", {})
        server_id = labels.get("server")
        if not server_id:
            continue  # a target without the `server` label can't be shown
        servers[server_id] = schemas.MonitoredServer(
            id=server_id,
            label=labels.get("display") or server_id,
            role=labels.get("role"),
            tier=labels.get("tier"),
            feed=labels.get("feed"),
            isApi=server_id in api_servers,
            up=_is_up(series),
        )
    return sorted(
        servers.values(), key=lambda s: (_ROLE_ORDER.get(s.role or "", 9), s.id)
    )
