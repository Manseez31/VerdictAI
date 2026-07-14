"""F-3 — token/spend budget (denial-of-wallet) tests.

The threat these encode: an attacker (or a bug) stays comfortably inside the
20-requests/minute rate limit and still burns the entire LLM balance, because
requests are not the unit of cost.
"""

import pytest

from security.budget import (
    ENDPOINT_BASE_COST, BudgetExceeded, BudgetGuard, TokenAccountant, TokenBudget,
    estimate_request_cost, estimate_tokens,
)


# ===========================================================================
#  Cost estimation
# ===========================================================================

def test_estimate_tokens_is_roughly_chars_over_four():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1          # never zero — a request always costs something


def test_expensive_endpoints_cost_more_than_cheap_ones():
    """The whole point: one /case-intelligence call must not be billed the same
    as one /chat call. Requests are not the unit of cost."""
    q = "is registration required?"
    assert estimate_request_cost("/case-intelligence", q) > estimate_request_cost("/verify-legal", q)
    assert estimate_request_cost("/verify-legal", q) > estimate_request_cost("/chat", q)
    assert estimate_request_cost("/chat", q) > estimate_request_cost("/translate", q)


def test_input_is_multiplied_by_agent_count():
    """A long description is re-sent to every agent, so its cost scales."""
    short = estimate_request_cost("/case-intelligence", "x" * 100)
    long = estimate_request_cost("/case-intelligence", "x" * 10_000)
    assert long > short + 10_000          # 8 agents x ~2500 tokens of input


def test_extract_document_costs_nothing():
    """It runs no LLM — charging for it would be dishonest."""
    assert estimate_request_cost("/extract-document", "x" * 5000) == 0


def test_unknown_endpoint_still_gets_a_default_cost():
    """Fail safe: a new endpoint must not be free by omission."""
    assert estimate_request_cost("/some-new-llm-route", "q") > 0


# ===========================================================================
#  Sliding-window counter
# ===========================================================================

def test_budget_allows_within_limit():
    b = TokenBudget(max_tokens=1000, window_seconds=60)
    assert b.try_spend("k", 400)[0] is True
    assert b.try_spend("k", 400)[0] is True
    assert b.used("k") == 800


def test_budget_rejects_over_limit_and_gives_retry_after():
    b = TokenBudget(max_tokens=1000, window_seconds=60)
    b.try_spend("k", 900)
    ok, retry = b.try_spend("k", 200)
    assert ok is False
    assert retry > 0                      # tells the client when to come back


def test_budget_window_rolls_off(monkeypatch):
    b = TokenBudget(max_tokens=1000, window_seconds=60)
    now = [1000.0]
    monkeypatch.setattr("security.budget.time.monotonic", lambda: now[0])

    assert b.try_spend("k", 1000)[0] is True
    assert b.try_spend("k", 1)[0] is False       # full

    now[0] += 61                                  # window passes
    assert b.try_spend("k", 1000)[0] is True      # refilled


def test_budget_keys_are_isolated():
    b = TokenBudget(max_tokens=1000, window_seconds=60)
    b.try_spend("alice", 1000)
    assert b.try_spend("bob", 1000)[0] is True    # alice must not starve bob


# ===========================================================================
#  Guard: global + per-principal
# ===========================================================================

def test_principal_budget_bounds_one_abusive_caller():
    g = BudgetGuard(global_tokens_per_min=10_000_000, principal_tokens_per_min=10_000)
    with pytest.raises(BudgetExceeded) as exc:
        for _ in range(50):
            g.check("user:mallory", "/verify-legal", "burn the balance")
    assert exc.value.scope == "principal"
    assert exc.value.retry_after > 0


def test_principal_is_rejected_before_it_can_drain_the_global_pool():
    """Order matters: a single abusive caller must hit THEIR ceiling first, so
    they cannot take everyone else down with them."""
    g = BudgetGuard(global_tokens_per_min=100_000, principal_tokens_per_min=30_000)
    with pytest.raises(BudgetExceeded) as exc:
        for _ in range(20):
            g.check("user:mallory", "/verify-legal", "q")
    assert exc.value.scope == "principal"
    # The global pool still has room for everyone else.
    assert g.global_budget.remaining("__global__") > 0


def test_global_budget_holds_across_many_principals():
    """THE KEY PROPERTY. A proxy pool / many accounts defeats a per-IP request
    limit, but must NOT defeat the wallet guard."""
    g = BudgetGuard(global_tokens_per_min=60_000, principal_tokens_per_min=1_000_000)
    with pytest.raises(BudgetExceeded) as exc:
        for i in range(100):
            g.check(f"ip:10.0.0.{i}", "/verify-legal", "q")   # a fresh IP every time
    assert exc.value.scope == "global"


def test_budget_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("TOKEN_BUDGET_ENABLED", "false")
    g = BudgetGuard(global_tokens_per_min=1, principal_tokens_per_min=1)
    assert g.check("user:a", "/chat", "q") == 0        # no-op, no raise


