"""Case management API.

Read paths (`/api/cases`, `/summary`, `/facets`) back the Case Management
screen; the write paths back triage actions; `/ingest` is what the rule engine
calls when a control fails.

Route order matters: the literal paths (`/summary`, `/facets`, `/ingest`) are
declared before `/{case_id}`, otherwise FastAPI would match them as an id.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import attachments, case_service, schemas
from app.case_service import CaseFilters
from app.db import get_db

router = APIRouter(prefix="/api/cases", tags=["cases"])

# Repeated across the list/summary/export endpoints — declared once so the three
# always accept exactly the same filter surface.
def filter_params(
    q: str | None = Query(None, description="Free text over reference, title, rule, batch, owner"),
    assurance: list[str] | None = Query(None, description="Assurance code or name, repeatable"),
    group: list[str] | None = Query(None, description="Assurance group, e.g. Revenue Assurance"),
    module: list[str] | None = Query(None, description="Sub-module / entity scope"),
    category: list[str] | None = Query(None, description="Issue type (rule primitive category)"),
    status: list[str] | None = Query(None),
    severity: list[str] | None = Query(None),
    origin: list[str] | None = Query(None),
    action: list[str] | None = Query(None),
    owner: list[str] | None = Query(None),
    rule_id: list[str] | None = Query(None, alias="ruleId"),
    stream: list[str] | None = Query(None),
    assignment: str = Query("all", pattern="^(all|assigned|unassigned|mine)$"),
    me: str | None = Query(None, description="Current user, required by assignment=mine"),
    date_from: datetime | None = Query(None, alias="dateFrom"),
    date_to: datetime | None = Query(None, alias="dateTo"),
    date_field: str = Query("createdAt", alias="dateField",
                            pattern="^(createdAt|detectedAt|updatedAt)$"),
    open_only: bool = Query(False, alias="openOnly"),
) -> CaseFilters:
    return CaseFilters(
        q=q,
        assurance=assurance or [],
        group=group or [],
        module=module or [],
        category=category or [],
        status=status or [],
        severity=severity or [],
        origin=origin or [],
        action=action or [],
        owner=owner or [],
        rule_id=rule_id or [],
        stream=stream or [],
        assignment=assignment,
        me=me,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        open_only=open_only,
    )


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

@router.get("", response_model=schemas.CaseListResponse)
def list_cases(
    filters: CaseFilters = Depends(filter_params),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500, alias="pageSize"),
    sort_by: str = Query("createdAt", alias="sortBy"),
    sort_dir: str = Query("desc", alias="sortDir", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> schemas.CaseListResponse:
    """Filtered, sorted, paginated case list."""
    return case_service.list_cases(db, filters, page, page_size, sort_by, sort_dir)


@router.get("/summary", response_model=schemas.SummaryResponse)
def summary(
    filters: CaseFilters = Depends(filter_params),
    db: Session = Depends(get_db),
) -> schemas.SummaryResponse:
    """Tile counts for the current filter scope."""
    return case_service.summarise(db, filters)


@router.get("/facets", response_model=schemas.FacetsResponse)
def facets(db: Session = Depends(get_db)) -> schemas.FacetsResponse:
    """Filter dropdown options with counts, derived from existing cases."""
    return case_service.facets(db)


@router.get("/export.csv")
def export_csv(
    filters: CaseFilters = Depends(filter_params),
    sort_by: str = Query("createdAt", alias="sortBy"),
    sort_dir: str = Query("desc", alias="sortDir", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> Response:
    """The filtered set as CSV — same filters as the list, no page cap."""
    result = case_service.list_cases(db, filters, page=1, page_size=500, sort_by=sort_by, sort_dir=sort_dir)
    columns = [
        ("reference", "Case"), ("created_at", "Created"), ("assurance_name", "Assurance"),
        ("module", "Module"), ("rule_id", "Rule"), ("rule_name", "Rule name"),
        ("rule_category", "Issue type"), ("title", "Title"), ("status", "Status"),
        ("severity", "Priority"), ("action", "Action"), ("owner", "Assigned to"),
        ("expected_value", "Expected"), ("actual_value", "Actual"), ("variance", "Variance"),
        ("affected_count", "Affected records"), ("estimated_impact", "Impact"),
        ("linked_batch", "Batch"), ("origin", "Origin"),
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in columns])
    for row in result.items:
        writer.writerow([
            # Timestamps go out in the same UTC ISO form as the JSON API, so a
            # spreadsheet and the UI never disagree about when a case was cut.
            "" if (v := getattr(row, key)) is None else (schemas.to_iso(v) if isinstance(v, datetime) else v)
            for key, _ in columns
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cases.csv"'},
    )


# --------------------------------------------------------------------------
# Writes — rule engine
# --------------------------------------------------------------------------

@router.post("/ingest", response_model=schemas.CaseDetail, status_code=201)
def ingest(
    payload: schemas.RuleCaseIngest,
    response: Response,
    db: Session = Depends(get_db),
) -> schemas.CaseDetail:
    """Raise a case from a control run.

    Send `dedupeKey` (e.g. `UA001:2026-07-30:NODE-2`) to make retries safe — a
    repeat post returns the case that already exists instead of a duplicate.
    201 means a new case was cut, 200 that one already existed.
    """
    case, created = case_service.ingest_rule_case(db, payload)
    if not created:
        response.status_code = 200
    return case_service.get_detail(db, case.id)


@router.post("/ingest/bulk", response_model=schemas.BulkIngestResponse, status_code=201)
def ingest_bulk(
    payload: list[schemas.RuleCaseIngest] = Body(...),
    db: Session = Depends(get_db),
) -> schemas.BulkIngestResponse:
    """Raise many cases from one rule-engine cycle."""
    created: list[schemas.CaseRow] = []
    duplicates: list[schemas.CaseRow] = []
    for item in payload:
        case, was_created = case_service.ingest_rule_case(db, item)
        row = schemas.CaseRow.model_validate(case)
        row.mismatch_count = len(case.mismatches)
        (created if was_created else duplicates).append(row)
    return schemas.BulkIngestResponse(
        created=created, duplicates=duplicates,
        created_count=len(created), duplicate_count=len(duplicates),
    )


# --------------------------------------------------------------------------
# Writes — analyst
# --------------------------------------------------------------------------

@router.post("", response_model=schemas.CaseDetail, status_code=201)
def create_case(payload: schemas.CaseCreate, db: Session = Depends(get_db)) -> schemas.CaseDetail:
    """Raise a case manually (the Add Case dialog)."""
    case = case_service.create_case(
        db, payload, origin=payload.origin, reference=payload.reference,
        actor=payload.created_by or payload.owner or "analyst",
    )
    return case_service.get_detail(db, case.id)


@router.get("/{case_id}", response_model=schemas.CaseDetail)
def get_case(case_id: str, db: Session = Depends(get_db)) -> schemas.CaseDetail:
    """One case with its mismatch rows, notes and audit trail. Accepts the
    uuid or the CASE-#### reference."""
    return case_service.get_detail(db, case_id)


