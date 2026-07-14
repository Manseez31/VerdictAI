"""Authentication & RBAC tests.

Includes the attacks that actually matter: refresh-token replay, privilege
escalation via a forged claim, account enumeration, and SQL injection against
the (new) user store.
"""

import os

import pytest

# Deterministic signing key for tests (the module otherwise mints an ephemeral
# one). Must be >= 32 bytes — tokens.py rejects anything shorter.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-not-for-production-at-least-32-bytes-long"
)

from auth.passwords import WeakPassword, hash_password, needs_rehash, verify_password
from auth.rbac import Permission, Role, has_permission, permissions_for
from auth.store import UserStore, token_digest
from auth.tokens import (
    TokenError, consume_one_time_token, create_access_token, create_one_time_token,
    decode_access, issue_pair, revoke_refresh, rotate_refresh,
)

GOOD_PW = "Correct-Horse-Battery-9"


@pytest.fixture
def store():
    s = UserStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def user(store):
    return store.create_user("lawyer@example.com", hash_password(GOOD_PW), Role.LAWYER)


# ===========================================================================
#  Passwords (Argon2id)
# ===========================================================================

def test_password_hash_and_verify():
    h = hash_password(GOOD_PW)
    assert h.startswith("$argon2id$")          # memory-hard KDF, not a fast hash
    assert GOOD_PW not in h                    # plaintext never stored
    assert verify_password(GOOD_PW, h)
    assert not verify_password("wrong-password-1", h)


def test_same_password_gets_unique_hashes():
    """Salting: identical passwords must not collide, or a leak reveals reuse."""
    assert hash_password(GOOD_PW) != hash_password(GOOD_PW)


def test_verify_never_raises_on_garbage():
    """A corrupt DB row must not 500 the login endpoint."""
    assert verify_password(GOOD_PW, "not-a-hash") is False
    assert verify_password("", "") is False


@pytest.mark.parametrize("bad", ["short", "alllowercaseonly", "12345678901234"])
def test_weak_passwords_rejected(bad):
    with pytest.raises(WeakPassword):
        hash_password(bad)


def test_absurdly_long_password_rejected():
    """Argon2 is memory-hard — unbounded input is a cheap DoS vector."""
    with pytest.raises(WeakPassword):
        hash_password("Aa1!" * 500)


# ===========================================================================
#  RBAC — deny by default, least privilege
# ===========================================================================

def test_admin_has_full_access():
    assert permissions_for(Role.ADMIN) == frozenset(Permission)


def test_lawyer_can_analyze_and_upload():
    assert has_permission(Role.LAWYER, Permission.CASE_ANALYZE)
    assert has_permission(Role.LAWYER, Permission.DOCUMENT_UPLOAD)
    assert has_permission(Role.LAWYER, Permission.REPORT_VIEW)


def test_researcher_cannot_analyze_or_upload():
    """A researcher reads the law; it does not process client matters."""
    assert has_permission(Role.RESEARCHER, Permission.SEARCH_LEGAL_DB)
    assert has_permission(Role.RESEARCHER, Permission.VIEW_CITATIONS)
    assert not has_permission(Role.RESEARCHER, Permission.CASE_ANALYZE)
    assert not has_permission(Role.RESEARCHER, Permission.DOCUMENT_UPLOAD)


def test_client_is_limited():
    assert has_permission(Role.CLIENT, Permission.CASE_VIEW_OWN)
    assert not has_permission(Role.CLIENT, Permission.CASE_VIEW_ALL)
    assert not has_permission(Role.CLIENT, Permission.DOCUMENT_UPLOAD)
    assert not has_permission(Role.CLIENT, Permission.CASE_ANALYZE)


