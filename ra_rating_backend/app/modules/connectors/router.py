"""Source-system and connector management API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.rbac import RatingPermKey
from app.modules.connectors import service as svc
from app.modules.connectors.adapters import ADAPTERS, PLANNED_VENDORS
from app.modules.connectors.models import ConnectorImport, SourceSystem

router = APIRouter(tags=["connectors"])

_view = require(RatingPermKey.CATALOG, "view")
ConnectorAdmin = principal_with(RatingPermKey.CATALOG, "edit")

SOURCE_TYPES = ("DATABASE", "API", "SFTP", "FILE", "XML", "JSON", "CSV", "EXCEL")
IMPORT_MODES = ("FULL", "INCREMENTAL", "CHANGE_ONLY")
CATEGORIES = ("RULES", "CDR", "REFERENCE")


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceCreate(Base):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    vendor: str
    category: str = "RULES"
    source_type: str
    connection: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    schedule: str | None = None
    import_mode: str = "FULL"
    field_mapping: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SourceUpdate(Base):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    source_type: str | None = None
    connection: dict[str, Any] | None = None
    #: Omit to leave the stored secrets untouched — sending back the redacted
    #: values the read endpoint returned must never overwrite them.
    credentials: dict[str, Any] | None = None
    schedule: str | None = None
    import_mode: str | None = None
    field_mapping: dict[str, Any] | None = None
    enabled: bool | None = None
    status: str | None = None


class SourceRead(Base):
    id: str
    code: str
    name: str
    description: str
    vendor: str
    category: str
    source_type: str
    status: str
    connection: dict[str, Any]
    credentials: dict[str, Any]
    schedule: str | None
    import_mode: str
    field_mapping: dict[str, Any]
    health_status: str
    health_detail: str
    last_tested_at: datetime | None
    last_import_at: datetime | None
    last_import_status: str | None
    total_imports: int
    failed_imports: int
    total_records_imported: int
    enabled: bool
    created_at: datetime


class ImportRead(Base):
    id: str
    source_id: str
    trigger: str
    import_mode: str
    status: str
    records_read: int
    records_mapped: int
    rules_created: int
    rules_updated: int
    rules_unchanged: int
    rules_deleted: int
    records_rejected: int
    summary: dict[str, Any]
    error: str | None
    duration_ms: int | None
    triggered_by_name: str | None
    created_at: datetime


class ImportDetail(ImportRead):
    errors: list[Any] = Field(default_factory=list)


def _read(source: SourceSystem) -> SourceRead:
    data = SourceRead.model_validate(source)
    data.credentials = svc.redact(source.credentials)
    return data


# --- Catalogue --------------------------------------------------------------


@router.get(
    "/connector-catalog",
    summary="Vendors, source types and import modes this platform supports",
    dependencies=[Depends(_view)],
)
async def catalog() -> dict[str, Any]:
    """What a connector can be configured as.

    Vendors with an adapter are listed with the fields their adapter reads;
    vendors named in the requirement but not yet mapped are listed separately so
    the gap is visible rather than silently absent.
    """
    return {
        "vendors": [
            {
                "code": a.spec.code,
                "vendor": a.spec.vendor,
                "label": a.spec.label,
                "description": a.spec.description,
                "record_path": a.spec.record_path,
                "expects": list(a.spec.expects),
                "notes": a.spec.notes,
                "adapter_available": True,
            }
            for a in ADAPTERS.values()
        ]
        + [
            {
                "code": code,
                "vendor": label,
                "label": label,
                "description": "No adapter yet — configure the source and import via CSV/JSON.",
                "record_path": "",
                "expects": [],
                "notes": "",
                "adapter_available": False,
            }
            for code, label in PLANNED_VENDORS
        ],
        "source_types": list(SOURCE_TYPES),
        "import_modes": list(IMPORT_MODES),
        "categories": list(CATEGORIES),
    }


@router.get(
    "/source-systems/health",
    summary="Connector health overview",
    dependencies=[Depends(_view)],
)
async def health(db: DbSession) -> dict[str, Any]:
    return await svc.health_overview(db)


# --- Source systems ---------------------------------------------------------


@router.get(
    "/source-systems",
    response_model=list[SourceRead],
    summary="Configured source systems",
    dependencies=[Depends(_view)],
)
async def list_sources(
    db: DbSession,
    page: PageParams,
    category: str | None = Query(None),
    vendor: str | None = Query(None),
) -> list[SourceRead]:
    stmt = select(SourceSystem)
    if category:
        stmt = stmt.where(SourceSystem.category == category.upper())
    if vendor:
        stmt = stmt.where(SourceSystem.vendor == vendor.upper())
    stmt = stmt.order_by(SourceSystem.code).limit(page.limit).offset(page.offset)
    return [_read(s) for s in (await db.execute(stmt)).scalars().all()]


@router.post(
    "/source-systems",
    response_model=SourceRead,
    status_code=201,
    summary="Register a source system",
)
async def create_source(
    db: DbSession, payload: SourceCreate, principal: ConnectorAdmin
) -> SourceRead:
    await svc.ensure_code_free(db, payload.code)
    source = SourceSystem(**payload.model_dump(), created_by=principal.id)
    db.add(source)
    await db.flush()
    await db.refresh(source)
    return _read(source)


@router.get(
    "/source-systems/{source_id}",
    response_model=SourceRead,
    summary="Source system detail",
    dependencies=[Depends(_view)],
)
async def get_source(db: DbSession, source_id: str) -> SourceRead:
    return _read(await svc.get_source(db, source_id))


@router.patch(
    "/source-systems/{source_id}",
    response_model=SourceRead,
    summary="Update a source system",
)
async def update_source(
    db: DbSession, source_id: str, payload: SourceUpdate, principal: ConnectorAdmin
) -> SourceRead:
    del principal
    source = await svc.get_source(db, source_id)
    data = payload.model_dump(exclude_unset=True)
    incoming = data.pop("credentials", None)
    if incoming is not None:
        # Merge rather than replace, and ignore any key still holding the mask —
        # otherwise a round-trip through the UI would blank every secret.
        merged = dict(source.credentials or {})
        for key, value in incoming.items():
            if value == "********":
                continue
            merged[key] = value
        source.credentials = merged
    for key, value in data.items():
        setattr(source, key, value)
    await db.flush()
    await db.refresh(source)
    return _read(source)


@router.delete(
    "/source-systems/{source_id}", status_code=204, summary="Remove a source system"
)
async def delete_source(db: DbSession, source_id: str, principal: ConnectorAdmin) -> None:
    del principal
    source = await svc.get_source(db, source_id)
    await db.delete(source)


@router.post(
    "/source-systems/{source_id}/test",
    response_model=SourceRead,
    summary="Test the connection and record the result",
)
async def test_connection(
    db: DbSession, source_id: str, principal: ConnectorAdmin
) -> SourceRead:
    del principal
    source = await svc.get_source(db, source_id)
    status, detail = await svc.test_connection(source)
    source.health_status = status
    source.health_detail = detail
    source.last_tested_at = datetime.now(tz=None).astimezone()
    await db.flush()
    await db.refresh(source)
    return _read(source)


# --- Imports ----------------------------------------------------------------


@router.post(
    "/source-systems/{source_id}/import",
    response_model=ImportDetail,
    status_code=201,
    summary="Run this connector — fetch or accept an export, map and store it",
)
async def run_import(
    db: DbSession,
    source_id: str,
    principal: ConnectorAdmin,
    file: UploadFile | None = File(None),
    dry_run: bool = Form(False),
) -> ConnectorImport:
    source = await svc.get_source(db, source_id)
    payload = await file.read() if file is not None else None
    return await svc.run_import(
        db,
        source,
        uploaded=payload,
        trigger="MANUAL",
        actor_id=principal.id,
        actor_name=principal.full_name,
        dry_run=dry_run,
    )


@router.get(
    "/source-systems/{source_id}/imports",
    response_model=list[ImportRead],
    summary="Import history for a connector",
    dependencies=[Depends(_view)],
)
async def list_imports(
    db: DbSession, source_id: str, page: PageParams
) -> list[ConnectorImport]:
    await svc.get_source(db, source_id)
    stmt = (
        select(ConnectorImport)
        .where(ConnectorImport.source_id == source_id)
        .order_by(ConnectorImport.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/connector-imports/{import_id}",
    response_model=ImportDetail,
    summary="Import detail with its rejected records",
    dependencies=[Depends(_view)],
)
async def get_import(db: DbSession, import_id: str) -> ConnectorImport:
    record = await db.get(ConnectorImport, import_id)
    if record is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Import '{import_id}' was not found.")
    return record


@router.post(
    "/connector-imports/{import_id}/retry",
    response_model=ImportDetail,
    status_code=201,
    summary="Re-run a failed import against its source",
)
async def retry_import(
    db: DbSession, import_id: str, principal: ConnectorAdmin
) -> ConnectorImport:
    record = await db.get(ConnectorImport, import_id)
    if record is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Import '{import_id}' was not found.")
    source = await svc.get_source(db, record.source_id)
    # Only API/DB sources can be retried unattended — a pushed file is gone.
    return await svc.run_import(
        db,
        source,
        uploaded=None,
        trigger="RETRY",
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
