"""Safety and API-contract tests for the mirror Rating Assurance job."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings
from app.core.database import Base
from app.core.errors import ValidationFailedError
from app.main import app
from app.modules.mirror_assurance import service


def _without_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)


def test_assurance_query_is_one_parameterized_read_only_statement():
    query = _without_comments(service.QUERY).strip()
    assert query.upper().startswith("WITH RECURSIVE")
    assert query.count(";") == 1
    assert ":window_start" in query
    assert ":window_end" in query
    assert ":reconciliation_tolerance" in query
    assert not re.search(
        r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|CALL|COPY)\b",
        query,
        re.IGNORECASE,
    )


def test_assurance_control_plane_tables_are_not_mirror_tables():
    assert {
        "mirror_assurance_schedule",
        "mirror_assurance_run",
        "mirror_assurance_result",
    } <= set(Base.metadata.tables)


def test_assurance_api_is_registered():
    paths = {route.path for route in app.routes}
    prefix = settings.api_prefix
    assert f"{prefix}/mirror-assurance/schedule" in paths
    assert f"{prefix}/mirror-assurance/runs" in paths
    assert f"{prefix}/mirror-assurance/runs/{{run_id}}/results" in paths


def test_initial_stage_contract_matches_the_job_monitor():
    assert [stage["key"] for stage in service.initial_stages()] == [
        "READ_MIRROR",
        "RECONCILE",
        "STORE_RESULTS",
    ]
    assert all(stage["status"] == "PENDING" for stage in service.initial_stages())


@pytest.mark.asyncio
async def test_manual_run_is_rejected_without_the_mirror(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", False)
    now = datetime.now(UTC)
    with pytest.raises(ValidationFailedError, match="MIRROR_ENABLED"):
        await service.create_run(
            object(),
            window_start=now - timedelta(hours=1),
            window_end=now,
            tolerance=Decimal("0.01"),
            trigger="MANUAL",
            actor_id="user-1",
            actor_name="User",
        )


@pytest.mark.asyncio
async def test_invalid_window_is_rejected_before_database_work(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", True)
    now = datetime.now(UTC)
    with pytest.raises(ValidationFailedError, match="end must be after"):
        await service.create_run(
            object(),
            window_start=now,
            window_end=now,
            tolerance=Decimal("0.01"),
            trigger="MANUAL",
            actor_id="user-1",
            actor_name="User",
        )


def test_result_values_are_json_safe_without_losing_decimal_precision():
    now = datetime.now(UTC)
    assert service._json_value({"charge": Decimal("0.500000"), "at": now}) == {
        "charge": "0.500000",
        "at": now.isoformat(),
    }


def test_missing_migration_is_recognized_through_database_wrappers():
    root = RuntimeError("relation does not exist")
    root.sqlstate = "42P01"  # type: ignore[attr-defined]
    wrapper = RuntimeError("SQLAlchemy ProgrammingError")
    wrapper.__cause__ = root
    assert service._missing_control_tables(wrapper) is True
    assert service._missing_control_tables(RuntimeError("connection refused")) is False
