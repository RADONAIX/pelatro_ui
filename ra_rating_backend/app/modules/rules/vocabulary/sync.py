"""Reconcile the Python vocabulary into the ``ra_rule`` lookup tables.

Idempotent and additive. Run on deploy (and from ``app.seed``), so adding a stage
or a rule type is a code change plus a restart rather than a migration — while the
tables stay joinable, per-tenant extensible and reportable.

**What this deliberately does not do:** delete. A stage or type that disappears
from the registry is left in place, because rows elsewhere reference it and
"tidying up" a lookup row that 4,000 rule versions point at is how a rule estate
loses its meaning. Removal is a considered migration, not a side effect of a
rename in a Python file.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.rules.canonical.lookups import (
    RuleStackingPolicyRow,
    RuleStage,
    RuleTypeRow,
)
from app.modules.rules.vocabulary.policies import CANONICAL_STACKING_POLICIES
from app.modules.rules.vocabulary.stages import CANONICAL_STAGES
from app.modules.rules.vocabulary.types import CANONICAL_RULE_TYPES


async def sync_vocabulary(
    db: AsyncSession, *, tenant_id: str | None = None
) -> dict[str, dict[str, int]]:
    """Upsert stages, rule types and stacking policies. Returns per-table counts."""
    tenant = tenant_id or settings.default_tenant_id

    stages = await _sync_stages(db, tenant)
    policies = await _sync_policies(db, tenant)
    # Types are last: each one resolves its stage by code, so the stages must exist.
    await db.flush()
    types = await _sync_types(db, tenant)

    await db.flush()
    return {"rule_stage": stages, "rule_stacking_policy": policies, "rule_type": types}


async def _existing(db: AsyncSession, model: Any, tenant: str, key: str) -> dict[str, Any]:
    rows = (
        await db.execute(select(model).where(model.tenant_id == tenant))
    ).scalars().all()
    return {getattr(r, key): r for r in rows}


async def _sync_stages(db: AsyncSession, tenant: str) -> dict[str, int]:
    existing = await _existing(db, RuleStage, tenant, "code")
    created = updated = 0
    for spec in CANONICAL_STAGES:
        row = existing.get(spec.code)
        if row is None:
            db.add(
                RuleStage(
                    tenant_id=tenant,
                    code=spec.code,
                    name=spec.name,
                    applies_to=spec.applies_to,
                    execution_order=spec.execution_order,
                    is_stateful=spec.is_stateful,
                    description=spec.description,
                )
            )
            created += 1
            continue
        changed = False
        for field, value in (
            ("name", spec.name),
            ("applies_to", spec.applies_to),
            ("execution_order", spec.execution_order),
            ("is_stateful", spec.is_stateful),
            ("description", spec.description),
        ):
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        updated += int(changed)
    return {"created": created, "updated": updated}


async def _sync_policies(db: AsyncSession, tenant: str) -> dict[str, int]:
    existing = await _existing(db, RuleStackingPolicyRow, tenant, "code")
    created = updated = 0
    for spec in CANONICAL_STACKING_POLICIES:
        row = existing.get(spec.code)
        if row is None:
            db.add(
                RuleStackingPolicyRow(
                    tenant_id=tenant,
                    code=spec.code,
                    name=spec.name,
                    allows_multiple=spec.allows_multiple,
                    overrides_lower=spec.overrides_lower,
                    description=spec.description,
                )
            )
            created += 1
            continue
        changed = False
        for field, value in (
            ("name", spec.name),
            ("allows_multiple", spec.allows_multiple),
            ("overrides_lower", spec.overrides_lower),
            ("description", spec.description),
        ):
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        updated += int(changed)
    return {"created": created, "updated": updated}


async def _sync_types(db: AsyncSession, tenant: str) -> dict[str, int]:
    stage_ids = {
        code: row.rule_stage_id
        for code, row in (await _existing(db, RuleStage, tenant, "code")).items()
    }
    existing = await _existing(db, RuleTypeRow, tenant, "code")
    created = updated = 0
    for spec in CANONICAL_RULE_TYPES:
        stage_id = stage_ids.get(spec.stage_code)
        if stage_id is None:
            # Cannot happen once _sync_stages has flushed; a loud failure beats a
            # rule type silently pointing at nothing.
            raise RuntimeError(
                f"Rule type '{spec.code}' names stage '{spec.stage_code}', which is "
                "not in the stage registry."
            )
        row = existing.get(spec.code)
        if row is None:
            db.add(
                RuleTypeRow(
                    tenant_id=tenant,
                    code=spec.code,
                    name=spec.name,
                    rule_category=spec.rule_category,
                    rule_stage_id=stage_id,
                    charging_mode=spec.charging_mode,
                    required_action_types=list(spec.required_action_types),
                    alias_of=spec.alias_of,
                    is_system=True,
                    description=spec.description,
                )
            )
            created += 1
            continue
        changed = False
        for field, value in (
            ("name", spec.name),
            ("rule_category", spec.rule_category),
            ("rule_stage_id", stage_id),
            ("charging_mode", spec.charging_mode),
            ("required_action_types", list(spec.required_action_types)),
            ("alias_of", spec.alias_of),
            ("description", spec.description),
        ):
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        updated += int(changed)
    return {"created": created, "updated": updated}
