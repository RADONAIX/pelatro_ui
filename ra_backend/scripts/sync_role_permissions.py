"""Sync existing role permission matrices with the current RBAC defaults.

Adding a new permission key (e.g. ``exports`` for the Download Center) leaves
existing role rows in the DB without an entry for it — so ``has_permission``
returns False and even admins are denied. This script fills any MISSING
permission key on each role from ``default_permissions_for(role)`` WITHOUT
overwriting existing (possibly customised) entries. Idempotent — safe to re-run.

Run after deploying a release that introduces a new PermKey:
    python -m scripts.sync_role_permissions
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.core.rbac import default_permissions_for
from app.modules.identity.models import Role

log = get_logger("sync-perms")


async def sync() -> None:
    async with SessionFactory() as db:
        roles = (await db.execute(select(Role))).scalars().all()
        changed = 0
        for role in roles:
            stored = role.permissions or {}
            defaults = default_permissions_for(role.id)
            # defaults as base, stored wins for existing keys → fills only gaps.
            merged = {**defaults, **stored}
            added = sorted(k for k in merged if k not in stored)
            if added:
                role.permissions = merged
                changed += 1
                log.info("role_permissions_filled", role=role.id, added=added)
        if changed:
            await db.commit()
        log.info("sync_done", roles=len(roles), updated=changed)


if __name__ == "__main__":
    configure_logging(level="INFO", json_logs=False)
    asyncio.run(sync())
