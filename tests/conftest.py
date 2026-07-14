"""Shared test fixtures.

Deterministic JWT secret must be set BEFORE any auth module is imported, since
auth.tokens reads JWT_SECRET at call time and would otherwise mint an ephemeral
per-process secret.
"""

import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-not-for-production-at-least-32-bytes-long"
)

import pytest


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """The rate limiter is process-global and counts per client IP. Every test
    shares TestClient's IP, so without this the suite's own request volume trips
    the 20/min limit and later tests fail with 429 — a test-isolation artifact,
    not a product bug. Reset the window before each test.
    """
    try:
        import backend
    except Exception:
        yield
        return

    limiter = getattr(backend, "rate_limiter", None)
    if limiter is not None:
        with limiter._lock:
            limiter._hits.clear()
    yield
