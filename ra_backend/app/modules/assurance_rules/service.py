"""Authored assurance rules — CRUD against application_schema.assurance_rule.

Raw SQL through the ra_pg integration rather than the ORM: this table lives in
rafms_rating on the RA Postgres host, not in the app database that Base/Alembic
own, so it has no place in the app's metadata.

Every statement here is parameterised. The only interpolated values are the
schema name (from settings, not from a request) and, in the ORDER BY, a column
name chosen from a fixed whitelist.
"""

from __future__ import annotations

import json
import re
from datetime import time as dt_time
from typing import Any

from app.core.config import settings
from app.core.errors import AppError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.integrations import ra_postgres
from app.modules.reconciliation import service as recon_service

log = get_logger("assurance_rules")

# The columns a caller may change, in the order the INSERT lists them.
_EDITABLE = (
    "name",
    "description",
    "category",
    "entity",
    "severity",
    "frequency",
    "execution_time",
    "breach_threshold",
    "state",
    "params",
    "case_routing",
    "comparison",
)

_ALL = ("id", "assurance_id", *_EDITABLE, "created_by", "created_at", "updated_at")

# A control-id prefix: two to four capitals, e.g. BA, UA, RA.
_PREFIX_RE = re.compile(r"^[A-Z]{2,4}$")

# Authored rules are numbered from here so they never collide with the
# provisioned control range (BA001–BA180 and friends).
_FIRST_SEQUENCE = 901


def _to_time(value: str) -> dt_time:
    """"HH:mm" -> datetime.time.

    asyncpg binds a `time` column from a time object, not a string — passing the
    string through fails the INSERT with "'str' object has no attribute 'hour'".
    The value is already pattern-validated by the schema; this only converts.
    """
    hour, _, minute = str(value).partition(":")
    try:
        return dt_time(int(hour), int(minute[:2]))
    except ValueError:
        return dt_time(0, 0)


def _table() -> str:
    # settings, never request input — see the module docstring.
    return f"{settings.app_rules_schema}.assurance_rule"


