"""Shared conventions for the canonical rule model.

Two deviations from the plan's illustrative DDL, both deliberate:

**``String(36)`` UUIDs, not native ``uuid``.** Every existing primary key in this
service is a 36-character UUID string, and the canonical tables carry foreign
keys into ``rating.products`` / ``offers`` / ``tariff_plans`` / ``source_systems``.
A native ``uuid`` column cannot reference a ``varchar(36)`` one, so matching the
incumbent type is the only option that keeps those FKs real rather than advisory.

**Explicit constraint names.** The migrations here are hand-written, so a
constraint whose name Alembic and SQLAlchemy each derive independently is a
constraint that eventually cannot be dropped. Everything is named at the point of
declaration.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings

#: Schema holding the canonical rule model. Distinct from `rating` (the execution
#: plane) so rule metadata and CDR volume can be granted and backed up apart.
RULE_SCHEMA: str = settings.rule_db_schema


def new_id() -> str:
    return str(uuid.uuid4())


def _default_tenant() -> str:
    """The tenant the current request or job is acting for.

    Imported lazily because the tenancy models are themselves declared against
    this module — a module-level import would close the loop at class-definition
    time.

    Before M8 this returned ``settings.default_tenant_id`` unconditionally, which
    was correct while one tenant existed and silently wrong the instant a second
    one did: a row written for tenant B would be stamped A, and then be invisible
    to the people who had just created it.
    """
    from app.modules.tenancy.context import current_tenant

    return current_tenant()


class TenantMixin:
    """Every canonical row is tenant-scoped.

    Present from R1 even though only one tenant existed, because retrofitting a
    tenant column onto a populated 40,000-rule estate means rewriting every
    unique constraint and every index — the column was cheap then and expensive
    later. M8 turns it from an honest column into an enforced boundary.

    The server default stays the deployment's default tenant. That is what makes
    a row inserted by raw SQL — a migration, a repair script, psql — land
    somewhere sane rather than failing a NOT NULL, and it costs nothing because
    every path that matters supplies the value explicitly.
    """

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        default=_default_tenant,
        server_default=settings.default_tenant_id,
        nullable=False,
        index=True,
    )
