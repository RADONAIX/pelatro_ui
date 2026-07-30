"""audit_logs.module (which module an action belongs to)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-10

Adds `module` so the audit screen can group/filter by feature. Values mirror
app.core.rbac.AuditModule (== PermKey values, plus "auth" for authentication
events). Existing rows are backfilled from their action; anything unmatched stays
NULL and renders as "—".
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# action → module. The module is the feature whose PERMISSION gates the action,
# not the router file it happens to live in (e.g. "Updated system config" is
# raised by the operations router but gated by PermKey.SETTINGS).
_BACKFILL: dict[str, tuple[str, ...]] = {
    "auth": (
        "Signed in", "Signed out", "Changed password", "Failed login",
        "Account locked", "Requested password reset", "Reset password",
    ),
    "userManagement": ("Created user", "Updated user", "Deleted user"),
    "roleManagement": ("Updated role", "Saved role", "Updated role permissions"),
    "settings": ("Updated system config",),
    "reports": ("Exported report",),
    "exports": ("Requested export",),
}


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("module", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_audit_logs_module"), "audit_logs", ["module"], unique=False)

    for module, actions in _BACKFILL.items():
        op.execute(
            sa.text("UPDATE audit_logs SET module = :m WHERE action IN :actions")
            .bindparams(sa.bindparam("actions", value=actions, expanding=True))
            .bindparams(m=module)
        )
    # SSO sign-ins are recorded as "Signed in via <Provider> SSO".
    op.execute(
        "UPDATE audit_logs SET module = 'auth' "
        "WHERE module IS NULL AND action LIKE 'Signed in via %'"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_module"), table_name="audit_logs")
    op.drop_column("audit_logs", "module")
