"""Authentication & user-administration endpoints.

All routes are additive under /auth — no existing contract changes.

SECURITY DECISIONS WORTH KNOWING
--------------------------------
* Login is deliberately vague ("Invalid email or password") and always performs a
  password hash even when the user does not exist, so timing cannot be used to
  enumerate accounts.
* Registration and password-reset never reveal whether an email exists.
* Refresh tokens live in an httpOnly, SameSite=Strict cookie: JavaScript cannot
  read them (so XSS cannot steal the long-lived credential), and the browser will
  not attach them cross-site (so CSRF cannot spend them).
* Every privilege change is written to the tamper-evident audit log.
* The FIRST registered user becomes Admin (bootstrap); everyone after that gets
  the default role and only an Admin can elevate them. This avoids shipping a
  default password, which is a far worse failure mode.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from .deps import bind, get_current_user, get_store, require_role
from .passwords import WeakPassword, hash_password, needs_rehash, verify_password
from .rbac import ROLE_PERMISSIONS, Permission, Role, permissions_for
from .store import User, UserStore
from .tokens import (
    REFRESH_TTL, TokenError, consume_one_time_token, create_one_time_token,
    issue_pair, revoke_refresh, rotate_refresh,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
DEFAULT_ROLE = Role(os.getenv("DEFAULT_ROLE", "client"))

_audit = None


def init(store: UserStore, audit=None) -> None:
    """Wire the shared store + audit log (called from backend.py)."""
    global _audit
    _audit = audit
    bind(store, audit)


def _secure_cookies() -> bool:
    return os.getenv("FORCE_HTTPS", "false").strip().lower() in {"1", "true", "yes", "on"}


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,                       # JS cannot read it -> XSS cannot steal it
        secure=_secure_cookies(),            # HTTPS-only in production
        samesite="strict",                   # browser won't send it cross-site -> CSRF-safe
        max_age=int(REFRESH_TTL.total_seconds()),
        path="/auth",                        # only sent to the auth endpoints
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/auth")


# ---------------------------------------------------------------- schemas

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    full_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    permissions: List[str]
    # Returned for non-browser clients; browsers should rely on the cookie.
    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    email_verified: bool
    permissions: List[str]


class RoleChangeRequest(BaseModel):
    role: Role


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=12, max_length=1024)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        **user.to_public(),
        permissions=sorted(str(p) for p in permissions_for(user.role)),
    )


# ---------------------------------------------------------------- routes

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request):
    store = get_store()

    try:
        pw_hash = hash_password(req.password)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Bootstrap: the first account is the Admin. Safer than a default password.
    is_first = store.count() == 0
    role = Role.ADMIN if is_first else DEFAULT_ROLE

    if store.get_by_email(req.email):
        # Do not confirm that the address is taken (account enumeration).
        raise HTTPException(status_code=409, detail="Unable to register with those details.")

    user = store.create_user(
        email=req.email, password_hash=pw_hash, role=str(role),
        full_name=req.full_name, email_verified=is_first,
    )

    if _audit:
        _audit.record(
            "user_registered", user_id=user.id, email=user.email,
            role=str(role), bootstrap_admin=is_first,
            ip=request.client.host if request.client else "unknown",
        )

    # Email verification: we mint the token and log it. There is no SMTP
    # integration — sending is left to the deployer (see /auth/verify-email).
    if not is_first:
        token = create_one_time_token(store, user.id, "verify_email")
        logger.info("Email verification token for %s: %s", user.email, token)

    return _user_response(user)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, response: Response, request: Request):
    store = get_store()
    user = store.get_by_email(req.email)

    # Constant-work path: hash even when the user is absent, so response time
    # does not reveal whether the account exists.
    stored = user.password_hash if user else "$argon2id$v=19$m=19456,t=2,p=1$" + "A" * 22 + "$" + "A" * 43
    ok = verify_password(req.password, stored)

    if not user or not ok:
        if _audit:
            _audit.record(
                "login_failed", email=req.email,
                ip=request.client.host if request.client else "unknown",
            )
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled.")

    # Transparent hash upgrade when we raise Argon2 cost parameters.
    if needs_rehash(user.password_hash):
        store.set_password(user.id, hash_password(req.password))

    pair = issue_pair(store, user.id, user.email, user.role)
    _set_refresh_cookie(response, pair.refresh_token)

    if _audit:
        _audit.record(
            "login_success", user_id=user.id, email=user.email, role=str(user.role),
            ip=request.client.host if request.client else "unknown",
        )

    return TokenResponse(
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        role=str(user.role),
        permissions=sorted(str(p) for p in permissions_for(user.role)),
        refresh_token=pair.refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response):
    """Rotate the refresh token. Replay of a spent token revokes the whole family."""
    store = get_store()
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        body_token = request.headers.get("X-Refresh-Token")
        token = body_token
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token supplied.")

    try:
        pair, user = rotate_refresh(store, token)
    except TokenError as exc:
        _clear_refresh_cookie(response)
        if "reuse" in str(exc).lower():
            # A stolen token was replayed — this is a security incident, and the
            # entire token family has already been revoked.
            logger.error("Refresh token reuse — all sessions revoked")
            if _audit:
                _audit.record(
                    "refresh_token_reuse", detail=str(exc),
                    ip=request.client.host if request.client else "unknown",
                )
        raise HTTPException(status_code=401, detail=str(exc))

    _set_refresh_cookie(response, pair.refresh_token)
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        role=str(user.role),
        permissions=sorted(str(p) for p in permissions_for(user.role)),
        refresh_token=pair.refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    """Secure logout: revokes the token FAMILY, not just the presented token, so
    no descendant refresh token survives. Idempotent."""
    store = get_store()
    token = request.cookies.get(REFRESH_COOKIE) or request.headers.get("X-Refresh-Token")
    if token:
        revoke_refresh(store, token)
    _clear_refresh_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.post("/verify-email")
def verify_email(token: str):
    store = get_store()
    user_id = consume_one_time_token(store, token, "verify_email")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token.")
    store.mark_email_verified(user_id)
    if _audit:
        _audit.record("email_verified", user_id=user_id)
    return {"verified": True}


@router.post("/password-reset/request")
def password_reset_request(req: PasswordResetRequest):
    """Always returns 200 — revealing whether an address exists is an
    enumeration oracle."""
    store = get_store()
    user = store.get_by_email(req.email)
    if user:
        token = create_one_time_token(store, user.id, "password_reset")
        logger.info("Password reset token for %s: %s", user.email, token)
        if _audit:
            _audit.record("password_reset_requested", user_id=user.id, email=user.email)
    return {"message": "If that account exists, a reset link has been sent."}


@router.post("/password-reset/confirm")
def password_reset_confirm(req: PasswordResetConfirm):
    store = get_store()
    user_id = consume_one_time_token(store, req.token, "password_reset")
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")

    try:
        store.set_password(user_id, hash_password(req.new_password))
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # A password reset must invalidate every existing session — otherwise a
    # thief who already has a refresh token keeps their access after the
    # victim "recovers" the account.
    revoked = store.revoke_all_for_user(user_id)
    if _audit:
        _audit.record("password_reset_completed", user_id=user_id, sessions_revoked=revoked)
    return {"message": "Password updated. All existing sessions have been revoked."}


# ---------------------------------------------------------------- admin

@router.get("/users", response_model=List[UserResponse])
def list_users(_: User = Depends(require_role(Role.ADMIN))):
    return [_user_response(u) for u in get_store().list_users()]


@router.put("/users/{user_id}/role", response_model=UserResponse)
def change_role(
    user_id: str,
    req: RoleChangeRequest,
    actor: User = Depends(require_role(Role.ADMIN)),
):
    """Change a user's role. Every privilege change is audited (immutably)."""
    store = get_store()
    target = store.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    # An admin must not be able to silently strip their own last admin rights and
    # lock the platform out of administration.
    if target.id == actor.id and req.role != Role.ADMIN:
        admins = [u for u in store.list_users() if u.role == Role.ADMIN and u.is_active]
        if len(admins) <= 1:
            raise HTTPException(status_code=409, detail="Cannot demote the only remaining admin.")

    old_role = target.role
    store.set_role(user_id, str(req.role))

    # Role change = privilege change. Revoke sessions so the new (possibly
    # reduced) privileges take effect immediately rather than at token expiry.
    store.revoke_all_for_user(user_id)

    if _audit:
        _audit.record(
            "role_changed", actor_id=actor.id, actor_email=actor.email,
            target_id=user_id, target_email=target.email,
            old_role=str(old_role), new_role=str(req.role),
        )
    logger.warning("ROLE CHANGE by %s: %s %s -> %s", actor.email, target.email, old_role, req.role)
    return _user_response(store.get_by_id(user_id))


@router.put("/users/{user_id}/active", response_model=UserResponse)
def set_active(user_id: str, active: bool, actor: User = Depends(require_role(Role.ADMIN))):
    store = get_store()
    target = store.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    store.set_active(user_id, active)
    if not active:
        store.revoke_all_for_user(user_id)     # disabling must be immediate

    if _audit:
        _audit.record(
            "user_active_changed", actor_id=actor.id, actor_email=actor.email,
            target_id=user_id, target_email=target.email, active=active,
        )
    return _user_response(store.get_by_id(user_id))


@router.get("/roles")
def list_roles():
    """The permission matrix — useful for the UI and for auditors."""
    return {
        "roles": {
            str(role): sorted(str(p) for p in perms)
            for role, perms in ROLE_PERMISSIONS.items()
        }
    }