@router.patch("/{case_id}", response_model=schemas.CaseDetail)
def update_case(
    case_id: str, payload: schemas.CaseUpdate, db: Session = Depends(get_db)
) -> schemas.CaseDetail:
    """Partial update; every tracked change is written to the audit trail."""
    return case_service.update_case(db, case_id, payload)


@router.post("/{case_id}/assign", response_model=schemas.CaseDetail)
def assign_case(
    case_id: str, payload: schemas.AssignIn, db: Session = Depends(get_db)
) -> schemas.CaseDetail:
    """Assign (or, with an empty owner, unassign) a case."""
    return case_service.assign_case(db, case_id, payload)


@router.post("/{case_id}/status", response_model=schemas.CaseDetail)
def set_status(
    case_id: str, payload: schemas.StatusIn, db: Session = Depends(get_db)
) -> schemas.CaseDetail:
    """Move a case through its lifecycle, optionally with a closing note."""
    return case_service.set_status(db, case_id, payload)


@router.post("/{case_id}/comments", response_model=schemas.CommentOut, status_code=201)
def add_comment(
    case_id: str, payload: schemas.CommentIn, db: Session = Depends(get_db)
) -> schemas.CommentOut:
    """Add an investigation note."""
    return case_service.add_comment(db, case_id, payload)


