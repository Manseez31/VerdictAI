"""VerdictAI authentication & authorization.

  passwords.py  Argon2id hashing + password policy
  store.py      SQLite persistence (users, refresh tokens, one-time tokens)
  tokens.py     JWT access tokens + refresh rotation with REUSE DETECTION
  rbac.py       5 roles -> permission matrix (deny by default)
  deps.py       FastAPI route guards (permission-based, re-derived server-side)
  routes.py     /auth endpoints

Enforcement is governed by AUTH_REQUIRED (default false), which keeps the
existing open API and every existing test working while the full auth stack is
present and usable. Set AUTH_REQUIRED=true for production.
"""

from .rbac import Permission, Role, ROLE_PERMISSIONS, has_permission, permissions_for
from .store import User, UserStore
from .deps import get_current_user, get_current_user_optional, require_permission, require_role
from . import routes

__all__ = [
    "Permission", "Role", "ROLE_PERMISSIONS", "has_permission", "permissions_for",
    "User", "UserStore",
    "get_current_user", "get_current_user_optional", "require_permission", "require_role",
    "routes",
]
