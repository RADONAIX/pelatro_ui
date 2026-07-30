"""Pydantic schemas for the monitoring module."""

from __future__ import annotations

from pydantic import BaseModel


class MonitoredServer(BaseModel):
    """One server in the monitored fleet, as declared by its Prometheus scrape
    target labels (deploy/prometheus/targets/nodes.json)."""

    id: str  # `server` label — matches the Grafana dashboard's $server variable
    label: str  # `display` label — what the UI selector shows
    role: str | None = None  # app | db | report | archive
    tier: str | None = None  # master | slave
    feed: str | None = None  # air | sdp | svc | all (app servers)
    isApi: bool = False  # runs the RADONAIX API → the API-health view applies
    up: bool = False  # is node_exporter currently being scraped successfully
