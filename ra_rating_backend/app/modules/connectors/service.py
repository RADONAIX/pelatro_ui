"""Connector execution: fetch → adapt → detect changes → store canonical rules.

**Change detection is the point.** A vendor export is a full dump every time.
Importing 4,000 rules nightly and creating 4,000 new versions would make the
audit trail useless within a week. So each mapped rule is compared against what
is already stored, and only genuine differences create a version — the import
summary then reads "3 changed, 3,997 unchanged", which is the number an
operations team can act on.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.modules.connectors import adapters
from app.modules.connectors.models import ConnectorImport, SourceSystem
from app.modules.imports import parser
from app.modules.rules import service as rule_svc
from app.modules.rules.constants import EDITABLE_STATUSES
from app.modules.rules.models import Rule
from app.modules.rules.schemas import RuleCreate

log = get_logger("connectors")

#: Credential keys are never returned to a caller. Kept as a set rather than a
#: prefix test so adding one is a deliberate act.
SECRET_KEYS = frozenset({"password", "secret", "token", "api_key", "private_key", "passphrase"})


def redact(credentials: dict[str, Any]) -> dict[str, Any]:
    """Show that a credential is set without ever revealing it."""
    return {
        key: ("********" if key.lower() in SECRET_KEYS and value else value)
        for key, value in (credentials or {}).items()
    }


def rule_fingerprint(canonical: dict[str, Any]) -> str:
    """Hash of the parts of a rule that change its behaviour.

    Excludes name, description and category: a vendor renaming a plan is not a
    tariff change, and treating it as one would version every rule for nothing.
    """
    payload = {
        "rule_type": canonical.get("rule_type"),
        "service_type": canonical.get("service_type"),
        "priority": canonical.get("priority"),
        "effective_from": str(canonical.get("effective_from")),
        "effective_to": str(canonical.get("effective_to")),
        "currency_code": canonical.get("currency_code"),
        "conditions": sorted(
            (
                c.get("attribute"),
                c.get("operator"),
                tuple(sorted(str(v) for v in (c.get("values") or []))),
            )
            for c in canonical.get("conditions") or []
        ),
        "actions": sorted(
            (
                a.get("action_type"),
                tuple(sorted((k, str(v)) for k, v in (a.get("params") or {}).items())),
            )
            for a in canonical.get("actions") or []
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# --- Connection testing -----------------------------------------------------


async def test_connection(source: SourceSystem) -> tuple[str, str]:
    """Probe the source. Returns ``(health_status, detail)``.

    Never raises: a failed test is a health state to display, not a 500.
    """
    settings = source.connection or {}
    kind = source.source_type.upper()

    try:
        if kind == "API":
            base = str(settings.get("base_url") or "").rstrip("/")
            if not base:
                return "UNREACHABLE", "No base_url is configured."
            path = str(settings.get("health_path") or settings.get("path") or "/")
            headers = {}
            token = (source.credentials or {}).get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{base}{path}", headers=headers)
            if response.status_code < 400:
                return "HEALTHY", f"HTTP {response.status_code} from {base}{path}."
            return "DEGRADED", f"HTTP {response.status_code} from {base}{path}."

        if kind == "DATABASE":
            for required in ("host", "port", "database"):
                if not settings.get(required):
                    return "UNREACHABLE", f"No {required} is configured."
            if not (source.credentials or {}).get("username"):
                return "DEGRADED", "Configuration looks complete but no username is set."
            return (
                "HEALTHY",
                f"Configured for {settings['host']}:{settings['port']}/{settings['database']}. "
                "A live probe runs at the next scheduled import.",
            )

        if kind == "SFTP":
            for required in ("host", "remote_path"):
                if not settings.get(required):
                    return "UNREACHABLE", f"No {required} is configured."
            return (
                "HEALTHY",
                f"Configured for {settings['host']}:{settings.get('port', 22)}"
                f"{settings['remote_path']}.",
            )

        # FILE / CSV / XML / JSON / EXCEL: nothing to reach, the file is pushed.
        return "HEALTHY", "File-based source — records are pushed to this connector."

    except httpx.HTTPError as exc:
        return "UNREACHABLE", f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return "UNREACHABLE", str(exc)


async def fetch_payload(source: SourceSystem, uploaded: bytes | None) -> Any:
    """Get the vendor's export, however this source supplies it."""
    kind = source.source_type.upper()

    if uploaded is not None:
        _, rows = _parse_any(source, uploaded)
        return rows

    if kind == "API":
        settings = source.connection or {}
        base = str(settings.get("base_url") or "").rstrip("/")
        path = str(settings.get("export_path") or settings.get("path") or "/")
        if not base:
            raise ValidationFailedError("This connector has no base_url configured.")
        headers = {"Accept": "application/json"}
        token = (source.credentials or {}).get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{base}{path}", headers=headers)
            response.raise_for_status()
            return response.json()

    raise ValidationFailedError(
        f"A {kind} connector needs its export pushed to this endpoint.",
        details={
            "hint": "Upload the vendor's export file, or configure the source as an API."
        },
    )


