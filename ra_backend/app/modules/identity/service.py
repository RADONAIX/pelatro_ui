"""Identity business logic: auth, users, roles, audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.logging import client_ip_ctx, get_logger, request_id_ctx, user_agent_ctx
from app.core.mailer import send_email
from app.core.rbac import AUDIT_MODULE_LABELS, ROLE_LABELS, AuditModule, default_permissions_for
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    needs_rehash,
    verify_password,
)
from app.modules.identity import schemas
from app.modules.identity.models import AuditLog, Role, User, UserSession

log = get_logger("identity")


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _role_label(role: Role | None, role_id: str) -> str:
    if role and role.name:
        return role.name
    return ROLE_LABELS.get(role_id, role_id)


# --- Mappers ---------------------------------------------------------------
def to_auth_user(user: User) -> schemas.AuthUser:
    return schemas.AuthUser(
        id=user.id,
        name=user.full_name,
        email=user.email,
        role=user.role_id,
        roleLabel=_role_label(user.role, user.role_id),
        department=user.department,
        avatar=user.avatar or _initials(user.full_name),
        status=user.status,
        lastLogin=user.last_login,
        mustResetPassword=user.must_reset_password,
    )


def to_user_row(user: User) -> schemas.UserRow:
    return schemas.UserRow(
        id=user.id,
        fullName=user.full_name,
        email=user.email,
        phone=user.phone,
        department=user.department,
        role=user.role_id,
        status=user.status,
        lastLogin=user.last_login,
        createdAt=user.created_at,
    )


def to_role_row(role: Role) -> schemas.RoleRow:
    return schemas.RoleRow(
        id=role.id,
        name=role.name,
        description=role.description,
        status=role.status,
        permissions=role.permissions or {},
        createdAt=role.created_at,
        updatedAt=role.updated_at,
    )


# --- Audit -----------------------------------------------------------------
_FAILED_LOGIN = "Failed login"


async def record_audit(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    module: AuditModule,
    target: str | None = None,
    actor_id: str | None = None,
    meta: dict | None = None,
) -> None:
    """Write an audit entry, auto-enriched with the request's IP / user-agent /
    request-id captured by middleware (no need to pass the Request around).

    ``module`` is required on purpose: a new audited action must declare which
    feature it belongs to, so the audit screen can never show a blank Module."""
    db.add(
        AuditLog(
            actor=actor,
            actor_id=actor_id,
            module=module.value,
            action=action,
            target=target,
            ip_address=client_ip_ctx.get(),
            user_agent=user_agent_ctx.get(),
            request_id=request_id_ctx.get(),
            meta=meta or {},
            at=datetime.now(UTC),
        )
    )
    await db.flush()


async def list_audit(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    actor: str | None = None,
    action: str | None = None,
    module: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[schemas.AuditRow]:
    stmt = select(AuditLog)
    if actor:
        stmt = stmt.where(AuditLog.actor.ilike(f"%{actor}%"))
    if module:
        stmt = stmt.where(AuditLog.module == module)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if date_from:
        stmt = stmt.where(AuditLog.at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.at <= date_to)
    rows = (
        (await db.execute(stmt.order_by(AuditLog.at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )

    # Resolve target IDs to human labels (affected user email / role name). Skip
    # self-targets (e.g. login sets target=actor_id) — there the target adds nothing.
    target_ids = {r.target for r in rows if r.target and r.target != r.actor_id}
    labels: dict[str, str] = {}
    if target_ids:
        for uid, email in (
            await db.execute(select(User.id, User.email).where(User.id.in_(target_ids)))
        ).all():
            labels[uid] = email
        for rid, rname in (
            await db.execute(select(Role.id, Role.name).where(Role.id.in_(target_ids)))
        ).all():
            labels.setdefault(rid, rname)

    return [
        schemas.AuditRow(
            id=r.id,
            actor=r.actor,
            module=r.module,
            module_label=AUDIT_MODULE_LABELS.get(r.module or ""),
            action=r.action,
            target=r.target,
            target_label=None if r.target == r.actor_id else labels.get(r.target or ""),
            ip_address=str(r.ip_address) if r.ip_address else None,
            meta=r.meta or {},
            at=r.at,
        )
        for r in rows
    ]


# --- Auth: tokens & sessions ----------------------------------------------
def _tokens_for_session(user: User, session_id: str, refresh_jti: str) -> tuple[str, str]:
    access = create_access_token(
        user.id, extra_claims={"email": user.email, "role": user.role_id, "sid": session_id}
    )
    refresh = create_refresh_token(user.id, session_id=session_id, jti=refresh_jti)
    return access, refresh


async def _issue_session(
    db: AsyncSession, user: User, *, user_agent: str | None, ip: str | None
) -> tuple[str, str]:
    now = datetime.now(UTC)
    session_id = str(uuid.uuid4())
    refresh_jti = uuid.uuid4().hex
    db.add(
        UserSession(
            id=session_id,
            user_id=user.id,
            refresh_jti=refresh_jti,
            user_agent=user_agent,
            ip_address=ip,
            issued_at=now,
            expires_at=now + timedelta(minutes=settings.refresh_token_expire_minutes),
            last_seen_at=now,
        )
    )
    await db.flush()
    return _tokens_for_session(user, session_id, refresh_jti)


async def authenticate(
    db: AsyncSession,
    email: str,
    password: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str, User]:
    """Verify credentials (with lockout) and open a session.

    Returns (access_token, refresh_token, user). Raises AuthenticationError on
    bad credentials, disabled account, or active lockout.
    """
    now = datetime.now(UTC)
    user = (
        await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()

    # Generic message so we don't reveal which emails exist. Failed attempts are
    # audited, then committed explicitly (the AuthenticationError we raise would
    # otherwise roll the request's session back, dropping the audit row).
    if user is None:
        await record_audit(
            db, actor=email, action=_FAILED_LOGIN, module=AuditModule.AUTH,
            meta={"reason": "unknown user"},
        )
        await db.commit()
        raise AuthenticationError("Invalid email or password.")
    if user.locked_until and user.locked_until > now:
        await record_audit(
            db, actor=user.email, actor_id=user.id, action=_FAILED_LOGIN, module=AuditModule.AUTH,
            meta={"reason": "account locked"},
        )
        await db.commit()
        raise AuthenticationError(
            "Account temporarily locked due to failed login attempts. Try again later."
        )
    if not user.is_active:
        await record_audit(
            db, actor=user.email, actor_id=user.id, action=_FAILED_LOGIN, module=AuditModule.AUTH,
            meta={"reason": "account disabled"},
        )
        await db.commit()
        raise AuthenticationError("Your account has been disabled. Please contact administrator.")

    if not verify_password(password, user.hashed_password):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
            await record_audit(
                db, actor=user.email, actor_id=user.id, action="Account locked",
                module=AuditModule.AUTH,
                meta={"reason": "too many failed logins"},
            )
            # Commit so the lockout persists despite the error we raise next
            # (the request's session would otherwise roll back on exception).
            await db.commit()
            raise AuthenticationError(
                "Too many failed attempts. Account locked for "
                f"{settings.lockout_minutes} minutes."
            )
        await record_audit(
            db, actor=user.email, actor_id=user.id, action=_FAILED_LOGIN, module=AuditModule.AUTH,
            meta={"reason": "bad password", "failed_count": user.failed_login_count},
        )
        await db.commit()
        raise AuthenticationError("Invalid email or password.")

    # Success — reset lockout state and open a session.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login = now
    # Transparently upgrade a legacy (bcrypt) or stale-param hash to Argon2id.
    # Persisted in the same transaction as the login-success audit event.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
    access, refresh = await _issue_session(db, user, user_agent=user_agent, ip=ip)
    return access, refresh, user


async def sso_login(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str, User]:
    """Sign a user in via verified SSO identity (email already proven by the
    provider). Finds the user by email, optionally auto-provisioning a new one,
    then opens a session — returning the same (access, refresh, user) tuple as
    password login. Raises AuthenticationError for disabled / unknown accounts.
    """
    now = datetime.now(UTC)
    user = (
        await db.execute(
            select(User).where(
                func.lower(User.email) == email.lower(), User.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()

    if user is None:
        if not settings.sso_auto_provision:
            raise AuthenticationError(
                "No account exists for this email. Contact your administrator."
            )
        await _require_role(db, settings.sso_default_role)
        user = User(
            full_name=name,
            email=email,
            role_id=settings.sso_default_role,
            status="Active",
            # SSO users have no usable password — store a random unguessable hash.
            hashed_password=hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
            avatar=_initials(name),
            must_reset_password=False,
            password_changed_at=now,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

    if user.locked_until and user.locked_until > now:
        raise AuthenticationError("Account is temporarily locked. Try again later.")
    if not user.is_active:
        raise AuthenticationError("Your account has been disabled. Please contact administrator.")

    user.last_login = now
    access, refresh = await _issue_session(db, user, user_agent=user_agent, ip=ip)
    return access, refresh, user


async def refresh_session(
    db: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, str]:
    """Validate a refresh token and rotate it, returning a fresh token pair.

    Implements refresh-token rotation with reuse detection: presenting a stale
    (already-rotated) refresh token revokes the whole session.
    """
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired refresh token.") from exc
    if payload.get("type") != "refresh":
        raise AuthenticationError("Not a refresh token.")

    session_id = payload.get("sid")
    jti = payload.get("jti")
    session = (
        await db.execute(select(UserSession).where(UserSession.id == session_id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise AuthenticationError("Session is no longer valid. Please sign in again.")
    if session.refresh_jti != jti:
        # A previously-rotated token was replayed → likely theft. Kill the session.
        # Commit so the revocation persists despite the error we raise next.
        session.revoked_at = now
        await db.commit()
        raise AuthenticationError("Refresh token reuse detected. Session revoked.")

    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        session.revoked_at = now
        await db.commit()
        raise AuthenticationError("Account is no longer active.")

    new_jti = uuid.uuid4().hex
    session.refresh_jti = new_jti
    session.last_seen_at = now
    session.expires_at = now + timedelta(minutes=settings.refresh_token_expire_minutes)
    if user_agent:
        session.user_agent = user_agent
    if ip:
        session.ip_address = ip
    await db.flush()
    return _tokens_for_session(user, session.id, new_jti)


async def revoke_session(db: AsyncSession, session_id: str | None) -> None:
    if not session_id:
        return
    session = (
        await db.execute(select(UserSession).where(UserSession.id == session_id))
    ).scalar_one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await db.flush()


async def change_password(
    db: AsyncSession, user_id: str, current_password: str, new_password: str
) -> User:
    user = await get_user(db, user_id)
    if not verify_password(current_password, user.hashed_password):
        raise AuthenticationError("Current password is incorrect.")
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    user.must_reset_password = False
    await db.flush()
    await db.refresh(user)
    return user


# --- Password reset (forgot / reset) ---------------------------------------
async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Issue a reset token and email the reset link. Silent no-op for unknown or
    inactive accounts — the caller ALWAYS returns ok=True (anti-enumeration).
    Commits the token before emailing so the link is valid the moment it arrives."""
    user = (
        await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        log.info("password_reset_unknown_account", email=email)
        return

    token = generate_reset_token()
    now = datetime.now(UTC)
    user.reset_token_hash = hash_reset_token(token)
    user.reset_token_expires_at = now + timedelta(minutes=settings.reset_token_ttl_minutes)
    await record_audit(
        db, actor=user.email, actor_id=user.id,
        action="Requested password reset", module=AuditModule.AUTH,
    )
    await db.commit()

    link = f"{settings.frontend_base_url.rstrip('/')}/reset-password?token={token}"
    ttl = settings.reset_token_ttl_minutes
    text = (
        "We received a request to reset your RADONAIX password.\n\n"
        f"Reset it here (valid for {ttl} minutes):\n{link}\n\n"
        "If you did not request this, you can safely ignore this email."
    )
    html = (
        "<p>We received a request to reset your RADONAIX password.</p>"
        f'<p><a href="{link}">Reset your password</a> (valid for {ttl} minutes).</p>'
        "<p>If you did not request this, you can safely ignore this email.</p>"
    )
    await send_email(user.email, "Reset your RADONAIX password", html, text)


async def reset_password(db: AsyncSession, token: str, new_password: str) -> None:
    """Consume a reset token: set the new password, clear the token + lockout, and
    revoke all existing sessions (so any active login must re-authenticate).
    Raises AuthenticationError if the token is invalid or expired."""
    now = datetime.now(UTC)
    user = (
        await db.execute(
            select(User).where(
                User.reset_token_hash == hash_reset_token(token),
                User.reset_token_expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("This reset link is invalid or has expired.")

    user.hashed_password = hash_password(new_password)
    user.password_changed_at = now
    user.must_reset_password = False
    user.reset_token_hash = None
    user.reset_token_expires_at = None
    user.failed_login_count = 0
    user.locked_until = None
    # Invalidate every active session — a reset must log out other devices.
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await record_audit(
        db, actor=user.email, actor_id=user.id,
        action="Reset password", module=AuditModule.AUTH,
    )
    await db.flush()


async def get_user(db: AsyncSession, user_id: str) -> User:
    user = (
        await db.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found.")
    return user


# --- Users -----------------------------------------------------------------
async def list_users(db: AsyncSession, *, limit: int, offset: int) -> list[schemas.UserRow]:
    rows = (
        (
            await db.execute(
                select(User)
                .where(User.deleted_at.is_(None))
                .order_by(User.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [to_user_row(u) for u in rows]


async def _require_role(db: AsyncSession, role_id: str) -> Role:
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"Role '{role_id}' does not exist.")
    return role


async def create_user(db: AsyncSession, payload: schemas.UserCreate) -> User:
    await _require_role(db, payload.role)
    exists = (
        await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    ).scalar_one_or_none()
    if exists is not None and exists.deleted_at is None:
        raise ConflictError("A user with this email already exists.")

    now = datetime.now(UTC)
    if exists is not None:
        # A soft-deleted account still owns this email (unique column). Re-creating
        # with the same email REVIVES that row with the new details, so the email
        # is reusable and its audit history stays linked (same user id).
        exists.deleted_at = None
        exists.full_name = payload.fullName
        exists.phone = payload.phone
        exists.department = payload.department
        exists.role_id = payload.role
        exists.status = payload.status
        exists.hashed_password = hash_password(payload.password)
        exists.avatar = _initials(payload.fullName)
        exists.must_reset_password = payload.mustResetPassword
        exists.password_changed_at = now
        exists.failed_login_count = 0
        exists.locked_until = None
        exists.reset_token_hash = None
        exists.reset_token_expires_at = None
        await db.flush()
        await db.refresh(exists)
        return exists

    user = User(
        full_name=payload.fullName,
        email=payload.email,
        phone=payload.phone,
        department=payload.department,
        role_id=payload.role,
        status=payload.status,
        hashed_password=hash_password(payload.password),
        avatar=_initials(payload.fullName),
        must_reset_password=payload.mustResetPassword,
        password_changed_at=now,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user_id: str, payload: schemas.UserUpdate) -> User:
    user = await get_user(db, user_id)
    if payload.role and payload.role != user.role_id:
        await _require_role(db, payload.role)
        user.role_id = payload.role
    if payload.fullName is not None:
        user.full_name = payload.fullName
        user.avatar = _initials(payload.fullName)
    if payload.email is not None:
        user.email = payload.email
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.department is not None:
        user.department = payload.department
    if payload.status is not None:
        user.status = payload.status
    if payload.password:
        user.hashed_password = hash_password(payload.password)
        user.password_changed_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(user)
    return user


async def soft_delete_user(db: AsyncSession, user_id: str) -> User:
    """Soft-delete a user: mark deleted_at and revoke all their sessions.

    The user is then hidden from listings and can no longer authenticate
    (User.is_active is False once deleted_at is set).
    """
    user = await get_user(db, user_id)  # 404 if already deleted / missing
    now = datetime.now(UTC)
    user.deleted_at = now
    # Revoke every active session so existing tokens stop working immediately.
    sessions = (
        await db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    for session in sessions:
        session.revoked_at = now
    await db.flush()
    return user


# --- Roles -----------------------------------------------------------------
async def list_permissions() -> list[schemas.PermissionInfo]:
    """Read-only catalog of feature permission keys (feature-matrix RBAC model)."""
    from app.core.rbac import PERMISSION_CATALOG

    return [
        schemas.PermissionInfo(key=key.value, label=label, path=path)
        for key, label, path in PERMISSION_CATALOG
    ]
async def list_roles(db: AsyncSession) -> list[schemas.RoleRow]:
    rows = (await db.execute(select(Role).order_by(Role.created_at))).scalars().all()
    return [to_role_row(r) for r in rows]


async def upsert_role(db: AsyncSession, payload: schemas.RoleUpsert) -> Role:
    role_id = payload.id or payload.name.lower().replace(" ", "_")
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    perms = payload.permissions or default_permissions_for(role_id)
    if role is None:
        role = Role(
            id=role_id,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            permissions=perms,
        )
        db.add(role)
    else:
        role.name = payload.name
        role.description = payload.description
        role.status = payload.status
        if payload.permissions is not None:
            role.permissions = payload.permissions
    await db.flush()
    await db.refresh(role)
    return role


async def update_role_permissions(db: AsyncSession, role_id: str, permissions: dict) -> Role:
    role = await _require_role(db, role_id)
    role.permissions = permissions
    await db.flush()
    await db.refresh(role)
    return role


async def update_role(db: AsyncSession, role_id: str, payload: schemas.RoleUpdate) -> Role:
    """Update role metadata (name/description/status). Permissions are updated
    separately via update_role_permissions."""
    role = await _require_role(db, role_id)
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.status is not None:
        role.status = payload.status
    await db.flush()
    await db.refresh(role)
    return role