def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    """DB row (snake_case) -> the UI's CustomRule shape (camelCase)."""
    return {
        "id": row["id"],
        "appId": row["assurance_id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "entity": row["entity"],
        "severity": row["severity"],
        "frequency": row["frequency"],
        # `time` comes back as a datetime.time; the wire shape is "HH:mm".
        "executionTime": row["execution_time"].strftime("%H:%M"),
        "state": row["state"],
        "params": row["params"] or {},
        "caseRouting": row["case_routing"],
        "comparison": row["comparison"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _payload_to_params(payload: Any) -> dict[str, Any]:
    """Pydantic model -> bind parameters. JSONB columns are serialised here
    because asyncpg will not adapt a dict to jsonb on its own."""
    data = payload.model_dump()
    case_routing = data.get("caseRouting")
    comparison = data.get("comparison")
    # The threshold is stored in its own column as well as inside case_routing:
    # the engine filters on it after a run, and a jsonb lookup would not use an
    # index. The routing block stays the source the UI round-trips.
    breach_threshold = int((case_routing or {}).get("breachThreshold") or 1)
    return {
        "name": data["name"].strip(),
        "description": data["description"],
        "category": data["category"],
        "entity": data["entity"],
        "severity": data["severity"],
        "frequency": data["frequency"],
        "execution_time": _to_time(data["executionTime"]),
        "breach_threshold": max(1, breach_threshold),
        "state": data["state"],
        "params": json.dumps(data.get("params") or {}),
        "case_routing": json.dumps(case_routing) if case_routing is not None else None,
        "comparison": json.dumps(comparison) if comparison is not None else None,
    }


async def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.query_database(settings.app_rules_db_name, sql, params)


async def _execute(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await ra_postgres.execute_database(settings.app_rules_db_name, sql, params)


async def list_rules(*, assurance_id: str, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """Every rule for ONE assurance, newest first.

    assurance_id is required rather than optional-with-a-default: an accidental
    unfiltered read is exactly the bug this screen must not have — Billing
    Assurance showing Usage's rules.
    """
    rows = await _query(
        f"SELECT {', '.join(_ALL)} FROM {_table()} "
        "WHERE assurance_id = :assurance_id "
        "ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
        {"assurance_id": assurance_id, "limit": limit, "offset": offset},
    )
    return [_row_to_api(r) for r in rows]


async def get_rule(rule_id: str) -> dict[str, Any]:
    rows = await _query(
        f"SELECT {', '.join(_ALL)} FROM {_table()} WHERE id = :id", {"id": rule_id}
    )
    if not rows:
        raise NotFoundError(f"Rule {rule_id} does not exist.")
    return _row_to_api(rows[0])


async def _next_id(assurance_id: str, prefix: str) -> str:
    """<prefix><n>, continuing from the highest number already used by this
    assurance. Reads max() rather than count() so deleting a rule cannot hand
    the next author an id that already existed."""
    rows = await _query(
        "SELECT coalesce(max(substring(id from '[0-9]+$')::int), 0) AS n "
        f"FROM {_table()} WHERE assurance_id = :assurance_id AND id LIKE :like",
        {"assurance_id": assurance_id, "like": f"{prefix}%"},
    )
    highest = int(rows[0]["n"] or 0)
    return f"{prefix}{max(highest + 1, _FIRST_SEQUENCE)}"


async def create_rule(
    *, assurance_id: str, prefix: str, payload: Any, created_by: str = ""
) -> dict[str, Any]:
    if not _PREFIX_RE.match(prefix):
        raise ValidationFailedError("A control-id prefix is two to four capital letters, e.g. BA.")

    params = _payload_to_params(payload)
    params["assurance_id"] = assurance_id
    params["created_by"] = created_by
    params["id"] = await _next_id(assurance_id, prefix)

    columns = ("id", "assurance_id", *_EDITABLE, "created_by")
    rows = await _execute(
        f"INSERT INTO {_table()} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{c}' for c in columns)}) "
        f"RETURNING {', '.join(_ALL)}",
        params,
    )
    stored = _row_to_api(rows[0])
    stored["reconciliation"] = await _compile_reconciliation(stored, trigger="create")
    return stored


async def update_rule(*, rule_id: str, payload: Any) -> dict[str, Any]:
    """Replace the editable fields. The id and assurance_id are untouched —
    a rule cannot move between assurances (see RuleUpdate)."""
    params = _payload_to_params(payload)
    params["id"] = rule_id
    rows = await _execute(
        f"UPDATE {_table()} SET {', '.join(f'{c} = :{c}' for c in _EDITABLE)} "
        f"WHERE id = :id RETURNING {', '.join(_ALL)}",
        params,
    )
    if not rows:
        raise NotFoundError(f"Rule {rule_id} does not exist.")
    stored = _row_to_api(rows[0])
    # An edited reconciliation is recompiled and re-run: the tables, keys or
    # metrics may have changed, and leaving yesterday's SQL registered would
    # keep producing a report the rule no longer describes.
    stored["reconciliation"] = await _compile_reconciliation(stored, trigger="update")
    return stored


async def _compile_reconciliation(rule: dict[str, Any], *, trigger: str) -> dict[str, Any] | None:
    """Compile and run a Reconciliation rule; a no-op for every other category.

    Deliberately non-fatal. The rule is already committed by the time this runs,
    and a compile or load failure is a property of the reconciliation, not of
    the rule — so the author keeps their rule and gets the reason back on the
    response instead of a 500 and a lost form. The failure is also on the
    execution record and the definition's status.
    """
    if not recon_service.is_reconciliation(rule.get("category")):
        return None
    # Activation is what triggers a run. A Draft rule is compiled and stored —
    # so its SQL and its report are ready — but it neither executes nor takes a
    # schedule until someone sets it Active.
    active = (rule.get("state") or "").strip().lower() == "active"
    try:
        return await recon_service.compile_and_run(
            rule=rule,
            trigger=trigger,
            triggered_by=rule.get("createdBy") or "",
            execute_now=active,
        )
    except AppError as exc:
        log.warning(
            "recon_compile_failed", rule_id=rule["id"], error=exc.message, details=exc.details
        )
        return {"executed": False, "error": exc.message, "details": exc.details}
    except Exception as exc:  # noqa: BLE001
        log.warning("recon_compile_failed", rule_id=rule["id"], error=str(exc))
        return {"executed": False, "error": str(exc)}


async def set_state(*, rule_id: str, state: str) -> dict[str, Any]:
    """Draft <-> Active, the one field the Controls table toggles inline."""
    if state not in ("Draft", "Active"):
        raise ValidationFailedError("State is either Draft or Active.")
    rows = await _execute(
        f"UPDATE {_table()} SET state = :state WHERE id = :id RETURNING {', '.join(_ALL)}",
        {"id": rule_id, "state": state},
    )
    if not rows:
        raise NotFoundError(f"Rule {rule_id} does not exist.")
    stored = _row_to_api(rows[0])
    # Moving a rule to Active is what starts it: it runs once immediately, and
    # only then takes its schedule. Pausing back to Draft leaves the last report
    # in place and simply stops the scheduler picking it up.
    if state == "Active":
        stored["reconciliation"] = await _compile_reconciliation(stored, trigger="activate")
    return stored


async def delete_rule(rule_id: str) -> None:
    # Drop the generated table and definition FIRST. recon_definition cascades
    # from this row, so deleting the rule first would take the definition with
    # it and leave the output table orphaned in the source database with no
    # metadata left to find it by.
    await recon_service.remove(rule_id)
    rows = await _execute(
        f"DELETE FROM {_table()} WHERE id = :id RETURNING id", {"id": rule_id}
    )
    if not rows:
        raise NotFoundError(f"Rule {rule_id} does not exist.")
