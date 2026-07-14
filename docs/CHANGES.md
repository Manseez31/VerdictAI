# Changes

Audit-driven security and quality work. Findings (F-n) refer to the Phase 0
audit; severities are from that report.

---

## F-1 — Authentication is now SECURE BY DEFAULT ⚠️ BREAKING

**Severity: Critical.** Fixes an unauthenticated denial-of-wallet.

### What was wrong
`AUTH_REQUIRED` defaulted to `false` (`auth/deps.py`), and it was not set in
`.env`. The RBAC guards on `/chat`, `/case-intelligence`, `/verify-legal`,
`/simulate-case` and `/extract-document` were therefore **no-ops in practice**:
anyone who could reach the port could spend the project's Groq balance.
`/verify-legal` alone is ~5 LLM calls per request.

Every other control in this codebase fails closed. This one failed open.

### What changed
1. **`AUTH_REQUIRED` now defaults to `true`.** Opening the API is a deliberate
   act; it can no longer happen by omission.
2. **The check is now an explicit opt-*out*, not an opt-*in*.** Previously the
   code asked "is this value in `{1, true, yes, on}`?", which meant a typo —
   `AUTH_REQUIRED=ture` — silently disabled authentication. It now asks "is this
   an explicit `{0, false, no, off}`?", so **unrecognized values fail closed**.
   (This bug was caught by the new parametrized test, not by inspection.)
3. **Startup fails hard if auth is enforced without a `JWT_SECRET`.** The token
   layer would otherwise mint an ephemeral per-process key, silently invalidating
   every session on restart — a security control that *appears* to work but
   doesn't, which is worse than one that is plainly off.
4. **Startup logs `CRITICAL` when auth is disabled**, naming the exposed
   endpoints. Open mode is legitimate for local dev but must never be silent.

### 🔧 Migration — action required

**API/behaviour change:** endpoints that previously answered anonymously now
return **`401 Unauthorized`** without a valid token. Request/response *shapes*
are unchanged.

To run the app you must now either:

```bash
# Recommended (and now the default): enforce auth
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
echo "AUTH_REQUIRED=true"      >> .env
echo "JWT_SECRET=$JWT_SECRET"  >> .env
# then register the first user at /login — it bootstraps as Admin
```

```bash
# Local development only — this makes every LLM endpoint public
echo "AUTH_REQUIRED=false" >> .env
```

The app **will refuse to start** if auth is enforced and `JWT_SECRET` is unset.
That is intentional.

### Tests
`236 passed` (was 228; +8).

The offline suite exercises the RAG/agent surface rather than auth, so
`tests/conftest.py` now sets `AUTH_REQUIRED=false` **explicitly**. That is a
deliberate, visible opt-in for the test environment — not a reliance on a
permissive default. New guards:

- `test_auth_secure_by_default` — asserts the *production* default is closed.
- `test_auth_only_opens_on_explicit_optout` — asserts typos/garbage values fail
  **closed**.

### Residual risk
Auth being enforced does not by itself bound spend: an authenticated user can
still burn the LLM budget, and the only remaining limiter is a per-IP rate cap
that a proxy pool defeats. That is **F-3 (global token budget)**, addressed next.

---

## F-3 — Token/spend budget (denial-of-wallet)

**Severity: High.** No API contract change; adds `429` / `503` responses.

### What was wrong
The only spend control was a per-IP limiter counting **requests** (20/min). Two
holes:

1. **Keyed on client IP** — a proxy pool defeats it for pocket change.
2. **Requests are not the unit of cost.** One `/case-intelligence` call is 8
   agents (~40k tokens); one `/chat` call is ~4k. Billing them the same is like
   charging a lorry and a bicycle the same toll. An attacker could sit
   comfortably inside "20 requests/minute" and still drain the Groq balance.

### What changed
A budget denominated in **tokens** (`security/budget.py`), enforced pre-flight on
every LLM endpoint (`/chat`, `/translate`, `/simulate-case`, `/case-intelligence`,
`/verify-legal`; `/extract-document` runs no LLM and is correctly free).

Three counters, deliberately **not** merged:

| Counter | Unit | Purpose |
|---|---|---|
| per-principal (est.) | reserved estimate | bounds one abusive caller; checked **first**, so they hit their own ceiling instead of draining the shared pool |
| global (est.) | reserved estimate | bounds concurrent bursts *before* money is spent |
| global (measured) | provider's real token counts | the true wallet backstop — an estimator can be fooled, the provider's meter cannot |

Merging the estimate and measured counters would **double-charge every request**,
so they are separate ceilings and a request is denied if either trips.

The budget key is the **authenticated user id** when available, falling back to IP.
A user id cannot be cheaply rotated; an IP can — which is precisely why F-1 matters.

**Fails closed:** if the budget cannot be evaluated, the request is refused (`503`).
A wallet guard that fails open is not a guard. `TOKEN_BUDGET_ENABLED` needs an
explicit `false`/`0`/`no`/`off` to disable — a typo fails **closed**, same lesson as F-1.

### Three bugs the tests caught (all would have shipped)
1. **A budget smaller than one request.** The first cut set the per-principal
   budget to 30k/min while one `/case-intelligence` request estimates ~40k —
   that endpoint was *dead on arrival*. Defaults raised (120k principal /
   600k global), and `BudgetGuard` now logs `MISCONFIGURED BUDGET` at startup if
   any endpoint costs more than the budget allows.
2. **`/chat` masked the rejection.** Its blanket `except Exception` swallowed the
   `HTTPException` and returned `200 ok:false` — a spend rejection disguised as an
   ordinary model failure, which a client would happily retry. `HTTPException` is
   now re-raised.
3. **The gate was silently missing from `/chat` and `/verify-legal`.** A batch edit
   aborted after mutating its buffer but before writing, so the two most-used LLM
   endpoints had no budget gate while everything still "passed". There is now a
   test that asserts *every* costed endpoint actually calls `_enforce_budget` —
   asserting the wiring, not the intention.

### Configuration
| Variable | Default | Meaning |
|---|---:|---|
| `TOKEN_BUDGET_ENABLED` | `true` | Explicit opt-out only |
| `PRINCIPAL_TOKEN_BUDGET_PER_MIN` | `120000` | ≈2 `/case-intelligence`, 4 `/verify-legal`, or 29 `/chat` per user |
| `GLOBAL_TOKEN_BUDGET_PER_MIN` | `600000` | Estimate-based global ceiling |
| `GLOBAL_ACTUAL_TOKEN_CAP_PER_MIN` | = global | Measured backstop |

Live spend posture is exposed at `GET /health` → `budget`.

### Verified
A simulated **proxy-pool attack** — 100 rotating IPs, each staying inside the
20 req/min rate limit — is **blocked after 23 requests** by the global token
budget. The per-IP limiter alone would have allowed all of them.

### Known limitation (stated, not hidden)
Measured usage is booked **globally**, not per-principal. Attributing a token to
a principal from inside a LangChain callback needs request context inside the
agent worker threads (`ThreadPoolExecutor` does not carry contextvars), and a
half-working attribution is worse than a clearly-scoped one. The global measured
cap is what protects the wallet; per-principal fairness remains estimate-based,
which is sufficient to bound one abusive caller.

### Tests
`263 passed` (was 236; +27).
