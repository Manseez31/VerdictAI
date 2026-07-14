"""JWT access tokens + refresh-token rotation with reuse detection.

THE THREAT
----------
A refresh token is a long-lived credential. If it is stolen (XSS, log leak,
stolen backup), naive designs let the thief mint access tokens forever, silently
and indistinguishably from the real user.

ROTATION + REUSE DETECTION (the real defense)
---------------------------------------------
Every refresh consumes the old token and issues a NEW one in the same *family*:

    login  -> RT1 (family F)
    refresh(RT1) -> RT1 marked used, issue RT2 (family F)
    refresh(RT2) -> RT2 marked used, issue RT3 (family F)

If a token is ever presented TWICE, exactly one of two things is true: the
attacker is replaying a stolen token, or the legitimate user is replaying one
the attacker already spent. Either way the family is compromised, so we
**revoke the entire family** — the attacker AND the victim are logged out, and
the theft becomes visible instead of silent. This is the standard OAuth 2.0 BCP
mitigation, and it is the difference between "we have refresh tokens" and "our
refresh tokens are safe".

OTHER DECISIONS
---------------
* Access tokens are short-lived (15 min) and stateless — they are NOT checked
  against the DB on every request, which is what makes them cheap. The cost of
  that is a <=15-minute revocation window, which is the standard trade.
* Refresh tokens are stored hashed, are single-use, and carry a `jti`.
* `typ` is asserted on every decode, so an access token can never be replayed as
  a refresh token (or vice-versa) — a classic confused-deputy bug.
* The signing secret must be set explicitly in production; a random per-process
  secret is used in dev, which safely invalidates tokens on restart rather than
  shipping a predictable default.
"""

from __future__ import annotations

import datetime
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

import jwt

from .rbac import Role, permissions_for
from .store import UserStore

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TTL = datetime.timedelta(minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15")))
REFRESH_TTL = datetime.timedelta(days=int(os.getenv("REFRESH_TOKEN_DAYS", "7")))
ONE_TIME_TTL = datetime.timedelta(hours=1)

ISSUER = "verdictai"


MIN_SECRET_BYTES = 32   # RFC 7518 §3.2 minimum for HMAC-SHA256


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if secret:
        # Fail FAST and LOUD on a weak signing key. A short HMAC secret is
        # brute-forceable, and a forged token is a total authentication bypass —
        # far worse than a startup error.
        if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
            raise RuntimeError(
                f"JWT_SECRET is too short ({len(secret)} bytes). It must be at least "
                f"{MIN_SECRET_BYTES} bytes for HS256. Generate one with: "
                f"python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return secret
    # No hardcoded fallback: a predictable default secret is a forgery oracle.
    # A per-process random secret means dev works, and tokens simply stop being
    # valid across restarts.
    global _DEV_SECRET
    try:
        return _DEV_SECRET
    except NameError:
        _DEV_SECRET = secrets.token_urlsafe(64)
        logger.warning(
            "JWT_SECRET is not set — using an ephemeral development secret. "
            "Tokens will be invalidated on restart. SET JWT_SECRET IN PRODUCTION."
        )
        return _DEV_SECRET


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class TokenError(Exception):
    """Invalid, expired, revoked, or replayed token."""


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int          # access token lifetime, seconds


def _encode(payload: dict) -> str:
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def _decode(token: str, expected_typ: str) -> dict:
    try:
        claims = jwt.decode(
            token, _secret(), algorithms=[ALGORITHM], issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("Token has expired.")
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}")

    # Type confusion guard: a refresh token must never be usable as an access
    # token, and vice versa.
    if claims.get("typ") != expected_typ:
        raise TokenError("Token type mismatch.")
    return claims


def create_access_token(user_id: str, email: str, role: str) -> str:
    now = _now()
    return _encode({
        "iss": ISSUER,
        "sub": user_id,
        "email": email,
        "role": str(role),
        # Permissions are embedded for convenience, but the server ALWAYS
        # re-derives them from the role — a forged/edited claim cannot grant
        # anything (see deps.get_current_user).
        "perms": sorted(str(p) for p in permissions_for(role)),
        "typ": "access",
        "iat": now,
        "exp": now + ACCESS_TTL,
        "jti": str(uuid.uuid4()),
    })


def create_refresh_token(store: UserStore, user_id: str, family_id: Optional[str] = None) -> str:
    now = _now()
    jti = str(uuid.uuid4())
    family = family_id or str(uuid.uuid4())
    expires = now + REFRESH_TTL
    token = _encode({
        "iss": ISSUER,
        "sub": user_id,
        "typ": "refresh",
        "fam": family,
        "iat": now,
        "exp": expires,
        "jti": jti,
    })
    store.store_refresh(jti, user_id, token, family, expires.isoformat(timespec="seconds"))
    return token


def issue_pair(store: UserStore, user_id: str, email: str, role: str) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id, email, role),
        refresh_token=create_refresh_token(store, user_id),
        expires_in=int(ACCESS_TTL.total_seconds()),
    )