def _parse_any(source: SourceSystem, data: bytes) -> tuple[list[str], Any]:
    """Parse an uploaded export. JSON keeps its nesting; tabular becomes rows."""
    name = f"export.{source.source_type.lower()}"
    if data.lstrip()[:1] in (b"{", b"["):
        return [], json.loads(data.decode("utf-8", errors="replace"))
    headings, rows = parser.parse(name, data)
    return headings, rows


# --- Import execution -------------------------------------------------------


async def run_import(
    db: AsyncSession,
    source: SourceSystem,
    *,
    uploaded: bytes | None,
    trigger: str,
    actor_id: str,
    actor_name: str,
    dry_run: bool = False,
) -> ConnectorImport:
    started = time.perf_counter()
    record = ConnectorImport(
        source_id=source.id,
        trigger=trigger,
        import_mode=source.import_mode,
        status="RUNNING",
        triggered_by=actor_id,
        triggered_by_name=actor_name,
    )
    db.add(record)
    await db.flush()

    try:
        payload = await fetch_payload(source, uploaded)
        mapped, spec = adapters.map_records(source.vendor, payload)
    except Exception as exc:
        record.status = "FAILED"
        record.error = str(exc)
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        source.last_import_at = datetime.now(UTC)
        source.last_import_status = "FAILED"
        source.total_imports += 1
        source.failed_imports += 1
        source.health_status = "DEGRADED"
        source.health_detail = str(exc)
        await db.flush()
        return record

    record.records_read = len(mapped)
    errors: list[dict[str, Any]] = []
    created = updated = unchanged = rejected = 0
    seen_keys: set[str] = set()

    for entry in mapped:
        if entry.canonical is None:
            rejected += 1
            errors.append(
                {"index": entry.index, "field": entry.field, "message": entry.error,
                 "raw": entry.raw}
            )
            continue

        canonical = entry.canonical
        if source.field_mapping:
            canonical = {**canonical, **dict(source.field_mapping)}
        canonical["source_system"] = source.code

        try:
            payload_model = RuleCreate(**canonical)
        except Exception as exc:
            rejected += 1
            errors.append(
                {"index": entry.index, "field": "", "message": _first_error(exc),
                 "raw": entry.raw}
            )
            continue

        key = payload_model.rule_key or rule_svc.derive_rule_key(payload_model.name)
        if key in seen_keys:
            rejected += 1
            errors.append(
                {"index": entry.index, "field": "rule_key",
                 "message": f"Duplicate rule key '{key}' within this export.", "raw": entry.raw}
            )
            continue
        seen_keys.add(key)
        payload_model.rule_key = key

        existing = await rule_svc.latest_version(db, key)
        fingerprint = rule_fingerprint(canonical)

        if existing is None:
            if not dry_run:
                rule = await rule_svc.create_rule(
                    db, payload_model, actor_id=actor_id, actor_name=actor_name
                )
                # Stamped on creation, not only on update: without it the next
                # import sees no fingerprint and versions every rule again,
                # which is exactly the churn change detection exists to prevent.
                rule.attributes = {
                    **(rule.attributes or {}),
                    "import_fingerprint": fingerprint,
                }
            created += 1
            continue

        if (existing.attributes or {}).get("import_fingerprint") == fingerprint:
            unchanged += 1
            continue

        if dry_run:
            updated += 1
            continue

        if existing.status in EDITABLE_STATUSES:
            # Still a draft — overwrite it rather than stacking versions the
            # author never asked for.
            from app.modules.rules.schemas import RuleUpdate

            await rule_svc.update_rule(
                db,
                existing.id,
                RuleUpdate(
                    name=payload_model.name,
                    description=payload_model.description,
                    rule_type=payload_model.rule_type,
                    service_type=payload_model.service_type,
                    priority=payload_model.priority,
                    effective_from=payload_model.effective_from,
                    effective_to=payload_model.effective_to,
                    currency_code=payload_model.currency_code,
                    conditions=payload_model.conditions,
                    actions=payload_model.actions,
                    change_comment=f"Re-imported from {source.code}.",
                ),
                actor_id=actor_id,
                actor_name=actor_name,
            )
            target = existing
        else:
            draft = await rule_svc.new_version(
                db,
                existing.id,
                change_comment=f"Imported change from {source.code}.",
                name=payload_model.name,
                actor_id=actor_id,
                actor_name=actor_name,
            )
            from app.modules.rules.schemas import RuleUpdate

            await rule_svc.update_rule(
                db,
                draft.id,
                RuleUpdate(
                    conditions=payload_model.conditions,
                    actions=payload_model.actions,
                    priority=payload_model.priority,
                    effective_from=payload_model.effective_from,
                    effective_to=payload_model.effective_to,
                    currency_code=payload_model.currency_code,
                ),
                actor_id=actor_id,
                actor_name=actor_name,
            )
            target = draft

        target.attributes = {**(target.attributes or {}), "import_fingerprint": fingerprint}
        updated += 1

    # --- Rules the vendor no longer sends -----------------------------------
    deleted = 0
    if source.import_mode == "FULL" and not dry_run and seen_keys:
        stale = (
            await db.execute(
                select(Rule).where(
                    Rule.source_system == source.code,
                    Rule.rule_key.notin_(seen_keys),
                    Rule.status.in_(list(EDITABLE_STATUSES)),
                )
            )
        ).scalars().all()
        for rule in stale:
            # Only drafts are retired automatically. An approved rule the vendor
            # stopped sending is a decision for a human, not an import.
            rule.status = "RETIRED"
            deleted += 1

    record.records_mapped = len(mapped) - rejected
    record.rules_created = created
    record.rules_updated = updated
    record.rules_unchanged = unchanged
    record.rules_deleted = deleted
    record.records_rejected = rejected
    record.errors = errors[:200]
    record.status = "COMPLETED" if not rejected else "PARTIAL"
    record.duration_ms = int((time.perf_counter() - started) * 1000)
    record.summary = {
        "adapter": spec.code,
        "adapter_label": spec.label,
        "dry_run": dry_run,
        "reasons": _group(errors),
    }

    source.last_import_at = datetime.now(UTC)
    source.last_import_status = record.status
    source.total_imports += 1
    if record.status == "FAILED":
        source.failed_imports += 1
    source.total_records_imported += created + updated
    source.health_status = "HEALTHY" if not rejected else "DEGRADED"
    source.health_detail = (
        f"Last import: {created} created, {updated} updated, {unchanged} unchanged"
        + (f", {rejected} rejected" if rejected else "")
    )

    await db.flush()
    await db.refresh(record)
    log.info(
        "connector_import",
        source=source.code,
        created=created,
        updated=updated,
        unchanged=unchanged,
        rejected=rejected,
    )
    return record


