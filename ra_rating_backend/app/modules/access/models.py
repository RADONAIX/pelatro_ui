"""Rating permission matrix, keyed by the existing role slug.

One row per role. Absent rows fall back to ``rbac.DEFAULT_ROLE_PERMISSIONS``,
so the service is fully functional before this table is ever populated — the
seed writes the defaults in for visibility/editability, not for correctness.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class RatingRolePermission(Base, TimestampMixin):
    __tablename__ = "role_permissions"

    # Mirrors administration.roles.id. NOT a foreign key: that table lives in
    # another schema owned by another service, and a cross-schema FK would let a
    # rating-side constraint block a role change over there.
    role_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: {ratingPermKey: {"view": bool, "edit": bool}}
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
