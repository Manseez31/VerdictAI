"""Token/spend budget — denial-of-wallet defense (F-3).

WHY THE EXISTING RATE LIMIT IS NOT ENOUGH
-----------------------------------------
The per-IP limiter counts REQUESTS (20/min). Two problems:

  1. It is keyed on client IP, which a proxy pool defeats for a few dollars.
  2. Requests are not the unit of cost. One /verify-legal call is ~5 LLM calls
     with large prompts; one /chat call is ~2 small ones. Counting them the same
     is like billing a lorry and a bicycle the same toll.

So an attacker (or a bug, or an enthusiastic user) can stay comfortably inside
"20 requests a minute" and still burn the entire Groq balance.

WHAT THIS ADDS
--------------
A budget denominated in TOKENS, enforced on two axes:

  * GLOBAL   — protects the wallet. This is the ceiling that actually matters:
               it holds no matter how many IPs, accounts or proxies are used.
  * PER-PRINCIPAL — protects other users from any single one, and bounds the
               damage from one compromised account.

Enforcement is PRE-FLIGHT: we estimate a request's cost and reserve it before
any LLM is called, because a budget checked afterwards has already been spent.
Actual usage is then reconciled from the provider's own token counts (see
TokenAccountant), so the estimate self-corrects rather than drifting.

FAILS CLOSED. If the budget cannot be evaluated, the request is denied. A
wallet-protection control that fails open is not a control.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Raised when a request would breach a token budget."""

    def __init__(self, message: str, scope: str, retry_after: int):
        super().__init__(message)
        self.scope = scope              # 'global' | 'principal'
        self.retry_after = retry_after  # seconds


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """~4 characters per token. Crude, but the point is a conservative BOUND, not
    an accurate meter — the accurate figure arrives afterwards from the provider
    and is reconciled in."""
    return max(1, len(text or "") // 4)


# Baseline cost of one call to each endpoint: the prompt scaffolding, retrieved
# context, and expected completion — i.e. everything the caller's input does NOT
# account for. Measured roughly from live runs; deliberately rounded UP, because
# an underestimate is a budget leak.
ENDPOINT_BASE_COST: Dict[str, int] = {
    "/chat": 4_000,               # ~2 LLM calls (generate + judge) + context
    "/translate": 2_500,          # 1 call, text in/out
    "/simulate-case": 20_000,     # 5 agents
    "/case-intelligence": 40_000,  # 8 agents + RAG research
    "/verify-legal": 25_000,      # 5 agents (lawyer + 3 critics + consensus)
    "/extract-document": 0,       # no LLM at all — parsing only
}

# The caller's own text is sent to (roughly) every agent, so it is multiplied.
ENDPOINT_INPUT_MULTIPLIER: Dict[str, int] = {
    "/chat": 2,
    "/translate": 2,
    "/simulate-case": 5,
    "/case-intelligence": 8,
    "/verify-legal": 5,
    "/extract-document": 0,
}


def estimate_request_cost(path: str, user_input: str) -> int:
    """Conservative pre-flight estimate of a request's token cost."""
    base = ENDPOINT_BASE_COST.get(path, 4_000)
    mult = ENDPOINT_INPUT_MULTIPLIER.get(path, 2)
    return base + estimate_tokens(user_input) * mult


# ---------------------------------------------------------------------------
# Sliding-window token counter
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    at: float
    tokens: int


class TokenBudget:
    """Thread-safe sliding-window token counter."""

    def __init__(self, max_tokens: int, window_seconds: float):
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds
        self._events: Dict[str, Deque[_Entry]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> int:
        events = self._events[key]
        while events and now - events[0].at > self.window_seconds:
            events.popleft()
        return sum(e.tokens for e in events)

    def used(self, key: str) -> int:
        with self._lock:
            return self._prune(key, time.monotonic())

    def remaining(self, key: str) -> int:
        return max(0, self.max_tokens - self.used(key))

    def try_spend(self, key: str, tokens: int) -> Tuple[bool, int]:
        """Reserve `tokens` if they fit. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            used = self._prune(key, now)
            if used + tokens > self.max_tokens:
                events = self._events[key]
                # How long until enough of the window rolls off to fit this?
                if events:
                    retry = int(self.window_seconds - (now - events[0].at)) + 1
                else:
                    retry = int(self.window_seconds)
                return False, max(1, retry)
            self._events[key].append(_Entry(at=now, tokens=tokens))
            return True, 0

    def record(self, key: str, tokens: int) -> None:
        """Record actual usage (reconciliation). Never rejects — the spend has
        already happened; we are correcting the books."""
        if tokens <= 0:
            return
        with self._lock:
            self._events[key].append(_Entry(at=time.monotonic(), tokens=tokens))

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


# ---------------------------------------------------------------------------
# Guard: global + per-principal
# ---------------------------------------------------------------------------

GLOBAL_KEY = "__global__"


class BudgetGuard:
    """Enforces a global and a per-principal token budget."""

    def __init__(
        self,
        global_tokens_per_min: Optional[int] = None,
        principal_tokens_per_min: Optional[int] = None,
        window_seconds: float = 60.0,
    ):
        self.global_budget = TokenBudget(
            int(global_tokens_per_min if global_tokens_per_min is not None
                else os.getenv("GLOBAL_TOKEN_BUDGET_PER_MIN", "600000")),
            window_seconds,
        )
        self.principal_budget = TokenBudget(
            int(principal_tokens_per_min if principal_tokens_per_min is not None
                else os.getenv("PRINCIPAL_TOKEN_BUDGET_PER_MIN", "120000")),
            window_seconds,
        )

        # TWO SEPARATE COUNTERS, deliberately not merged:
        #
        #   *_budget       counts pre-flight ESTIMATES (reserved before the call)
        #   actual_budget  counts the provider's MEASURED tokens (after the call)
        #
        # Booking both into one counter would double-charge every request. Keeping
        # them separate gives two independent ceilings, and the request is denied
        # if EITHER trips: the estimate guard bounds concurrent bursts before any
        # money is spent, and the actual guard is the true wallet backstop that
        # cannot be fooled by an estimator blind spot.
        self.actual_budget = TokenBudget(
            int(os.getenv("GLOBAL_ACTUAL_TOKEN_CAP_PER_MIN",
                          str(self.global_budget.max_tokens))),
            window_seconds,
        )

        self.enabled = os.getenv("TOKEN_BUDGET_ENABLED", "true").strip().lower() \
            not in {"0", "false", "no", "off"}
        logger.info(
            "Token budget: global(est)=%d/min principal(est)=%d/min actual(measured)=%d/min enabled=%s",
            self.global_budget.max_tokens, self.principal_budget.max_tokens,
            self.actual_budget.max_tokens, self.enabled,
        )
        self._warn_if_unusable()

    def _warn_if_unusable(self) -> None:
        """A budget smaller than a single request makes that endpoint permanently
        unusable — the caller is rejected before they can ever succeed.

        This is not hypothetical: the first cut of this class shipped a 30k/min
        principal budget while one /case-intelligence request estimates ~40k, so
        that endpoint was dead on arrival. A test caught it; this check makes the
        class catch it for the next person who tunes the numbers.
        """
        if not self.enabled:
            return
        for path, base in ENDPOINT_BASE_COST.items():
            if base <= 0:
                continue
            if base > self.principal_budget.max_tokens:
                logger.error(
                    "MISCONFIGURED BUDGET: one %s request costs ~%d tokens but the "
                    "per-principal budget is only %d/min — that endpoint can NEVER "
                    "succeed. Raise PRINCIPAL_TOKEN_BUDGET_PER_MIN.",
                    path, base, self.principal_budget.max_tokens,
                )
            if base > self.global_budget.max_tokens:
                logger.error(
                    "MISCONFIGURED BUDGET: one %s request costs ~%d tokens but the "
                    "global budget is only %d/min — that endpoint can NEVER succeed.",
                    path, base, self.global_budget.max_tokens,
                )

    def check(self, principal: str, path: str, user_input: str) -> int:
        """Reserve the estimated cost of this request, or raise BudgetExceeded.

        Order matters: the PRINCIPAL is checked first, so that one abusive caller
        is rejected on their own budget rather than being allowed to consume the
        global pool and take everyone else down with them.
        """
        if not self.enabled:
            return 0

        estimated = estimate_request_cost(path, user_input)
        if estimated <= 0:
            return 0

        ok, retry = self.principal_budget.try_spend(principal, estimated)
        if not ok:
            logger.warning(
                "Token budget EXCEEDED (principal=%s path=%s est=%d used=%d/%d)",
                principal, path, estimated,
                self.principal_budget.used(principal), self.principal_budget.max_tokens,
            )
            raise BudgetExceeded(
                "You have exhausted your token budget for this period. "
                f"Please retry in about {retry}s.",
                scope="principal", retry_after=retry,
            )

        # The MEASURED backstop. Checked before reserving the estimate, because
        # if we have genuinely already spent the cap, no further estimate should
        # be admitted regardless of how cheap it claims to be.
        actual_used = self.actual_budget.used(GLOBAL_KEY)
        if actual_used >= self.actual_budget.max_tokens:
            logger.error(
                "MEASURED token cap EXCEEDED (%d/%d in window) — shedding load to "
                "protect the LLM spend cap.",
                actual_used, self.actual_budget.max_tokens,
            )
            raise BudgetExceeded(
                "The service is at capacity (spend cap reached). Please retry shortly.",
                scope="global_actual", retry_after=int(self.actual_budget.window_seconds),
            )

        ok, retry = self.global_budget.try_spend(GLOBAL_KEY, estimated)
        if not ok:
            logger.error(
                "GLOBAL token budget EXCEEDED (path=%s est=%d used=%d/%d) — "
                "the service is shedding load to protect the LLM spend cap.",
                path, estimated,
                self.global_budget.used(GLOBAL_KEY), self.global_budget.max_tokens,
            )
            raise BudgetExceeded(
                "The service is at capacity (global token budget reached). "
                f"Please retry in about {retry}s.",
                scope="global", retry_after=retry,
            )

        return estimated

    def snapshot(self) -> Dict[str, int]:
        return {
            "global_estimated_used": self.global_budget.used(GLOBAL_KEY),
            "global_estimated_limit": self.global_budget.max_tokens,
            "global_actual_used": self.actual_budget.used(GLOBAL_KEY),
            "global_actual_limit": self.actual_budget.max_tokens,
            "principal_limit": self.principal_budget.max_tokens,
            "enabled": int(self.enabled),
        }


# ---------------------------------------------------------------------------
# Actual-usage accounting (closes the loop on the estimate)
# ---------------------------------------------------------------------------

class TokenAccountant:
    """LangChain callback that books the provider's REAL token counts.

    Attached to every ChatGroq instance, so it sees each call regardless of which
    agent made it. Without this, the budget would only ever know its own guesses
    and would drift — an estimator that is never corrected is a guess with a
    spreadsheet.

    It books into `guard.actual_budget` — a SEPARATE counter from the pre-flight
    estimate reservations, so a request is never charged twice.

    SCOPE (an honest limitation): measured usage is booked globally, not
    per-principal. Attributing a token to a principal from inside a callback
    would require propagating request context into the agent worker threads
    (ThreadPoolExecutor does not carry contextvars), and a half-working
    attribution is worse than a clearly-scoped one. The global measured cap is
    the control that actually protects the wallet; per-principal fairness remains
    estimate-based, which is sufficient to bound one abusive caller.

    Never raises: a metering failure must not take down an LLM call.
    """

    def __init__(self, guard: "BudgetGuard"):
        self.guard = guard
        self.total_tokens = 0
        self.total_calls = 0
        self._lock = threading.Lock()

    # -- LangChain BaseCallbackHandler surface (duck-typed; no base class import
    #    so this module stays importable without langchain installed) ----------

    @property
    def ignore_llm(self) -> bool:
        return False

    def __getattr__(self, name: str):
        # LangChain probes for many on_* hooks. Everything we don't implement is
        # a no-op, so a version bump cannot break us.
        if name.startswith("on_") or name.startswith("ignore_"):
            return lambda *a, **k: None
        raise AttributeError(name)

    def on_llm_end(self, response, **kwargs) -> None:
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            total = int(usage.get("total_tokens") or 0)
            if not total:
                return
            with self._lock:
                self.total_tokens += total
                self.total_calls += 1
            # Book the real cost against the MEASURED counter (never the estimate
            # counter — that would double-charge the request).
            self.guard.actual_budget.record(GLOBAL_KEY, total)
            logger.debug(
                "LLM call: %d tokens (measured window=%d/%d)", total,
                self.guard.actual_budget.used(GLOBAL_KEY),
                self.guard.actual_budget.max_tokens,
            )
        except Exception:  # noqa: BLE001 — metering must never break inference
            logger.exception("Token accounting failed (ignored)")

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"total_tokens": self.total_tokens, "total_calls": self.total_calls}
