"""Identity routes: /auth, /users, /roles, /audit-logs."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession, PageParams, Principal, require
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.middleware import client_ip
from app.core.rbac import AUDIT_MODULE_LABELS, AUDITED_MODULES, AuditModule, PermKey
from app.integrations import oauth
from app.modules.identity import schemas, service

log = get_logger("identity.router")
router = APIRouter(tags=["identity"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), client_ip(request)


def _redirect_with(target: str, **params: str) -> RedirectResponse:
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}{urlencode(params)}")


# --- Auth ------------------------------------------------------------------
@router.post("/auth/login", response_model=schemas.LoginResponse)
async def login(
    payload: schemas.LoginRequest, request: Request, db: DbSession
) -> schemas.LoginResponse:
    ua, ip = _client_meta(request)
    access, refresh, user = await service.authenticate(
        db, payload.email, payload.password, user_agent=ua, ip=ip
    )
    await service.record_audit(
        db,
        actor=user.email,
        actor_id=user.id,
        action="Signed in",
        module=AuditModule.AUTH,
        target=user.id,
    )
    return schemas.LoginResponse(
        token=access, refreshToken=refresh, user=service.to_auth_user(user)
    )


@router.post("/auth/refresh", response_model=schemas.TokenPair)
async def refresh(
    payload: schemas.RefreshRequest, request: Request, db: DbSession
) -> schemas.TokenPair:
    ua, ip = _client_meta(request)
    access, new_refresh = await service.refresh_session(
        db, payload.refreshToken, user_agent=ua, ip=ip
    )
    return schemas.TokenPair(token=access, refreshToken=new_refresh)


@router.post("/auth/logout", response_model=schemas.ActionResult)
async def logout(principal: CurrentUser, db: DbSession) -> schemas.ActionResult:
    await service.revoke_session(db, principal.session_id)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Signed out",
        module=AuditModule.AUTH,
    )
    return schemas.ActionResult(ok=True, detail="Signed out.")


@router.post("/auth/change-password", response_model=schemas.ActionResult)
async def change_password(
    payload: schemas.ChangePasswordRequest, principal: CurrentUser, db: DbSession
) -> schemas.ActionResult:
    await service.change_password(db, principal.id, payload.currentPassword, payload.newPassword)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Changed password",
        module=AuditModule.AUTH,
    )
    return schemas.ActionResult(ok=True, detail="Password updated.")


@router.post("/auth/forgot-password", response_model=schemas.ActionResult)
async def forgot_password(
    payload: schemas.ForgotPasswordRequest, db: DbSession
) -> schemas.ActionResult:
    """Start a password reset. ALWAYS returns ok=True (even for unknown emails) so
    the response can't be used to discover which accounts exist."""
    try:
        await service.request_password_reset(db, payload.email)
    except Exception:  # noqa: BLE001 — anti-enumeration: never surface internal errors
        log.warning("forgot_password_failed", email=payload.email)
    return schemas.ActionResult(ok=True)


@router.post("/auth/reset-password", response_model=schemas.ActionResult)
async def reset_password(
    payload: schemas.ResetPasswordRequest, db: DbSession
) -> schemas.ActionResult:
    """Complete a password reset with a valid, unexpired token."""
    await service.reset_password(db, payload.token, payload.newPassword)
    return schemas.ActionResult(ok=True, detail="Password has been reset.")


@router.get("/auth/me", response_model=schemas.AuthUser)
async def me(principal: CurrentUser, db: DbSession) -> schemas.AuthUser:
    user = await service.get_user(db, principal.id)
    return service.to_auth_user(user)


@router.get("/auth/my-permissions")
async def my_permissions(principal: CurrentUser) -> dict:
    """The current user's effective permission matrix (from their role in
    administration.roles). Auth-only — every user can read their own
    permissions — so the UI's RBAC reflects whatever Role Management configured.
    """
    return principal.permissions or {}


