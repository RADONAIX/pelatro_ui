"""File-based rule import API.

    GET  /rule-imports/columns            what the importer understands
    POST /rule-imports/preview            parse + map + validate, write nothing
    POST /rule-imports                    same pipeline, then commit valid rows
    GET  /rule-imports                    history
    GET  /rule-imports/{id}               batch detail with per-row outcome
    GET  /rule-imports/{id}/rejected.csv  the failed rows, ready to fix and resend
    GET|POST /rule-import-templates       saved column mappings
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import DbSession, PageParams, principal_with, require
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.rbac import RatingPermKey
from app.modules.imports import schemas as s
from app.modules.imports import service as svc
from app.modules.imports.constants import ALL_COLUMNS, RowStatus
from app.modules.imports.models import RuleImportBatch, RuleImportRow, RuleImportTemplate
from app.modules.imports.parser import EXCEL_AVAILABLE

router = APIRouter(tags=["rule-imports"])

_view = require(RatingPermKey.RULES, "view")
RuleImporter = principal_with(RatingPermKey.RULES, "edit")

#: Uploads are held in memory for the whole pipeline, so cap them well below the
#: worker's footprint. 50k rows of tariff CSV is ~15 MB.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            details={"hint": "Split the sheet, or import it through a connector in Phase 5."},
        )
    return data


def _parse_mapping(raw: str | None) -> dict[str, str | None] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"`mapping` is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError("`mapping` must be an object of heading -> column key.")
    return {str(k): (str(v) if v else None) for k, v in parsed.items()}


@router.get(
    "/rule-imports/columns",
    response_model=list[s.ColumnInfo],
    summary="Columns the importer understands",
    dependencies=[Depends(_view)],
)
async def import_columns() -> list[s.ColumnInfo]:
    """Drives the mapping screen, and doubles as the spec handed to a vendor."""
    return [
        s.ColumnInfo(
            key=c.key, label=c.label, kind=c.kind, required=c.required,
            description=c.description, aliases=list(c.aliases),
        )
        for c in ALL_COLUMNS
    ]


@router.get("/rule-imports/capabilities", summary="Which upload formats are available")
async def capabilities(_: Annotated[object, Depends(_view)]) -> dict[str, object]:
    return {
        "formats": ["csv", "tsv", "json", "xml"] + (["xlsx"] if EXCEL_AVAILABLE else []),
        "excel_available": EXCEL_AVAILABLE,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_rows": 50_000,
        "max_rules_per_rule_set": settings.max_rules_per_rule_set,
    }


@router.post(
    "/rule-imports/preview",
    response_model=s.PreviewResponse,
    summary="Parse, map and validate an upload without writing anything",
)
async def preview(
    db: DbSession,
    principal: RuleImporter,
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON: {heading: column_key|null}"),
    template_code: str | None = Form(None),
    effective_from: date | None = Form(None),
) -> s.PreviewResponse:
    del principal
    data = await _read_upload(file)

    resolved_mapping = _parse_mapping(mapping)
    if resolved_mapping is None and template_code:
        tpl = (
            await db.execute(
                select(RuleImportTemplate).where(RuleImportTemplate.code == template_code)
            )
        ).scalar_one_or_none()
        if tpl is None:
            raise NotFoundError(f"Mapping template '{template_code}' was not found.")
        resolved_mapping = dict(tpl.mapping)

    headings, used_mapping, rows = await svc.run_pipeline(
        db,
        filename=file.filename or "upload",
        data=data,
        mapping=resolved_mapping,
        default_effective_from=effective_from,
    )

    mapped_keys = {v for v in used_mapping.values() if v}
    valid = sum(1 for r in rows if r.status != RowStatus.REJECTED.value)
    sample = rows[: svc.PREVIEW_ROW_LIMIT]

    return s.PreviewResponse(
        filename=file.filename or "upload",
        headings=headings,
        mapping=used_mapping,
        unmapped_headings=[h for h, k in used_mapping.items() if not k],
        missing_required=[c for c in svc.REQUIRED_COLUMNS if c not in mapped_keys],
        total_rows=len(rows),
        valid_rows=valid,
        rejected_rows=len(rows) - valid,
        rows=[s.PreviewRow(**r.as_dict()) for r in sample],
        truncated=len(rows) > len(sample),
    )


@router.post(
    "/rule-imports",
    response_model=s.CommitResponse,
    status_code=201,
    summary="Import the valid rows of an upload as draft rules",
)
async def commit_import(
    db: DbSession,
    principal: RuleImporter,
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    template_code: str | None = Form(None),
    rule_set_id: str | None = Form(None),
    source_system: str = Form("FILE_IMPORT"),
    effective_from: date | None = Form(None),
) -> s.CommitResponse:
    data = await _read_upload(file)

    resolved_mapping = _parse_mapping(mapping)
    if resolved_mapping is None and template_code:
        tpl = (
            await db.execute(
                select(RuleImportTemplate).where(RuleImportTemplate.code == template_code)
            )
        ).scalar_one_or_none()
        if tpl is None:
            raise NotFoundError(f"Mapping template '{template_code}' was not found.")
        resolved_mapping = dict(tpl.mapping)

    headings, used_mapping, rows = await svc.run_pipeline(
        db,
        filename=file.filename or "upload",
        data=data,
        mapping=resolved_mapping,
        default_effective_from=effective_from,
        source_system=source_system,
    )

    batch = await svc.commit(
        db,
        filename=file.filename or "upload",
        headings=headings,
        mapping=used_mapping,
        rows=rows,
        rule_set_id=rule_set_id or None,
        source_system=source_system,
        actor_id=principal.id,
        actor_name=principal.full_name,
    )
    return s.CommitResponse.model_validate(batch)


@router.get(
    "/rule-imports",
    response_model=list[s.BatchSummary],
    summary="Import history",
    dependencies=[Depends(_view)],
)
async def list_batches(db: DbSession, page: PageParams) -> list[RuleImportBatch]:
    stmt = (
        select(RuleImportBatch)
        .order_by(RuleImportBatch.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get(
    "/rule-imports/{batch_id}",
    response_model=s.BatchDetail,
    summary="Batch detail with per-row outcome",
    dependencies=[Depends(_view)],
)
async def get_batch(
    db: DbSession,
    batch_id: str,
    status: str | None = Query(None, description="Filter rows, e.g. REJECTED"),
    limit: int = Query(200, ge=1, le=2000),
) -> s.BatchDetail:
    batch = await db.get(RuleImportBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"Import batch '{batch_id}' was not found.")

    stmt = (
        select(RuleImportRow)
        .where(RuleImportRow.batch_id == batch_id)
        .order_by(RuleImportRow.row_number)
        .limit(limit)
    )
    if status:
        stmt = stmt.where(RuleImportRow.status == status.upper())
    rows = list((await db.execute(stmt)).scalars().all())

    detail = s.BatchDetail.model_validate(batch)
    detail.rows = [s.BatchRow.model_validate(r) for r in rows]
    return detail


@router.get(
    "/rule-imports/{batch_id}/rejected.csv",
    response_class=PlainTextResponse,
    summary="Download the rejected rows, with reasons",
    dependencies=[Depends(_view)],
)
async def download_rejected(db: DbSession, batch_id: str) -> PlainTextResponse:
    batch = await db.get(RuleImportBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"Import batch '{batch_id}' was not found.")
    stmt = (
        select(RuleImportRow)
        .where(
            RuleImportRow.batch_id == batch_id,
            RuleImportRow.status == RowStatus.REJECTED.value,
        )
        .order_by(RuleImportRow.row_number)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    body = svc.rejected_csv(rows, (batch.summary or {}).get("headings"))
    name = f"rejected-{batch.filename.rsplit('.', 1)[0]}.csv"
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- Mapping templates ------------------------------------------------------


@router.get(
    "/rule-import-templates",
    response_model=list[s.TemplateRead],
    summary="Saved column mappings",
    dependencies=[Depends(_view)],
)
async def list_templates(db: DbSession) -> list[RuleImportTemplate]:
    stmt = select(RuleImportTemplate).order_by(RuleImportTemplate.name)
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/rule-import-templates",
    response_model=s.TemplateRead,
    status_code=201,
    summary="Save a column mapping for reuse",
)
async def create_template(
    db: DbSession, payload: s.TemplateCreate, principal: RuleImporter
) -> RuleImportTemplate:
    existing = (
        await db.execute(
            select(func.count())
            .select_from(RuleImportTemplate)
            .where(RuleImportTemplate.code == payload.code)
        )
    ).scalar_one()
    if existing:
        raise ConflictError(f"Template code '{payload.code}' is already in use.")
    tpl = RuleImportTemplate(**payload.model_dump(), created_by=principal.id)
    db.add(tpl)
    await db.flush()
    await db.refresh(tpl)
    return tpl


@router.delete(
    "/rule-import-templates/{template_id}",
    status_code=204,
    summary="Delete a saved mapping",
)
async def delete_template(
    db: DbSession, template_id: str, principal: RuleImporter
) -> None:
    del principal
    tpl = await db.get(RuleImportTemplate, template_id)
    if tpl is None:
        raise NotFoundError(f"Template '{template_id}' was not found.")
    await db.delete(tpl)