def test_budget_is_enabled_by_default_and_on_typos(monkeypatch):
    """Same fail-closed lesson as F-1: a typo must not silently remove the guard."""
    monkeypatch.setenv("TOKEN_BUDGET_ENABLED", "ture")   # typo
    g = BudgetGuard(global_tokens_per_min=1000, principal_tokens_per_min=1000)
    assert g.enabled is True


# ===========================================================================
#  Measured-usage backstop (the estimator can be wrong; the meter cannot)
# ===========================================================================

class _Resp:
    def __init__(self, total):
        self.llm_output = {"token_usage": {"total_tokens": total}}


def test_accountant_books_real_tokens_to_the_measured_counter():
    g = BudgetGuard(global_tokens_per_min=1_000_000, principal_tokens_per_min=1_000_000)
    acct = TokenAccountant(g)
    acct.on_llm_end(_Resp(1234))
    assert acct.stats() == {"total_tokens": 1234, "total_calls": 1}
    assert g.actual_budget.used("__global__") == 1234


def test_estimates_and_actuals_are_not_double_counted():
    """The estimate counter and the measured counter are separate on purpose —
    booking both into one would charge every request twice."""
    g = BudgetGuard(global_tokens_per_min=100_000, principal_tokens_per_min=100_000)
    est = g.check("user:a", "/chat", "hello")
    TokenAccountant(g).on_llm_end(_Resp(9999))

    assert g.global_budget.used("__global__") == est        # estimates only
    assert g.actual_budget.used("__global__") == 9999       # measured only


def test_measured_cap_sheds_load_even_if_estimates_look_cheap(monkeypatch):
    """The estimator can be fooled; the provider's own meter cannot. If we have
    genuinely spent the cap, further requests are refused regardless of how cheap
    they claim to be."""
    monkeypatch.setenv("GLOBAL_ACTUAL_TOKEN_CAP_PER_MIN", "5000")
    g = BudgetGuard(global_tokens_per_min=10_000_000, principal_tokens_per_min=10_000_000)

    TokenAccountant(g).on_llm_end(_Resp(5000))              # cap reached, for real

    with pytest.raises(BudgetExceeded) as exc:
        g.check("user:a", "/chat", "q")
    assert exc.value.scope == "global_actual"


def test_accounting_failure_never_breaks_inference():
    """Metering is not worth taking an LLM call down for."""
    g = BudgetGuard()
    acct = TokenAccountant(g)
    acct.on_llm_end(object())          # malformed response — must not raise
    acct.on_llm_end(_Resp(None))
    assert acct.stats()["total_calls"] == 0


# ===========================================================================
#  Configuration sanity (regression: the first cut shipped a budget smaller
#  than a single request, which made /case-intelligence permanently unusable)
# ===========================================================================

def test_default_budgets_can_afford_the_most_expensive_endpoint():
    """REGRESSION. A budget smaller than one request rejects the caller before
    they can ever succeed. The default per-principal budget must fit at least
    one call to the priciest endpoint."""
    g = BudgetGuard()
    priciest = max(ENDPOINT_BASE_COST.values())
    assert g.principal_budget.max_tokens >= priciest
    assert g.global_budget.max_tokens >= priciest


def test_misconfigured_budget_is_reported_loudly(caplog):
    """The class must flag an unusable configuration rather than silently
    bricking an endpoint."""
    import logging

    with caplog.at_level(logging.ERROR):
        BudgetGuard(global_tokens_per_min=10, principal_tokens_per_min=10)
    assert any("MISCONFIGURED BUDGET" in r.message for r in caplog.records)


def test_a_single_case_intelligence_request_fits_the_default_budget():
    g = BudgetGuard()
    # Must not raise — a legitimate first request has to be able to get through.
    g.check("user:alice", "/case-intelligence", "A defrauded 240 investors. " * 20)


def test_every_llm_endpoint_is_actually_gated():
    """REGRESSION. A batch edit silently dropped the budget gate from /chat and
    /verify-legal — the two most-used LLM endpoints — and everything still
    'passed' until a behavioural test looked. Assert the wiring, not the
    intention: every endpoint with a non-zero cost must call _enforce_budget.
    """
    import inspect

    import backend

    gated = {
        "/chat": backend.chat_endpoint,
        "/translate": backend.translate_endpoint,
        "/simulate-case": backend.simulate_case_endpoint,
        "/case-intelligence": backend.case_intelligence_endpoint,
        "/verify-legal": backend.verify_legal_endpoint,
    }
    for path, fn in gated.items():
        assert ENDPOINT_BASE_COST.get(path, 1) > 0, f"{path} should have a cost"
        src = inspect.getsource(fn)
        assert "_enforce_budget(" in src, f"{path} is NOT behind the token budget"


def test_accountant_tolerates_unknown_langchain_hooks():
    """LangChain probes for many on_* hooks; a version bump must not break us."""
    acct = TokenAccountant(BudgetGuard())
    acct.on_chain_start({}, {})        # not implemented -> no-op, no AttributeError
    acct.on_llm_new_token("x")
