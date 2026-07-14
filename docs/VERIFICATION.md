# Phase 2 — Multi-Agent Legal Verification

VerdictAI no longer answers legal questions in a single pass. `POST /verify-legal`
routes a question through seven stages in which several agents **independently
review the analysis** before anything is shown to the user.

`/chat` is untouched. This is an additive endpoint, not a replacement.

## 1. Architecture

```
question
   │
   ▼
Retriever Agent      0 LLM   reuses the production hybrid retriever (dense+BM25+RRF)
   │                          → evidence, citations, source_scores (ranked 0-100)
   ▼
Lawyer Agent         1 LLM   applies statutes, argues BOTH sides, states assumptions
   │
   ├───────────────┬───────────────┐
   ▼               ▼               ▼      ← three INDEPENDENT critics of the SAME
Judge          Fact Checker      Risk        analysis, run CONCURRENTLY
   │               │               │
   └───────────────┴───────────────┘
   │
   ▼
Citation Verifier    0 LLM   deterministic; reuses security.citation_verifier
   │                          TERMINAL — no agent may overrule it
   ▼
Consensus Engine     1 LLM   scores in CODE, then writes narrative
   │
   ▼
Verification Gate            no conclusion is returned unless this passes
   │
   ▼
final response
```

### Why three critics instead of one reviewer
A single "review this" prompt produces one bland pass. Each critic has one
adversarial job and hunts a specific failure mode, which surfaces different
defects:

| Agent | Asks | Catches |
|-------|------|---------|
| **Judge** | Is the *reasoning* sound? | unsupported leaps, silent assumptions |
| **Fact Checker** | Are the *claims* in the retrieved text? | fabrication |
| **Risk** | How *uncertain* is this? | contradictions, thin evidence |

They are explicitly told that finding no problems is a valid result — a critic
that must always find fault manufactures objections.

## 2. Agent communication flow

Strictly one-directional; no agent can see or influence a peer's critique:

```
retrieval ──► lawyer ──► {judge, fact_checker, risk}  (each sees retrieval + lawyer, never each other)
                     └──► citation_verifier            (sees lawyer's citations + the corpus)
                              │
   retrieval + judge + fact_checker + risk + citations ──► consensus
```

The critics are deliberately *blind to one another* — if they could see each
other's verdicts they would converge, and three correlated opinions are worth
about one.

## 3. The central decision: confidence is COMPUTED, not self-reported

Asking an LLM "how confident are you?" yields a number that tracks **fluency, not
correctness** — a confidently-worded fabrication scores high. So **no agent sets
the final confidence.** It is derived in code from independently verified signals:

| Sub-score | Weight | Source |
|-----------|-------:|--------|
| `source_trust` | **0.40** | citation verification rate against the real corpus |
| `factual_integrity` | 0.25 | fact checker: verified / (verified + disputed) |
| `reasoning_quality` | 0.20 | judge: objections weighted by severity |
| `evidence_strength` | 0.15 | retrieval depth − risk agent's weak/contradictory findings |

Then adjusted by the Risk agent, which **may lower confidence substantially but
may only nudge it upward (max +10)** — an agent able to inflate its own
confidence defeats the entire purpose.

The weights are explicit and auditable rather than buried in a prompt, and every
sub-score is returned so a user can see *why* the number is what it is.

### Conflict resolution (deliberately asymmetric)
The failure modes are asymmetric — a fabricated statute is far worse than an
over-cautious refusal — so the rules are too:

- The **citation verifier is terminal.** Fabricated citations cap confidence at
  30 and fail the gate. No agent may overrule it.
- The **fact checker beats the lawyer.** A disputed claim is not rescued by the
  lawyer's confidence in it.
- A **critical judge objection fails the gate** regardless of other scores.
- Ambiguity resolves **downward**, never upward.

## 4. The Verification Gate — no answer unless verification succeeds

The gate blocks, and returns its *reasons* instead of a conclusion, when:

- nothing was retrieved (no applicable law)
- any citation is fabricated
- citation verification was unavailable (**fails closed**)
- source trust < 50
- the judge raised a **critical** objection
- computed confidence < 35
- **any agent failed to run** (see below)

An unverifiable legal answer is worse than no answer, so a refusal is a valid,
`HTTP 200` outcome — with `passed: false` and `gate_reasons`.

## 5. Security implications

Every existing control is inherited; none is bypassed or weakened.

- The untrusted question is **nonce-fence isolated** (`security.prompt_guard`)
  *before* it reaches any agent prompt, so an injected instruction arrives at all
  five agents as inert data. A test asserts every agent prompt contains the
  security directive.
- The endpoint runs the **same** `scan_for_injection` input gate as `/chat` — not
  a weaker one — and the same `guard_output` on the way out.