def decode_access(token: str) -> dict:
    return _decode(token, "access")


def rotate_refresh(store: UserStore, token: str):
    """Consume a refresh token and issue a new pair.

    Returns (TokenPair, User). Raises TokenError on any failure — including
    REPLAY, in which case the whole token family has already been revoked.
    """
    claims = _decode(token, "refresh")
    jti, user_id, family = claims["jti"], claims["sub"], claims.get("fam")

    row = store.get_refresh(jti)
    if row is None:
        # Signed correctly but unknown to us: the row was purged, or the token
        # came from an old secret/DB. Refuse.
        raise TokenError("Refresh token is not recognized.")

    if row["revoked"]:
        raise TokenError("Refresh token has been revoked.")

    if row["used"]:
        # ---- REPLAY DETECTED ----
        # This token was already spent. Either an attacker is replaying a stolen
        # token, or the victim is replaying one the attacker spent. Burn the
        # whole family: better to log everyone out than to let a thief persist.
        revoked = store.revoke_family(row["family_id"])
        logger.error(
            "REFRESH TOKEN REUSE detected for user=%s family=%s — revoked %d tokens",
            user_id, row["family_id"], revoked,
        )
        raise TokenError("Refresh token reuse detected. All sessions have been revoked.")

    user = store.get_by_id(user_id)
    if user is None or not user.is_active:
        raise TokenError("Account is inactive.")

    # Rotate: spend the old token, mint the next one in the same family.
    store.mark_refresh_used(jti)
    new_refresh = create_refresh_token(store, user_id, family_id=row["family_id"])

    pair = TokenPair(
        access_token=create_access_token(user.id, user.email, user.role),
        refresh_token=new_refresh,
        expires_in=int(ACCESS_TTL.total_seconds()),
    )
    return pair, user


def revoke_refresh(store: UserStore, token: str) -> None:
    """Secure logout: revoke the presented token's whole family, so no
    descendant refresh token remains usable."""
    try:
        claims = _decode(token, "refresh")
    except TokenError:
        return                     # already invalid — logout is idempotent
    row = store.get_refresh(claims["jti"])
    if row:
        store.revoke_family(row["family_id"])


# ---- single-use tokens (email verification / password reset) ----

def create_one_time_token(store: UserStore, user_id: str, purpose: str) -> str:
    """Opaque, high-entropy, single-use, 1-hour token. Stored hashed."""
    raw = secrets.token_urlsafe(32)
    expires = (_now() + ONE_TIME_TTL).isoformat(timespec="seconds")
    store.store_one_time(raw, user_id, purpose, expires)
    return raw


def consume_one_time_token(store: UserStore, raw: str, purpose: str) -> Optional[str]:
    return store.consume_one_time(raw, purpose)