# --- SSO / OAuth (server-side / backend-based redirect flow) ---------------
def _oauth_start(provider: str, target: str) -> RedirectResponse:
    """Build the provider authorize URL and redirect the browser to consent.
    `target` is where we send the user back (with ?token=) after the callback."""
    if not oauth.is_configured(provider):
        return _redirect_with(target, error=f"{provider.title()} SSO is not configured.")
    state = oauth.encode_state(provider, target)
    return RedirectResponse(oauth.authorize_url(provider, state))


@router.get("/auth/oauth/{provider}/login")
async def oauth_login(provider: str) -> RedirectResponse:
    """Canonical entry point: the frontend redirects the browser here (no params).
    We send the user to the provider; on success the callback redirects them to
    the configured FRONTEND_SUCCESS_REDIRECT with a session token."""
    return _oauth_start(provider, oauth.allowed_redirect(settings.frontend_success_redirect))


@router.get("/auth/oauth/{provider}")
async def oauth_start(provider: str, redirect_uri: str | None = None) -> RedirectResponse:
    """Back-compat entry point that accepts a frontend-supplied redirect_uri
    (vetted against the CORS allow-list)."""
    return _oauth_start(provider, oauth.allowed_redirect(redirect_uri))


@router.get("/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Provider redirects here with a code. Exchange it, resolve the user, mint
    our JWT, and redirect back to the frontend with `?token=` (or `?error=`)."""
    try:
        target = oauth.allowed_redirect(oauth.decode_state(state or "").get("redirect_uri"))
    except AppError:
        target = settings.frontend_success_redirect

    if error:
        return _redirect_with(target, error=error_description or error)
    if not code:
        return _redirect_with(target, error="Missing authorization code.")

    try:
        info = await oauth.exchange_code_for_userinfo(provider, code)
        email, name = oauth.extract_identity(info)
        ua, ip = _client_meta(request)
        access, _refresh, user = await service.sso_login(
            db, email=email, name=name, user_agent=ua, ip=ip
        )
        await service.record_audit(
            db,
            actor=user.email,
            actor_id=user.id,
            action=f"Signed in via {provider.title()} SSO",
            module=AuditModule.AUTH,
            target=user.id,
        )
        return _redirect_with(target, token=access)
    except AppError as exc:
        return _redirect_with(target, error=exc.message)
    except Exception:  # noqa: BLE001 — never leak a stack trace into the redirect
        return _redirect_with(target, error="SSO sign-in failed. Please try again.")


@router.get("/permissions", response_model=list[schemas.PermissionInfo])
async def list_permissions(
    _: Principal = Depends(require(PermKey.ROLE_MANAGEMENT, "view")),
) -> list[schemas.PermissionInfo]:
    return await service.list_permissions()


# --- Users -----------------------------------------------------------------
users_router = APIRouter(prefix="/users", tags=["users"])


@users_router.get("", response_model=list[schemas.UserRow])
async def list_users(
    db: DbSession,
    page: PageParams,
    _: Principal = Depends(require(PermKey.USER_MANAGEMENT, "view")),
) -> list[schemas.UserRow]:
    return await service.list_users(db, limit=page.limit, offset=page.offset)


@users_router.post("", response_model=schemas.UserRow, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: schemas.UserCreate,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.USER_MANAGEMENT, "edit")),
) -> schemas.UserRow:
    user = await service.create_user(db, payload)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Created user",
        module=AuditModule.USER_MANAGEMENT,
        target=user.id,
    )
    return service.to_user_row(user)


@users_router.patch("/{user_id}", response_model=schemas.UserRow)
async def update_user(
    user_id: str,
    payload: schemas.UserUpdate,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.USER_MANAGEMENT, "edit")),
) -> schemas.UserRow:
    user = await service.update_user(db, user_id, payload)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Updated user",
        module=AuditModule.USER_MANAGEMENT,
        target=user.id,
    )
    return service.to_user_row(user)


@users_router.get("/{user_id}", response_model=schemas.UserRow)
async def get_user(
    user_id: str,
    db: DbSession,
    _: Principal = Depends(require(PermKey.USER_MANAGEMENT, "view")),
) -> schemas.UserRow:
    user = await service.get_user(db, user_id)
    return service.to_user_row(user)


@users_router.delete("/{user_id}", response_model=schemas.ActionResult)
async def delete_user(
    user_id: str,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.USER_MANAGEMENT, "edit")),
) -> schemas.ActionResult:
    if user_id == principal.id:
        raise AppError("You cannot delete your own account.")
    user = await service.soft_delete_user(db, user_id)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Deleted user",
        module=AuditModule.USER_MANAGEMENT,
        target=user.id,
    )
    return schemas.ActionResult(ok=True, detail="User deleted.")


# --- Roles -----------------------------------------------------------------
roles_router = APIRouter(prefix="/roles", tags=["roles"])


@roles_router.get("", response_model=list[schemas.RoleRow])
async def list_roles(
    db: DbSession,
    _: Principal = Depends(require(PermKey.ROLE_MANAGEMENT, "view")),
) -> list[schemas.RoleRow]:
    return await service.list_roles(db)


@roles_router.patch("/{role_id}", response_model=schemas.RoleRow)
async def update_role(
    role_id: str,
    payload: schemas.RoleUpdate,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.ROLE_MANAGEMENT, "edit")),
) -> schemas.RoleRow:
    role = await service.update_role(db, role_id, payload)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Updated role",
        module=AuditModule.ROLE_MANAGEMENT,
        target=role.id,
    )
    return service.to_role_row(role)


@roles_router.put("/{role_id}/permissions", response_model=schemas.RoleRow)
async def update_role_permissions(
    role_id: str,
    payload: schemas.RolePermissionsUpdate,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.ROLE_MANAGEMENT, "edit")),
) -> schemas.RoleRow:
    role = await service.update_role_permissions(db, role_id, payload.permissions)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Updated role permissions",
        module=AuditModule.ROLE_MANAGEMENT,
        target=role_id,
    )
    return service.to_role_row(role)


@roles_router.post("", response_model=schemas.RoleRow, status_code=status.HTTP_201_CREATED)
async def upsert_role(
    payload: schemas.RoleUpsert,
    db: DbSession,
    principal: Principal = Depends(require(PermKey.ROLE_MANAGEMENT, "edit")),
) -> schemas.RoleRow:
    role = await service.upsert_role(db, payload)
    await service.record_audit(
        db,
        actor=principal.email,
        actor_id=principal.id,
        action="Saved role",
        module=AuditModule.ROLE_MANAGEMENT,
        target=role.id,
    )
    return service.to_role_row(role)


# --- Audit -----------------------------------------------------------------
audit_router = APIRouter(prefix="/audit-logs", tags=["audit"])


@audit_router.get("", response_model=list[schemas.AuditRow])
async def list_audit(
    db: DbSession,
    page: PageParams,
    actor: str | None = Query(default=None, description="Filter by actor (substring)"),
    action: str | None = Query(default=None, description="Filter by action (substring)"),
    module: AuditModule | None = Query(default=None, description="Filter by module (exact)"),
    date_from: datetime | None = Query(default=None, description="Only events at/after this time"),
    date_to: datetime | None = Query(default=None, description="Only events at/before this time"),
    _: Principal = Depends(require(PermKey.SETTINGS, "view")),
) -> list[schemas.AuditRow]:
    return await service.list_audit(
        db,
        limit=page.limit,
        offset=page.offset,
        actor=actor,
        action=action,
        module=module.value if module else None,
        date_from=date_from,
        date_to=date_to,
    )


@audit_router.get("/modules", response_model=list[schemas.AuditModuleOption])
async def list_audit_modules(
    _: Principal = Depends(require(PermKey.SETTINGS, "view")),
) -> list[schemas.AuditModuleOption]:
    """Modules that can appear in the audit log — backs the UI's Module filter.

    Only modules with an audited action are listed (AUDITED_MODULES); view-only
    features that raise no audit events are excluded."""
    return [
        schemas.AuditModuleOption(key=m.value, label=AUDIT_MODULE_LABELS[m.value])
        for m in AUDITED_MODULES
    ]


router.include_router(users_router)
router.include_router(roles_router)
router.include_router(audit_router)
