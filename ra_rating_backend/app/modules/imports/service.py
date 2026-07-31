"""Import orchestration: parse → map → validate → commit.

The preview and the commit run the *same* pipeline; preview simply stops before
writing. That is deliberate — an import that previews clean and then fails on
commit is the fastest way to lose an operations team's trust.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.imports import parser
from app.modules.imports.constants import (
    ALL_COLUMNS,
    ImportStatus,
    RowStatus,
    suggest_mapping,
)
from app.modules.imports.mapper import RowError, map_row
from app.modules.imports.models import RuleImportBatch, RuleImportRow
from app.modules.rules import service as rule_svc
from app.modules.rules import validation as rule_validation
from app.modules.rules.models import Rule, RuleAction, RuleCondition
from app.modules.rules.schemas import RuleCreate

#: Columns without which a row cannot become a rule.
REQUIRED_COLUMNS = tuple(c.key for c in ALL_COLUMNS if c.required)

PREVIEW_ROW_LIMIT = 200


class MappedRow:
    """One row's journey through the pipeline, carried in memory."""

    __slots__ = ("canonical", "errors", "raw", "row_number", "status")

    def __init__(self, row_number: int, raw: dict[str, str]):
        self.row_number = row_number
        self.raw = raw
        self.canonical: dict[str, Any] | None = None
        self.status: str = RowStatus.VALID.value
        self.errors: list[dict[str, str]] = []

    def reject(self, message: str, column: str = "") -> None:
        self.status = RowStatus.REJECTED.value
        self.errors.append({"row_number": self.row_number, "column": column, "message": message})

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "raw": self.raw,
            "canonical": self.canonical,
            "status": self.status,
            "errors": self.errors,
        }


async def run_pipeline(
    db: AsyncSession,
    *,
    filename: str,
    data: bytes,
    mapping: dict[str, str | None] | None,
    default_effective_from: date | None = None,
    source_system: str = "FILE_IMPORT",
) -> tuple[list[str], dict[str, str | None], list[MappedRow]]:
    """Parse, map and validate every row. Writes nothing."""
    headings, raw_rows = parser.parse(filename, data)
    resolved = mapping or suggest_mapping(headings)
    # A saved template may not mention a heading this file has; keep it visible
    # as unmapped rather than dropping it silently.
    for heading in headings:
        resolved.setdefault(heading, None)

    effective_from = default_effective_from or date.today()
    rows: list[MappedRow] = []

    for index, raw in enumerate(raw_rows):
        # +2: one for the header line, one because spreadsheets are 1-based.
        mapped = MappedRow(index + 2, raw)
        try:
            mapped.canonical = map_row(
                raw,
                resolved,
                default_effective_from=effective_from,
                source_system=source_system,
            )
        except RowError as exc:
            mapped.reject(exc.message, exc.column)
            rows.append(mapped)
            continue
        except Exception as exc:
            mapped.reject(f"Could not read this row: {exc}")
            rows.append(mapped)
            continue

        # Structural validation against the real rule model + catalogue, so the
        # preview surfaces "zone LOCAL_XYZ does not exist" before anything is
        # written — the same check the authoring UI runs.
        issues = await _validate_canonical(db, mapped.canonical)
        for issue in issues:
            mapped.reject(issue["message"], issue.get("column", ""))
        rows.append(mapped)

    return headings, resolved, rows


async def _validate_canonical(db: AsyncSession, canonical: dict[str, Any]) -> list[dict[str, str]]:
    """Run the structural validator over a detached, unsaved rule."""
    try:
        payload = RuleCreate(**canonical)
    except Exception as exc:
        return [{"message": _first_pydantic_message(exc)}]

    probe = Rule(
        rule_key=payload.rule_key or rule_svc.derive_rule_key(payload.name),
        name=payload.name,
        description=payload.description,
        rule_type=str(payload.rule_type),
        execution_stage=rule_svc.RULE_TYPE_STAGE[payload.rule_type],
        service_type=str(payload.service_type),
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        currency_code=payload.currency_code,
    )
    probe.conditions = [
        RuleCondition(
            sequence=i, group_index=c.group_index, attribute=c.attribute,
            operator=str(c.operator), values=list(c.values or []), negate=c.negate,
        )
        for i, c in enumerate(payload.conditions)
    ]
    probe.actions = [
        RuleAction(sequence=i, action_type=str(a.action_type), params=dict(a.params or {}))
        for i, a in enumerate(payload.actions)
    ]

    report = await rule_validation.validate_rule(db, probe)
    return [
        {"message": i.message, "column": i.path}
        for i in report.issues
        if i.severity == "ERROR"
    ]


