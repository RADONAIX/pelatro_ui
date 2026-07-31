"""Case attachment storage.

Bytes go to disk under `settings.uploads_dir`; only metadata goes to the
database. Two rules make this safe to expose:

* the stored path is a uuid the server generates — the client's filename is
  display metadata and never touches the filesystem, so `../../etc/passwd` is
  simply a label;
* the content type is verified against the bytes (`%PDF-`), not just the
  declared header, so a renamed executable is rejected rather than stored and
  later handed back to a browser.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.config import settings
from app.models import CaseAttachment, utcnow

logger = logging.getLogger("ra.attachments")

UPLOAD_ROOT = Path(settings.uploads_dir)
MAX_BYTES = settings.max_upload_mb * 1024 * 1024
# Read in chunks so a large upload never lands in memory whole.
CHUNK = 1024 * 1024

# Magic bytes per accepted type. A PDF must start with "%PDF-".
MAGIC: dict[str, bytes] = {"application/pdf": b"%PDF-"}

EXTENSIONS: dict[str, str] = {"application/pdf": ".pdf"}


def _safe_display_name(name: str | None) -> str:
    """Keep the leaf of whatever the client sent, for display only."""
    leaf = Path(name or "").name.strip() or "attachment.pdf"
    return leaf[:255]


def _dated_dir() -> Path:
    """Bucket by month so one directory never accumulates every upload."""
    stamp: datetime = utcnow()
    return UPLOAD_ROOT / f"{stamp:%Y-%m}"


def _download_url(attachment: CaseAttachment) -> str:
    return f"/api/cases/{attachment.case_id}/attachments/{attachment.id}/download"


def to_out(attachment: CaseAttachment) -> schemas.AttachmentOut:
    out = schemas.AttachmentOut.model_validate(attachment)
    out.download_url = _download_url(attachment)
    return out


def save_upload(
    db: Session, case_id: str, upload: UploadFile, uploaded_by: str = ""
) -> CaseAttachment:
    """Validate and persist one uploaded file against a case."""
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    if declared not in settings.allowed_upload_types:
        allowed = ", ".join(settings.allowed_upload_types)
        raise HTTPException(415, f"Unsupported file type '{declared or 'unknown'}'. Allowed: {allowed}")

    display_name = _safe_display_name(upload.filename)
    expected_ext = EXTENSIONS.get(declared, "")
    if expected_ext and not display_name.lower().endswith(expected_ext):
        display_name += expected_ext

    target_dir = _dated_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{expected_ext}"
    target = target_dir / stored_name

    digest = hashlib.sha256()
    size = 0
    magic = MAGIC.get(declared)
    try:
        with target.open("wb") as sink:
            while chunk := upload.file.read(CHUNK):
                if size == 0 and magic and not chunk.startswith(magic):
                    raise HTTPException(
                        400,
                        f"That file is not a valid {declared} — its contents do not match "
                        "the declared type.",
                    )
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(
                        413, f"File is larger than the {settings.max_upload_mb} MB limit."
                    )
                digest.update(chunk)
                sink.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)   # never leave a rejected file behind
        raise
    except OSError:
        target.unlink(missing_ok=True)
        logger.exception("failed to write attachment for case %s", case_id)
        raise HTTPException(500, "Could not store the file.") from None
    finally:
        upload.file.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty.")

    attachment = CaseAttachment(
        case_id=case_id,
        filename=display_name,
        content_type=declared,
        size_bytes=size,
        storage_path=str(target.relative_to(UPLOAD_ROOT)),
        checksum_sha256=digest.hexdigest(),
        uploaded_by=uploaded_by,
    )
    db.add(attachment)
    return attachment


def get_or_404(db: Session, case_id: str, attachment_id: str) -> CaseAttachment:
    attachment = db.execute(
        select(CaseAttachment).where(
            CaseAttachment.id == attachment_id, CaseAttachment.case_id == case_id
        )
    ).scalars().first()
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    return attachment


def resolve_path(attachment: CaseAttachment) -> Path:
    """Absolute path of the stored file, confined to the upload root."""
    root = UPLOAD_ROOT.resolve()
    path = (root / attachment.storage_path).resolve()
    # Defence in depth: a storage_path edited in the database can't be used to
    # read an arbitrary file off the server.
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "The stored file is no longer available.")
    return path


def delete_file(attachment: CaseAttachment) -> None:
    """Remove the bytes. The row is deleted by the caller's session."""
    try:
        (UPLOAD_ROOT / attachment.storage_path).unlink(missing_ok=True)
    except OSError:
        # An orphaned file is a cleanup problem, not a reason to fail the
        # request the user asked for.
        logger.warning("could not remove %s", attachment.storage_path, exc_info=True)


def purge_case_files(attachments: list[CaseAttachment]) -> None:
    """Delete the files behind a case that is about to be removed."""
    for attachment in attachments:
        delete_file(attachment)


def storage_usage() -> dict:
    """Total bytes held under the upload root — for an ops/health view."""
    root = UPLOAD_ROOT
    if not root.exists():
        return {"files": 0, "bytes": 0, "path": str(root.resolve())}
    files = [p for p in root.rglob("*") if p.is_file()]
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "path": str(root.resolve()),
        "freeBytes": shutil.disk_usage(root).free,
    }
