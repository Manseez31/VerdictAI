"""User & token persistence (SQLite).

SECURITY NOTES
--------------
* This is the project's FIRST user-controlled SQL surface. Every statement uses
  bound parameters (`?`) — string interpolation into SQL appears nowhere in this
  file, so injection has no purchase. A test asserts it.
* Refresh tokens are stored HASHED (SHA-256), never in plaintext. A database
  leak therefore does not hand the attacker usable sessions — the same reason we
  never store plaintext passwords.
* Single-use verification/reset tokens are hashed for the same reason and carry
  an expiry.
* Emails are stored case-folded and uniquely indexed, so `Admin@x.com` cannot be
  registered alongside `admin@x.com` to impersonate.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .rbac import Role

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def token_digest(raw: str) -> str:
    """Tokens are high-entropy already, so a fast hash is correct here (unlike
    passwords, which need a slow KDF)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class User:
    id: str
    email: str
    full_name: str
    role: str
    password_hash: str
    is_active: bool
    email_verified: bool
    created_at: str

    def to_public(self) -> dict:
        """Never leak the password hash to a caller."""
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "email_verified": self.email_verified,
            "created_at": self.created_at,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    full_name      TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1,
    email_verified INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

-- Refresh tokens: hashed, single-use, chained by family for reuse detection.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti         TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    family_id   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rt_family ON refresh_tokens(family_id);
CREATE INDEX IF NOT EXISTS idx_rt_user   ON refresh_tokens(user_id);

-- Single-use email-verification / password-reset tokens.
CREATE TABLE IF NOT EXISTS one_time_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    purpose    TEXT NOT NULL,           -- 'verify_email' | 'password_reset'
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


class UserStore:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or os.getenv("AUTH_DB_PATH", "data/auth/users.db"))
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False + our own lock: FastAPI serves from a threadpool.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------- users ----------------

    def create_user(
        self, email: str, password_hash: str, role: str,
        full_name: str = "", email_verified: bool = False,
    ) -> User:
        user = User(
            id=str(uuid.uuid4()),
            email=email.strip().casefold(),
            full_name=full_name.strip(),
            role=str(Role(role)),
            password_hash=password_hash,
            is_active=True,
            email_verified=email_verified,
            created_at=_now(),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (id, email, full_name, role, password_hash, is_active, email_verified, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, user.email, user.full_name, user.role, user.password_hash,
                 int(user.is_active), int(user.email_verified), user.created_at),
            )
            self._conn.commit()
        return user

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"], email=row["email"], full_name=row["full_name"],
            role=row["role"], password_hash=row["password_hash"],
            is_active=bool(row["is_active"]), email_verified=bool(row["email_verified"]),
            created_at=row["created_at"],
        )

    def get_by_email(self, email: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().casefold(),)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_id(self, user_id: str) -> Optional[User]:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> List[User]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._row_to_user(r) for r in rows]

    def set_role(self, user_id: str, role: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET role = ? WHERE id = ?", (str(Role(role)), user_id))
            self._conn.commit()

    def set_active(self, user_id: str, active: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(active), user_id))
            self._conn.commit()

    def set_password(self, user_id: str, password_hash: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            self._conn.commit()

    def mark_email_verified(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
            self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    # ---------------- refresh tokens ----------------

    def store_refresh(self, jti: str, user_id: str, raw_token: str, family_id: str, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO refresh_tokens (jti, user_id, token_hash, family_id, expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (jti, user_id, token_digest(raw_token), family_id, expires_at, _now()),
            )
            self._conn.commit()

    def get_refresh(self, jti: str) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM refresh_tokens WHERE jti = ?", (jti,)).fetchone()

    def mark_refresh_used(self, jti: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE refresh_tokens SET used = 1 WHERE jti = ?", (jti,))
            self._conn.commit()

    def revoke_family(self, family_id: str) -> int:
        """Kill an entire token family — used on refresh-token REUSE, which means
        a stolen token is in play and every descendant must die."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ? AND revoked = 0",
                (family_id,),
            )
            self._conn.commit()
            return cur.rowcount

    def revoke_all_for_user(self, user_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
            self._conn.commit()
            return cur.rowcount

    # ---------------- one-time tokens ----------------

    def store_one_time(self, raw_token: str, user_id: str, purpose: str, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO one_time_tokens (token_hash, user_id, purpose, expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (token_digest(raw_token), user_id, purpose, expires_at, _now()),
            )
            self._conn.commit()

    def consume_one_time(self, raw_token: str, purpose: str) -> Optional[str]:
        """Redeem a single-use token. Returns the user_id, or None if the token is
        unknown, wrong-purpose, already used, or expired."""
        digest = token_digest(raw_token)
        row = self._conn.execute(
            "SELECT * FROM one_time_tokens WHERE token_hash = ? AND purpose = ?",
            (digest, purpose),
        ).fetchone()
        if not row or row["used"]:
            return None
        if row["expires_at"] < _now():
            return None
        with self._lock:
            self._conn.execute("UPDATE one_time_tokens SET used = 1 WHERE token_hash = ?", (digest,))
            self._conn.commit()
        return row["user_id"]

    def close(self) -> None:
        self._conn.close()
