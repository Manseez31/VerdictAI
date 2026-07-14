"""Password hashing (Argon2id).

WHY ARGON2id
------------
Argon2id won the Password Hashing Competition and is the current OWASP first
choice. Unlike bcrypt/PBKDF2 it is *memory-hard*: an attacker with GPUs or ASICs
cannot trade cheap parallelism for speed, because each guess must also allocate
memory. Parameters below follow the OWASP minimum (19 MiB, t=2, p=1).

Two properties this module guarantees:

  * `verify()` is constant-time with respect to the hash comparison (argon2-cffi
    handles this), and returns False rather than raising on a malformed hash, so
    a corrupt row cannot 500 the login endpoint.
  * `needs_rehash()` lets us transparently upgrade stored hashes when we raise
    the cost parameters, without forcing a password reset.
"""

from __future__ import annotations

import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

# OWASP-recommended minimum for Argon2id.
_hasher = PasswordHasher(
    time_cost=2,          # iterations
    memory_cost=19456,    # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

MIN_PASSWORD_LENGTH = 12


class WeakPassword(ValueError):
    """Raised when a password fails the policy."""


def validate_password(password: str) -> None:
    """Enforce a minimal, non-annoying policy.

    Length is the dominant factor in password strength, so we require length and
    a little variety rather than a maze of composition rules that push users
    toward 'Password1!'.
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 1024:
        # Argon2 is memory-hard: unbounded input is a cheap DoS vector.
        raise WeakPassword("Password is too long (max 1024 characters).")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        raise WeakPassword(
            "Password must include at least three of: lowercase, uppercase, digit, symbol."
        )


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification. Never raises on bad input."""
    if not password or not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        logger.exception("Unexpected password verification error")
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than we now require."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:
        return False
