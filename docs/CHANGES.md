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
