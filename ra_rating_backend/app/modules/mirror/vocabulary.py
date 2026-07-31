"""Seed the target's lookup tables with the target's OWN reference vocabulary.

Earlier revisions projected the *platform's* registries into these tables. That
was wrong twice over: it clobbered the operator's reference rows (their handler
names, their descriptions), and it made mirrored rules speak platform codes
(``EQUALS``, ``duration_seconds``) inside a schema whose own sample data says
``EQ`` and ``duration_sec``. The reference rows now live in
:mod:`targetvocab`, verbatim from the operator's specification, and every
mirrored value is translated into them.

One class of addition on top of the verbatim rows: **supplemental attributes**.
Platform attributes with no target equivalent — ``tariff_plan`` above all, the
single most common condition the rule wizard writes — pass through under their
own names, so those names must exist in `rule_attribute` or the condition FK
breaks. They are appended as extra ACTIVE rows with ``source_entity = NULL``,
which keeps them visibly foreign next to the operator's own nineteen.

Still additive, still idempotent, still never deletes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.mirror import tables, targetvocab
from app.modules.rules.constants import DataType
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTES

log = get_logger("mirror.vocabulary")

#: Platform DataType → the target's value-type vocabulary, for supplemental
#: (pass-through) attributes only; mapped attributes carry the type declared in
#: the operator's own table.
_PASSTHROUGH_TYPES: dict[str, str] = {
    DataType.STRING: "STRING",
    DataType.NUMBER: "DECIMAL",
    DataType.BOOLEAN: "BOOLEAN",
    DataType.ENUM: "STRING",
    DataType.REFERENCE: "STRING",
    DataType.DATETIME: "TIMESTAMP",
}


def passthrough_type(platform_data_type: str) -> str:
    return _PASSTHROUGH_TYPES.get(platform_data_type, "STRING")


def attribute_rows() -> list[dict[str, Any]]:
    """The operator's nineteen rows verbatim, then the supplemental pass-throughs."""
    rows = [
        {
            "attribute_name": name,
            "attribute_description": description,
            "data_type": data_type,
            "source_entity": source_entity,
            "is_runtime_attribute": True,
            "status": "ACTIVE",
        }
        for name, description, data_type, source_entity in targetvocab.TARGET_ATTRIBUTE_ROWS
    ]
    mapped_keys = set(targetvocab.ATTRIBUTE_MAP)
    for attr in CANONICAL_ATTRIBUTES:
        if attr.key in mapped_keys or attr.key in targetvocab.TARGET_ATTRIBUTE_NAMES:
            continue
        rows.append(
            {
                "attribute_name": attr.key[:100],
                "attribute_description": (attr.description or attr.label)[:500],
                "data_type": passthrough_type(attr.data_type),
                # NULL marks the row as a platform pass-through rather than one
                # of the operator's own entity-sourced attributes.
                "source_entity": None,
                "is_runtime_attribute": True,
                "status": "ACTIVE",
            }
        )
    return rows


def operator_rows() -> list[dict[str, Any]]:
    return [
        {
            "operator_code": code,
            "operator_name": name,
            "description": description,
            "supported_data_types": supported,
            "status": "ACTIVE",
        }
        for code, name, description, supported in targetvocab.OPERATOR_ROWS
    ]


def action_type_rows() -> list[dict[str, Any]]:
    return [
        {
            "action_type": action_type,
            "action_name": action_name,
            "rule_stage": rule_stage,
            "handler_name": handler_name,
            "description": description,
            "status": "ACTIVE",
        }
        for action_type, action_name, rule_stage, handler_name, description
        in targetvocab.ACTION_TYPE_ROWS
    ]


async def _upsert(db: AsyncSession, table: Any, rows: list[dict[str, Any]], key: str) -> int:
    if not rows:
        return 0
    stmt = insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[key],
        set_={c: stmt.excluded[c] for c in rows[0] if c != key},
    )
    await db.execute(stmt)
    return len(rows)


async def reconcile(db: AsyncSession) -> dict[str, int]:
    """Upsert all three vocabulary tables. Idempotent."""
    counts = {
        "rule_attribute": await _upsert(
            db, tables.rule_attribute, attribute_rows(), "attribute_name"
        ),
        "rule_operator": await _upsert(
            db, tables.rule_operator, operator_rows(), "operator_code"
        ),
        "rule_action_type": await _upsert(
            db, tables.rule_action_type, action_type_rows(), "action_type"
        ),
    }
    return counts


# --- Self-healing ------------------------------------------------------------
#
# Reconciling only at startup is not enough, and the failure it produces is
# lasting rather than transient: if the target's lookup tables are truncated, or
# the mirror database is recreated, or the service happened to start before the
# mirror was reachable, then EVERY subsequent rule write fails on
# `fk_action_type` / `fk_condition_attribute` and keeps failing until somebody
# restarts the process. A mirror that cannot recover on its own is one that
# quietly stops mirroring.

_reconciled = False


def invalidate() -> None:
    """Forget that the vocabulary was reconciled, so the next write re-seeds it."""
    global _reconciled
    _reconciled = False


async def ensure(db: AsyncSession) -> bool:
    """Reconcile once per process, or again after :func:`invalidate`.

    Returns whether it actually did the work, so a caller can decide whether a
    retry is worth attempting.
    """
    global _reconciled
    if _reconciled:
        return False
    counts = await reconcile(db)
    _reconciled = True
    log.info("mirror_vocabulary_ensured", **counts)
    return True
