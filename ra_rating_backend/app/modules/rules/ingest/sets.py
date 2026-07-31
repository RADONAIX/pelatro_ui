"""Giving an import a name, so it can be operated on as one thing.

This is the link the bulk-lifecycle plan turns on. Without it, "approve
everything from Tuesday's Ericsson import" is a five-hundred-element array a UI
has to assemble and a caller has to be trusted with. With it, it is one id.

Everything downstream already understands rule sets. ``compile_snapshot`` scopes
a snapshot by ``rule_set_id``; a snapshot rolls back atomically; the catalogue
filters by set. So attaching an import to a set is not a feature so much as the
missing wire between three mechanisms that were each built expecting it.

``RuleSetType.VENDOR_IMPORT`` was defined in R1 with the docstring "everything
one vendor export produced, so an import is revertible as a unit". Nothing had
ever created one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.rules.canonical.sets import CanonicalRuleSet
from app.modules.rules.vocabulary.modes import RuleSetType

_SLUG = re.compile(r"[^A-Z0-9]+")

#: Matches `rule_set.code`.
MAX_CODE_LENGTH = 64


def suggest_code(*, source_code: str | None, filename: str | None, when: datetime) -> str:
    """A readable, sortable code for an import's set.

    ``ERICSSON_20260730_1412``. Time to the minute rather than the day, because
    two imports from one source on one day is normal and a collision would put
    the second one's rules in the first one's set — which is exactly the mistake
    this feature exists to make impossible.
    """
    stem = source_code or (filename or "IMPORT").rsplit(".", 1)[0]
    prefix = _SLUG.sub("_", stem.upper()).strip("_") or "IMPORT"
    stamp = when.strftime("%Y%m%d_%H%M")
    return f"{prefix}_{stamp}"[:MAX_CODE_LENGTH]


async def resolve(
    db: AsyncSession,
    *,
    tenant_id: str,
    rule_set_id: str | None,
    create: bool,
    source_system_id: str | None,
    source_code: str | None,
    filename: str | None,
    actor_id: str | None,
) -> CanonicalRuleSet | None:
    """The set this import's rules should join, if any.

    Three outcomes, and the caller chooses between them rather than the system
    guessing: join a named set, create one for this import, or neither. An
    operator importing a three-rule correction does not want a new set every
    time, and one that fires automatically would bury the sets that matter under
    a hundred that do not.
    """
    if rule_set_id:
        existing = await db.get(CanonicalRuleSet, rule_set_id)
        if existing is None:
            raise NotFoundError(f"Rule set '{rule_set_id}' was not found.")
        return existing

    if not create:
        return None

    now = datetime.now(UTC)
    code = suggest_code(source_code=source_code, filename=filename, when=now)
    code = await _unique_code(db, tenant_id, code)

    rule_set = CanonicalRuleSet(
        tenant_id=tenant_id,
        code=code,
        name=f"Import — {filename or source_code or 'file'} — {now:%d %b %Y %H:%M}",
        description=(
            "Created by a rule import. Every rule the import produced is a "
            "member, so the import can be validated, approved and rolled back "
            "as one unit."
        ),
        set_type=RuleSetType.VENDOR_IMPORT,
        source_system_id=source_system_id,
        created_by=actor_id,
    )
    db.add(rule_set)
    await db.flush()
    return rule_set


async def _unique_code(db: AsyncSession, tenant_id: str, code: str) -> str:
    """Suffix on collision rather than failing the import.

    Two imports in the same minute is unusual and entirely possible — a retry, a
    split file — and refusing the second one because its *label* clashes would be
    a poor trade for a name nobody types.
    """
    taken = set(
        (
            await db.execute(
                select(CanonicalRuleSet.code).where(
                    CanonicalRuleSet.tenant_id == tenant_id,
                    CanonicalRuleSet.code.like(f"{code}%"),
                )
            )
        )
        .scalars()
        .all()
    )
    if code not in taken:
        return code
    for n in range(2, 100):
        candidate = f"{code[: MAX_CODE_LENGTH - 4]}_{n}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"Cannot find a free rule set code based on '{code}'.")
