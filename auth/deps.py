"""FastAPI authorization dependencies (route guards).

KEY SECURITY PROPERTY
---------------------
Permissions are ALWAYS re-derived server-side from the user's CURRENT role in the
database — never trusted from the JWT's `perms` claim, and never trusted from the
`role` claim alone. Consequences:

  * A tampered/forged claim grants nothing (the signature already prevents this,
    but we do not rely on a single control).
  * Demoting or disabling a user takes effect on their NEXT request, not when
    their 15-minute access token happens to expire.

BACKWARD COMPATIBILITY
----------------------
`AUTH_REQUIRED` (default **false**) governs enforcement:

  * false — endpoints behave exactly as before (open). Auth still works, so you
            can register/login/test it; it is simply not mandatory. This keeps
            the existing UI and every existing test passing.
  * true  — every guarded endpoint requires a valid access token AND the
            required permission.

Flip it to `true` for production. This is the deliberate resolution of the
tension between "enforced login" and "preserve backward compatibility".
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from .rbac import Permission, Role, has_permission
from .store import User, UserStore
from .tokens import TokenError, decode_access

logger = logging.getLogger(__name__)


def auth_required() -> bool:
    return os.getenv("AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}


# Wired by backend.py at startup (single shared store).
_store: Optional[UserStore] = None
_audit = None


def bind(store: UserStore, audit=None) -> None:
    global _store, _audit
    _store = store
    _audit = audit


def get_store() -> UserStore:
    if _store is None:
        raise RuntimeError("auth.deps.bind() was never called")
    return _store


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Fallback: httpOnly cookie (browser clients). Not used for CSRF-sensitive
    # state change without the SameSite=Strict protection set in routes.py.
    return request.cookies.get("access_token")


def get_current_user_optional(request: Request) -> Optional[User]:
    """Resolve the caller if a valid token is present; otherwise None."""
    token = _bearer(request)
    if not token:
        return None
    try:
        claims = decode_access(token)
    except TokenError:
        return None

    user = get_store().get_by_id(claims["sub"])
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(request: Request) -> User:
    """Require an authenticated, active user."""
    token = _bearer(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_store().get_by_id(claims["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account no longer exists.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled.")
    return user


def require_permission(permission: Permission):
    """Route guard factory. Endpoints depend on a PERMISSION, never a role.

    Usage:
        @app.post("/chat", dependencies=[Depends(require_permission(Permission.CHAT_QUERY))])

    When AUTH_REQUIRED is false this is a no-op, preserving the open API.
    """

    def _guard(request: Request) -> Optional[User]:
        if not auth_required():
            # Still attach the user if they happen to be logged in — useful for
            # audit attribution — but do not demand it.
            return get_current_user_optional(request)

        user = get_current_user(request)

        # Re-derive from the CURRENT role in the DB. The JWT's `perms` claim is
        # never the source of truth.
        if not has_permission(user.role, permission):
            logger.warning(
                "AUTHZ DENIED user=%s role=%s permission=%s path=%s",
                user.email, user.role, permission, request.url.path,
            )
            if _audit:
                _audit.record(
                    "authz_denied", user_id=user.id, email=user.email,
                    role=str(user.role), permission=str(permission),
                    path=request.url.path,
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({user.role}) does not have the required permission: {permission}",
            )
        return user

    return _guard


def require_role(*roles: Role):
    """Guard by role. Prefer require_permission — use this only where the
    concept genuinely IS the role (e.g. admin-only user administration)."""

    def _guard(request: Request) -> Optional[User]:
        if not auth_required():
            return get_current_user_optional(request)
        user = get_current_user(request)
        if Role(user.role) not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(str(r) for r in roles)}",
            )
        return user

    return _guard