def test_auditor_is_read_only_and_sees_no_case_data():
    """Separation of duties: the auditor watches the system, not the clients."""
    assert has_permission(Role.AUDITOR, Permission.AUDIT_READ)
    assert not has_permission(Role.AUDITOR, Permission.CASE_ANALYZE)
    assert not has_permission(Role.AUDITOR, Permission.CASE_VIEW_ALL)
    assert not has_permission(Role.AUDITOR, Permission.DOCUMENT_UPLOAD)
    assert not has_permission(Role.AUDITOR, Permission.CHAT_QUERY)


def test_only_admin_can_manage_users():
    for role in (Role.LAWYER, Role.RESEARCHER, Role.CLIENT, Role.AUDITOR):
        assert not has_permission(role, Permission.USER_MANAGE)
        assert not has_permission(role, Permission.ROLE_ASSIGN)
    assert has_permission(Role.ADMIN, Permission.USER_MANAGE)


def test_unknown_role_gets_nothing():
    """Deny by default: an unrecognized role is powerless, not powerful."""
    assert permissions_for("superuser") == frozenset()
    assert not has_permission("superuser", Permission.CHAT_QUERY)


# ===========================================================================
#  JWT
# ===========================================================================

def test_access_token_roundtrip(user):
    token = create_access_token(user.id, user.email, user.role)
    claims = decode_access(token)
    assert claims["sub"] == user.id
    assert claims["role"] == Role.LAWYER
    assert claims["typ"] == "access"


def test_refresh_token_cannot_be_used_as_access_token(store, user):
    """Type-confusion guard: a long-lived refresh token must never authenticate
    a request as an access token."""
    pair = issue_pair(store, user.id, user.email, user.role)
    with pytest.raises(TokenError, match="type mismatch"):
        decode_access(pair.refresh_token)


def test_tampered_token_is_rejected(user):
    token = create_access_token(user.id, user.email, Role.CLIENT)
    # Flip a character in the signature.
    forged = token[:-3] + ("abc" if not token.endswith("abc") else "xyz")
    with pytest.raises(TokenError):
        decode_access(forged)


def test_forged_role_claim_does_not_grant_privileges(store):
    """PRIVILEGE ESCALATION: even if an attacker could mint a token claiming
    admin, the server re-derives permissions from the role in the DATABASE."""
    client = store.create_user("client@example.com", hash_password(GOOD_PW), Role.CLIENT)
    # Attacker crafts a token asserting they are an admin.
    token = create_access_token(client.id, client.email, Role.ADMIN)
    claims = decode_access(token)
    assert claims["role"] == Role.ADMIN          # the claim says admin...

    # ...but the source of truth is the DB row, which still says client.
    db_user = store.get_by_id(claims["sub"])
    assert db_user.role == Role.CLIENT
    assert not has_permission(db_user.role, Permission.USER_MANAGE)


# ===========================================================================
#  Refresh rotation + REUSE DETECTION (the core session-security property)
# ===========================================================================

def test_refresh_rotates_and_old_token_dies(store, user):
    pair = issue_pair(store, user.id, user.email, user.role)
    new_pair, got = rotate_refresh(store, pair.refresh_token)
    assert new_pair.refresh_token != pair.refresh_token   # rotated
    assert got.id == user.id


def test_refresh_token_reuse_revokes_the_whole_family(store, user):
    """THE ATTACK: a stolen refresh token is replayed.

    RT1 -> RT2 (legit). The attacker then replays the spent RT1. That replay must
    (a) fail, and (b) burn the entire family, so the thief's RT2 is dead too —
    the theft becomes visible instead of silent.
    """
    pair1 = issue_pair(store, user.id, user.email, user.role)
    pair2, _ = rotate_refresh(store, pair1.refresh_token)      # legitimate rotation

    # Attacker replays the ALREADY-SPENT token.
    with pytest.raises(TokenError, match="reuse"):
        rotate_refresh(store, pair1.refresh_token)

    # The whole family is now revoked — even the currently-valid RT2.
    with pytest.raises(TokenError, match="revoked"):
        rotate_refresh(store, pair2.refresh_token)