@router.post("/{case_id}/insights", response_model=schemas.CaseDetail, status_code=201)
def add_insight(
    case_id: str, payload: schemas.InsightIn, db: Session = Depends(get_db)
) -> schemas.CaseDetail:
    """Pin an assistant reply to the case."""
    return case_service.add_insight(db, case_id, payload)


@router.get("/{case_id}/mismatches")
def list_mismatches(
    case_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000, alias="pageSize"),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """The mismatch evidence behind a case, paginated."""
    return case_service.list_mismatches(db, case_id, page, page_size, status)


@router.post("/{case_id}/mismatches", response_model=schemas.CaseDetail, status_code=201)
def add_mismatches(
    case_id: str,
    payload: list[schemas.MismatchIn] = Body(...),
    db: Session = Depends(get_db),
) -> schemas.CaseDetail:
    """Attach further mismatch rows to an existing case."""
    return case_service.add_mismatches(db, case_id, payload)


@router.delete("/{case_id}", response_model=schemas.OkResponse)
def delete_case(case_id: str, db: Session = Depends(get_db)) -> schemas.OkResponse:
    """Delete a case and everything hanging off it, including its files."""
    case_service.delete_case(db, case_id)
    return schemas.OkResponse(message=f"Case {case_id} deleted")


# --------------------------------------------------------------------------
# Attachments (PDF evidence)
# --------------------------------------------------------------------------

@router.get("/{case_id}/attachments", response_model=list[schemas.AttachmentOut])
def list_attachments(case_id: str, db: Session = Depends(get_db)) -> list[schemas.AttachmentOut]:
    """Files attached to a case."""
    return case_service.list_attachments(db, case_id)


@router.post("/{case_id}/attachments", response_model=list[schemas.AttachmentOut], status_code=201)
async def upload_attachments(
    case_id: str,
    files: list[UploadFile] = File(..., description="PDF evidence"),
    uploaded_by: str = Form("", alias="uploadedBy"),
    db: Session = Depends(get_db),
) -> list[schemas.AttachmentOut]:
    """Attach PDF evidence to a case (multipart/form-data, field name `files`).

    Rejects anything that isn't a PDF — both the declared content type and the
    file's own magic bytes are checked — and anything over the size limit.
    """
    return case_service.add_attachments(db, case_id, files, uploaded_by)


@router.get("/{case_id}/attachments/{attachment_id}/download")
def download_attachment(
    case_id: str, attachment_id: str, db: Session = Depends(get_db)
) -> FileResponse:
    """Stream one attachment back with its original filename."""
    case = case_service.get_case_or_404(db, case_id)
    attachment = attachments.get_or_404(db, case.id, attachment_id)
    return FileResponse(
        attachments.resolve_path(attachment),
        media_type=attachment.content_type,
        filename=attachment.filename,
        headers={"X-Checksum-SHA256": attachment.checksum_sha256},
    )


@router.delete("/{case_id}/attachments/{attachment_id}", response_model=schemas.OkResponse)
def delete_attachment(
    case_id: str,
    attachment_id: str,
    actor: str = Query("system"),
    db: Session = Depends(get_db),
) -> schemas.OkResponse:
    """Remove an attachment and its stored file."""
    case_service.remove_attachment(db, case_id, attachment_id, actor)
    return schemas.OkResponse(message="Attachment deleted")