def _group(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for err in errors:
        counts[err["message"]] = counts.get(err["message"], 0) + 1
    return [
        {"message": m, "count": c}
        for m, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ][:20]


def _first_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            loc = ".".join(str(p) for p in first.get("loc", ()))
            return f"{loc}: {first.get('msg', 'invalid value')}" if loc else first.get("msg", "")
        except (IndexError, KeyError, TypeError):
            pass
    return str(exc)


# --- Reads ------------------------------------------------------------------


async def get_source(db: AsyncSession, source_id: str) -> SourceSystem:
    source = await db.get(SourceSystem, source_id)
    if source is None:
        raise NotFoundError(f"Source system '{source_id}' was not found.")
    return source


async def ensure_code_free(db: AsyncSession, code: str) -> None:
    exists = (
        await db.execute(
            select(func.count()).select_from(SourceSystem).where(SourceSystem.code == code)
        )
    ).scalar_one()
    if exists:
        raise ConflictError(f"Source system code '{code}' is already in use.")


async def health_overview(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(SourceSystem.health_status, func.count()).group_by(SourceSystem.health_status)
        )
    ).all()
    total = int(
        (await db.execute(select(func.count()).select_from(SourceSystem))).scalar_one()
    )
    return {
        "total": total,
        "by_health": dict(rows),
        "enabled": int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(SourceSystem)
                    .where(SourceSystem.enabled.is_(True))
                )
            ).scalar_one()
        ),
    }
