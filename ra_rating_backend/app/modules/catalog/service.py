"""Generic persistence helpers for the canonical metadata entities.

All 14 entities share the same lifecycle (create with a unique code, patch,
soft-retire, list with search + status filter), so the CRUD lives here once and
each entity contributes only its column list.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.catalog.constants import CatalogStatus
from app.modules.mirror import hooks as mirror_hooks


async def get_by_id[T](db: AsyncSession, model: type[T], entity_id: str, label: str) -> T:
    obj = await db.get(model, entity_id)
    if obj is None:
        raise NotFoundError(f"{label} '{entity_id}' was not found.")
    return obj


async def get_by_code(db: AsyncSession, model: type[Any], code: str) -> Any | None:
    return (
        await db.execute(select(model).where(model.code == code))
    ).scalar_one_or_none()


async def ensure_code_free(
    db: AsyncSession, model: type[Any], code: str, label: str, *, exclude_id: str | None = None
) -> None:
    stmt = select(model.id).where(model.code == code)
    if exclude_id:
        stmt = stmt.where(model.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise ConflictError(f"{label} code '{code}' is already in use.")


def apply_filters(
    stmt: Select,
    model: type[Any],
    *,
    search: str | None,
    status: str | None,
    extra_equals: dict[str, Any] | None = None,
) -> Select:
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(model.code).like(needle),
                func.lower(model.name).like(needle),
            )
        )
    if status:
        stmt = stmt.where(model.status == status)
    for column, value in (extra_equals or {}).items():
        if value is not None and hasattr(model, column):
            stmt = stmt.where(getattr(model, column) == value)
    return stmt


async def count(db: AsyncSession, stmt: Select) -> int:
    total = await db.execute(select(func.count()).select_from(stmt.subquery()))
    return int(total.scalar_one())


async def create(
    db: AsyncSession,
    model: type[Any],
    payload: dict[str, Any],
    *,
    label: str,
    actor_id: str | None,
) -> Any:
    await ensure_code_free(db, model, payload["code"], label)
    obj = model(**payload, created_by=actor_id)
    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    mirror_hooks.record_catalog_entity(db, model, obj.id)
    return obj


async def update(
    db: AsyncSession,
    model: type[Any],
    entity_id: str,
    payload: dict[str, Any],
    *,
    label: str,
) -> Any:
    obj = await get_by_id(db, model, entity_id, label)
    if "code" in payload and payload["code"] != obj.code:
        await ensure_code_free(db, model, payload["code"], label, exclude_id=entity_id)
    for key, value in payload.items():
        setattr(obj, key, value)
    await db.flush()
    await db.refresh(obj)
    mirror_hooks.record_catalog_entity(db, model, obj.id)
    return obj


async def retire(db: AsyncSession, model: type[Any], entity_id: str, *, label: str) -> Any:
    """Soft-delete. Metadata is referenced by historical rating results, so a
    hard delete would orphan an audit trail we are contractually required to
    keep. Retired records stop matching new rules but stay resolvable."""
    obj = await get_by_id(db, model, entity_id, label)
    obj.status = CatalogStatus.RETIRED.value
    await db.flush()
    await db.refresh(obj)
    mirror_hooks.record_catalog_entity(db, model, obj.id)
    return obj