- Citation verification is **mandatory, deterministic, and non-bypassable**. It is
  code, not an agent: asking a model "is this citation real?" would reintroduce
  the very hallucination we are eliminating.
- Auth/RBAC unchanged: the endpoint requires `chat:query` like every other query
  route, and is rate-limited.

**Net security posture: strictly improved.** Five agents mean five surfaces, but
they all sit *behind* the same input gate and inside the same isolation fence, and
the pipeline adds an independent adversarial check that single-pass RAG never had.

## 6. Performance implications (the honest cost)

| | Single-pass `/chat` | Verified `/verify-legal` |
|---|---|---|
| LLM calls | ~2 | **~5** |
| Sequential LLM stages | 2 | **3** (critics fan out) |
| Measured latency | ~10–20 s | **~79 s** |

The parallel fan-out means latency ≈ retrieval + lawyer + max(critics) + consensus
rather than the sum of five calls — but this is still materially slower and ~2.5×
the token cost. **That is why it is a separate endpoint**, not a replacement for
`/chat`. Verification is for answers that must be right, not for every message.

### Rate limits are a real constraint
Five calls with large prompts will exceed a provider's tokens-per-minute quota.
Prompt budgets are trimmed to fit, and the resilience layer retries with backoff,
honouring the provider's own "try again in Ns" hint.

## 7. Resilience — found by live testing, not by theory

The first live run returned **HTTP 502**: Groq rate-limited one of the three
parallel critics, and the exception took down the whole request. Five LLM calls
means five independent chances to fail.

Two fixes, pulling in opposite directions:

1. **Retry with backoff** on transient failures (429/timeout/5xx), honouring the
   provider's retry hint. Most such failures vanish on the second attempt.

2. **Fail closed when retries are exhausted.** The obvious move — "skip the critic
   that failed and carry on" — is exactly wrong. The premise of this whole phase is
   that nothing ships unverified. A critic that never ran verified nothing, so an
   answer that skips it is **unverified while looking verified**. Instead the agent
   is recorded as `agent_failed`, the gate refuses, and the user is told
   verification was incomplete and to retry.

**Availability is traded for integrity** — the correct trade for a legal tool.

## 8. What it looks like in practice

A real run against the live system (`फार्मेसी दर्ता गर्न के के चाहिन्छ?`):

```
PASSED GATE  : true
confidence   : 61/100          ← computed, not claimed
sub_scores   : source_trust 100 | factual_integrity 60
               reasoning_quality 45 | evidence_strength 48
citations    : 5/5 verified, 0 fabricated
```

The critics *earned their keep*: the fact checker **disputed 2 of 5 claims** and
the judge raised **3 objections**, dragging confidence from a naive ~95 down to
61. A single-pass system would have presented the same answer with unearned
certainty. Here it is hedged, and the counter-arguments and missing evidence are
surfaced rather than hidden.

## 9. Response contract

```jsonc
{
  "passed": true,
  "verdict": "...",
  "confidence": 61,               // computed
  "evidence_strength": 48,
  "source_trust_score": 100,
  "applicable_laws": [...],
  "reasoning_summary": "...",
  "counter_arguments": [...],     // weaknesses surfaced, not hidden
  "risk_factors": [...],
  "missing_evidence": [...],
  "citations": [...],             // each with verified: true/false
  "hallucinated_citations": [],
  "gate_reasons": [],             // populated when passed = false
  "sub_scores": {...},            // why the confidence is what it is
  "agent_trace": {...},           // what each agent independently said
  "elapsed_ms": 78900
}
```

`agent_trace` is the explainability payload: an auditor can see each agent's
independent contribution rather than trusting the final number.

## 10. Testing

`tests/test_verification.py` — **42 tests, all offline** (LLM, retriever and
citation registry are injected fakes):

- **Agents**: retriever ranking/no-hits/broken-index, lawyer refusing to argue
  without context, critic normalization.
- **Anti-gaming**: the risk agent **cannot inflate its own confidence** (upward
  adjustments clamped to +10).
- **Consensus**: weights sum to 1; fabricated citations cap confidence; disputed
  claims sink factual integrity; "no claims checked" scores 60 (neutral), **not**
  100 — absence of checked claims is not evidence of correctness.
- **Gate**: blocks fabricated citations, critical objections, empty retrieval, low
  trust, unavailable registry.
- **Hallucination (end-to-end)**: the lawyer invents a Penal Code → no conclusion
  is presented.
- **Prompt injection**: every agent prompt contains the security directive.
- **Resilience**: retries transient errors, never raises, does not retry permanent
  errors, and a failed agent **fails the gate closed** (regression test for the
  502).
- **Parallel ≡ sequential**: the fan-out is an optimization, not a behavior change.