def _first_pydantic_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            loc = ".".join(str(p) for p in first.get("loc", ()))
            return f"{loc}: {first.get('msg', 'invalid value')}" if loc else first.get("msg", "")
        except (IndexError, KeyError, TypeError):
            pass
    return str(exc)


async def commit(
    db: AsyncSession,
    *,
    filename: str,
    headings: list[str],
    mapping: dict[str, str | None],
    rows: list[MappedRow],
    rule_set_id: str | None,
    source_system: str,
    actor_id: str,
    actor_name: str,
) -> RuleImportBatch:
    """Persist the batch and create a rule for every valid row.

    Valid rows import even when others failed — a 4000-row tariff sheet with
    three bad cells should land 3997 rules, not zero. The rejects are kept and
    downloadable so the source team can fix and re-send only those.
    """
    batch = RuleImportBatch(
        filename=filename,
        source_system=source_system,
        rule_set_id=rule_set_id,
        mapping=mapping,
        total_rows=len(rows),
        created_by=actor_id,
        created_by_name=actor_name,
        status=ImportStatus.PENDING.value,
    )
    db.add(batch)
    await db.flush()

    imported = 0
    rejected = 0
    # Keys claimed earlier in THIS file: two rows deriving the same key would
    # otherwise fail on the unique constraint halfway through the batch.
    claimed: set[str] = set()

    for row in rows:
        if row.status == RowStatus.REJECTED.value or row.canonical is None:
            rejected += 1
            db.add(RuleImportRow(batch_id=batch.id, **row.as_dict()))
            continue

        payload = RuleCreate(**row.canonical)
        key = payload.rule_key or rule_svc.derive_rule_key(payload.name)
        if key in claimed:
            row.reject(f"Duplicate rule key '{key}' — an earlier row in this file uses it.")
            rejected += 1
            db.add(RuleImportRow(batch_id=batch.id, **row.as_dict()))
            continue
        if await rule_svc.latest_version(db, key) is not None:
            row.reject(
                f"Rule key '{key}' already exists. Create a new version of it instead."
            )
            rejected += 1
            db.add(RuleImportRow(batch_id=batch.id, **row.as_dict()))
            continue

        payload.rule_key = key
        if rule_set_id:
            payload.rule_set_id = rule_set_id
        try:
            rule = await rule_svc.create_rule(
                db, payload, actor_id=actor_id, actor_name=actor_name
            )
        except Exception as exc:
            row.reject(f"Could not create the rule: {exc}")
            rejected += 1
            db.add(RuleImportRow(batch_id=batch.id, **row.as_dict()))
            continue

        claimed.add(key)
        imported += 1
        record = row.as_dict()
        record["status"] = RowStatus.IMPORTED.value
        db.add(RuleImportRow(batch_id=batch.id, rule_id=rule.id, **record))

    batch.imported_rows = imported
    batch.rejected_rows = rejected
    batch.valid_rows = imported
    batch.completed_at = datetime.now(UTC)
    batch.status = (
        ImportStatus.COMPLETED.value
        if rejected == 0
        else ImportStatus.FAILED.value
        if imported == 0
        else ImportStatus.PARTIAL.value
    )
    batch.summary = {
        "imported": imported,
        "rejected": rejected,
        "total": len(rows),
        # Kept because a JSONB object does not preserve key order: without this
        # the rejected-rows export cannot reproduce the source file's columns.
        "headings": headings,
        # Grouped reasons: 400 rows failing for one reason is one fix, and the
        # operations team needs to see that immediately.
        "reasons": _group_reasons(rows),
    }
    await db.flush()
    await db.refresh(batch)
    return batch


def _group_reasons(rows: list[MappedRow]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        for err in row.errors:
            counts[err["message"]] = counts.get(err["message"], 0) + 1
    return [
        {"message": m, "count": c}
        for m, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ][:20]


def rejected_csv(rows: list[RuleImportRow], headings: list[str] | None = None) -> str:
    """Rebuild the rejected rows as CSV, with the reason appended.

    Same columns, in the same order, as the source file — so the sending team
    can correct in place and re-upload rather than reverse-engineering our
    canonical format. Falls back to key order for batches recorded before the
    heading order was captured.
    """
    headings = list(headings or [])
    for row in rows:
        for key in row.raw:
            if key not in headings:
                headings.append(key)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([*headings, "_row_number", "_rejection_reason"])
    for row in rows:
        reason = "; ".join(e.get("message", "") for e in (row.errors or []))
        writer.writerow(
            [*(row.raw.get(h, "") for h in headings), row.row_number, reason]
        )
    return buffer.getvalue()
