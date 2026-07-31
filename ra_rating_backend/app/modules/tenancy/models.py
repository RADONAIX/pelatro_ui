"""The tenant registry and who belongs to it.

Two tables, and one of them is deliberately not tenant-scoped.

``tenant`` is the registry itself. It cannot carry ``tenant_id`` and cannot be
under row-level security, because resolving *which* tenant a request belongs to
has to happen before any tenant scope exists — a registry you can only read once
you know your tenant is a registry you can never read.

``tenant_membership`` maps a user to the tenants they may act in. It is the piece
that would normally be a claim in the token; it lives here because the token is
issued by a service that does not know tenants exist, and waiting for that to
change would mean waiting to be multi-tenant at all.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, new_id


class Tenant(Base, TimestampMixin):
    """One operator. The root of every scope in the canonical model.

    Not itself tenant-scoped, and not under RLS: this is the table a request
    consults to discover its own scope, so putting it behind that scope would be
    circular. It holds no operator data — a code, a name and a status — so the
    exposure is the existence of other tenants' names, which every deployment
    already knows from its own configuration.
    """

    __tablename__ = "tenant"
    __table_args__ = (
        UniqueConstraint("code", name="uq_tenant_code"),
        {"schema": RULE_SCHEMA},
    )

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    #: ACTIVE | SUSPENDED. A suspended tenant's data stays where it is and stops
    #: being reachable — the opposite of deleting it, which an assurance platform
    #: under a retention obligation may not do.
    status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE", server_default="ACTIVE", nullable=False, index=True
    )
    #: Per-tenant overrides — default currency, timezone, locale. Deliberately
    #: JSONB: these differ per operator and adding a column for each would put a
    #: migration between us and onboarding.
    settings: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )


class TenantMembership(Base, TimestampMixin):
    """Which tenants a user may act in.

    A user can belong to several — an analyst at a group operator works across
    subsidiaries — so the *active* tenant for a request is the default one unless
    the caller asks for another they are entitled to. Asking for one they are not
    entitled to is a 403, not an empty result: an empty result reads as "there is
    no data", which is a different and more alarming thing.
    """

    __tablename__ = "tenant_membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),
        Index("ix_tenant_membership_user", "user_id"),
        {"schema": RULE_SCHEMA},
    )

    membership_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.tenant.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The `administration.users` id. No foreign key: that schema is read-only to
    #: this service and lives behind a different connection, so the constraint
    #: could not be enforced even if we were allowed to declare it.
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: Which tenant this user lands in when they do not name one.
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
