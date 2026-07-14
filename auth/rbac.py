"""Role-Based Access Control.

DESIGN
------
Permissions are the unit of authorization, roles are just named bundles of them.
Endpoints depend on a PERMISSION, never on a role — so adding a role, or moving
a capability between roles, never requires touching an endpoint. (Open/Closed.)

The matrix is deny-by-default: anything not explicitly granted is denied.

Least privilege, as specified:

  Admin      full access (including user administration)
  Lawyer     analyze cases, upload documents, view reports
  Researcher search the legal database, view citations
  Client     limited case access (read own cases only)
  Auditor    read-only access to logs — deliberately NOT able to run analyses
             or read case content, because an auditor's job is to inspect the
             system's behaviour, not its clients' matters.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Dict, FrozenSet


class Role(StrEnum):
    ADMIN = "admin"
    LAWYER = "lawyer"
    RESEARCHER = "researcher"
    CLIENT = "client"
    AUDITOR = "auditor"


class Permission(StrEnum):
    # Legal Q&A / research
    CHAT_QUERY = "chat:query"
    SEARCH_LEGAL_DB = "search:legal_db"
    VIEW_CITATIONS = "citations:view"

    # Case work
    CASE_ANALYZE = "case:analyze"          # run the multi-agent suite / simulator
    CASE_VIEW_OWN = "case:view_own"        # a client's own matters
    CASE_VIEW_ALL = "case:view_all"
    DOCUMENT_UPLOAD = "document:upload"
    REPORT_VIEW = "report:view"
    REPORT_EXPORT = "report:export"

    # Platform / oversight
    AUDIT_READ = "audit:read"
    SECURITY_METRICS_READ = "security:metrics_read"

    # Administration
    USER_MANAGE = "user:manage"            # create/disable users, change roles
    ROLE_ASSIGN = "role:assign"


# Deny-by-default matrix. Explicit > clever.
ROLE_PERMISSIONS: Dict[Role, FrozenSet[Permission]] = {
    Role.ADMIN: frozenset(Permission),     # full access

    Role.LAWYER: frozenset({
        Permission.CHAT_QUERY,
        Permission.SEARCH_LEGAL_DB,
        Permission.VIEW_CITATIONS,
        Permission.CASE_ANALYZE,
        Permission.CASE_VIEW_ALL,
        Permission.DOCUMENT_UPLOAD,
        Permission.REPORT_VIEW,
        Permission.REPORT_EXPORT,
    }),

    Role.RESEARCHER: frozenset({
        Permission.CHAT_QUERY,
        Permission.SEARCH_LEGAL_DB,
        Permission.VIEW_CITATIONS,
        # No case analysis, no uploads: research reads the law, it does not
        # process client matters.
    }),

    Role.CLIENT: frozenset({
        Permission.CHAT_QUERY,
        Permission.CASE_VIEW_OWN,
        Permission.REPORT_VIEW,
        # Deliberately NO upload and NO analyze: a client consumes results.
    }),

    Role.AUDITOR: frozenset({
        Permission.AUDIT_READ,
        Permission.SECURITY_METRICS_READ,
        # Read-only oversight. No case content, no analysis, no uploads —
        # separation of duties: the auditor watches the system, not the clients.
    }),
}


def permissions_for(role: Role | str) -> FrozenSet[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return frozenset()          # unknown role => no permissions (deny by default)


def has_permission(role: Role | str, permission: Permission) -> bool:
    return permission in permissions_for(role)