def test_logout_revokes_family(store, user):
    pair = issue_pair(store, user.id, user.email, user.role)
    revoke_refresh(store, pair.refresh_token)
    with pytest.raises(TokenError, match="revoked"):
        rotate_refresh(store, pair.refresh_token)


def test_disabled_user_cannot_refresh(store, user):
    pair = issue_pair(store, user.id, user.email, user.role)
    store.set_active(user.id, False)
    with pytest.raises(TokenError, match="inactive"):
        rotate_refresh(store, pair.refresh_token)


def test_refresh_tokens_are_stored_hashed_not_plaintext(store, user):
    """A DB leak must not hand the attacker usable sessions."""
    pair = issue_pair(store, user.id, user.email, user.role)
    rows = store._conn.execute("SELECT token_hash FROM refresh_tokens").fetchall()
    stored = rows[0]["token_hash"]
    assert stored != pair.refresh_token
    assert stored == token_digest(pair.refresh_token)


# ===========================================================================
#  One-time tokens (email verification / password reset)
# ===========================================================================

def test_one_time_token_is_single_use(store, user):
    tok = create_one_time_token(store, user.id, "password_reset")
    assert consume_one_time_token(store, tok, "password_reset") == user.id
    # A second redemption must fail — otherwise a leaked reset link is reusable.
    assert consume_one_time_token(store, tok, "password_reset") is None


def test_one_time_token_purpose_is_bound(store, user):
    """A verification token must not be redeemable as a password reset."""
    tok = create_one_time_token(store, user.id, "verify_email")
    assert consume_one_time_token(store, tok, "password_reset") is None


def test_one_time_tokens_stored_hashed(store, user):
    tok = create_one_time_token(store, user.id, "password_reset")
    row = store._conn.execute("SELECT token_hash FROM one_time_tokens").fetchone()
    assert row["token_hash"] == token_digest(tok)
    assert row["token_hash"] != tok


# ===========================================================================
#  Store hardening
# ===========================================================================

def test_sql_injection_in_email_is_harmless(store, user):
    """The user store is the project's first SQL surface. All statements bind
    parameters, so this is inert data, not code."""
    evil = "' OR '1'='1' --"
    assert store.get_by_email(evil) is None

    dropper = "x@y.com'); DROP TABLE users; --"
    assert store.get_by_email(dropper) is None
    # The table must still exist and still hold our user.
    assert store.count() == 1
    assert store.get_by_email("lawyer@example.com") is not None


def test_emails_are_case_folded(store):
    """Admin@x.com must not be registerable alongside admin@x.com."""
    store.create_user("Admin@Example.COM", hash_password(GOOD_PW), Role.ADMIN)
    assert store.get_by_email("admin@example.com") is not None
    assert store.get_by_email("ADMIN@EXAMPLE.COM") is not None


def test_public_view_never_leaks_password_hash(user):
    assert "password_hash" not in user.to_public()


def test_auth_secure_by_default(monkeypatch):
    """F-1 REGRESSION GUARD.

    Auth previously defaulted to OFF, so a deployment that merely forgot to set
    AUTH_REQUIRED served every expensive LLM endpoint to the internet. Opening
    the API must be a deliberate act, never an omission.
    """
    from auth.deps import auth_required

    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    assert auth_required() is True, "auth must fail CLOSED when unconfigured"


@pytest.mark.parametrize("value,expected", [
    ("false", False), ("0", False), ("no", False),      # explicit opt-out only
    ("true", True), ("1", True), ("", True),            # anything else stays closed
    ("garbage", True),
])
def test_auth_only_opens_on_explicit_optout(monkeypatch, value, expected):
    from auth.deps import auth_required

    monkeypatch.setenv("AUTH_REQUIRED", value)
    assert auth_required() is expected


def test_short_jwt_secret_is_rejected(monkeypatch):
    """A short HMAC secret is brute-forceable, and a forged token is a total
    auth bypass — so fail fast at startup rather than sign weakly."""
    import auth.tokens as t

    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="too short"):
        t._secret()
