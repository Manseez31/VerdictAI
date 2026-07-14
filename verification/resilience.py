"""Agent resilience: retry, backoff, and FAIL-CLOSED degradation.

WHY THIS EXISTS (found by live testing, not by theory)
------------------------------------------------------
The first live run of the pipeline returned HTTP 502. The cause: Groq enforces a
tokens-per-minute quota, one of the three parallel critics was rejected with a
429, and the raised exception took down the ENTIRE request. Five LLM calls means
five independent chances to fail — an orchestrator that treats any one failure as
fatal is not production-ready.

TWO FIXES, AND THEY PULL IN OPPOSITE DIRECTIONS
-----------------------------------------------
1. RETRY with backoff, honouring the provider's own "try again in Ns" hint. Most
   rate-limit failures are transient and disappear on the second attempt.

2. FAIL CLOSED when retries are exhausted. This is the subtle one. The obvious
   move — "skip the critic that failed and carry on" — is exactly wrong here. The
   whole premise of Phase 2 is that no answer ships unless it was independently
   verified. A critic that never ran did not verify anything, so an answer that
   skips it is UNVERIFIED while looking verified. That is worse than no answer.

   So a failed agent does not crash the pipeline (fix 1) and does not get waved
   through either: it is recorded as `agent_failed`, and the consensus gate
   refuses to present a conclusion, telling the user verification was incomplete
   and to retry.

Availability is sacrificed for integrity — the correct trade for a legal tool.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 30.0

# Providers usually tell us exactly how long to wait: "Please try again in 5.24s".
_RETRY_AFTER = re.compile(r"try again in ([0-9.]+)s", re.IGNORECASE)
_TRANSIENT = ("rate limit", "rate_limit", "429", "timeout", "timed out",
              "503", "502", "overloaded", "temporarily unavailable")


def _is_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT)


def _retry_after(exc: Exception, fallback: float) -> float:
    m = _RETRY_AFTER.search(str(exc))
    if m:
        try:
            return min(float(m.group(1)) + 0.5, MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(fallback, MAX_BACKOFF_SECONDS)


def call_agent_safely(
    fn: Callable[..., Dict[str, Any]],
    *args,
    agent_name: str,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run one agent with retry/backoff. NEVER raises.

    On unrecoverable failure returns ``{"agent_failed": True, ...}``, which the
    consensus gate treats as "verification incomplete" and refuses to present an
    answer. Degraded, not silently skipped.
    """
    # Resolved at CALL time, not bound as a default at import time — otherwise the
    # seam is untestable (a patched time.sleep would never be seen).
    sleep = sleep or time.sleep

    backoff = 2.0
    last: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(*args, **kwargs)
            if attempt > 1:
                logger.info("Agent %s succeeded on attempt %d", agent_name, attempt)
            return result
        except Exception as exc:  # noqa: BLE001 — an agent must never escape
            last = exc
            if not _is_transient(exc) or attempt == max_attempts:
                break
            wait = _retry_after(exc, backoff)
            logger.warning(
                "Agent %s failed (attempt %d/%d): %s — retrying in %.1fs",
                agent_name, attempt, max_attempts, type(exc).__name__, wait,
            )
            sleep(wait)
            backoff *= 2

    logger.error("Agent %s FAILED after %d attempts: %s", agent_name, max_attempts, last)
    return {
        "agent_failed": True,
        "agent_name": agent_name,
        "error": type(last).__name__ if last else "Unknown",
        "error_detail": str(last)[:200] if last else "",
    }


def agent_failed(result: Dict[str, Any]) -> bool:
    return bool(result.get("agent_failed"))
