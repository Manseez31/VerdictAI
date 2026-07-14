"""Phase 2 — Multi-Agent Legal Verification.

Replaces single-pass legal reasoning with a pipeline in which several agents
INDEPENDENTLY review the analysis before anything is returned to the user.

    query
      │
      ▼
    Retriever Agent      (no LLM — reuses the production hybrid retriever)
      │  evidence, citations, source_scores
      ▼
    Lawyer Agent         (1 LLM call — builds the argument)
      │  legal_reasoning, prosecution_arguments, defense_arguments
      ├──────────────┬──────────────┐
      ▼              ▼              ▼     ← run in PARALLEL: three independent
    Judge        Fact Checker     Risk       critics of the SAME lawyer output
      │              │              │
      └──────────────┴──────────────┘
      │
      ▼
    Citation Verifier    (no LLM — deterministic, reuses security.citation_verifier)
      │
      ▼
    Consensus Engine     (deterministic scoring + 1 LLM call for narrative)
      │
      ▼
    Verification Gate    ← no answer is returned unless verification passes
      │
      ▼
    Final Response

TWO DESIGN DECISIONS WORTH KNOWING
----------------------------------
1. **Confidence is COMPUTED, not self-reported.** Asking an LLM "how confident
   are you?" produces a badly-calibrated number that rises with fluency, not
   with correctness. Here, confidence is derived deterministically from
   verifiable signals (citation verification rate, fact-check ratio, judge
   objections, evidence strength). The LLM writes narrative; the machine does
   the scoring. See consensus.py.

2. **The critics run in parallel.** Judge, Fact Checker and Risk all critique
   the SAME lawyer output and do not depend on each other, so they fan out
   concurrently. That is 3 sequential LLM stages instead of 5.

SECURITY
--------
Every agent inherits the existing controls — none are bypassed or weakened:
  * the untrusted query is nonce-fence isolated (security.prompt_guard)
  * every agent prompt carries the safety preamble (case_intelligence.common)
  * citation verification against the real corpus is MANDATORY and terminal
  * the final answer passes through the existing output guard
"""

from .pipeline import run_verified_legal_analysis, VerificationResult
from .consensus import ConsensusEngine, VerificationGate

__all__ = [
    "run_verified_legal_analysis",
    "VerificationResult",
    "ConsensusEngine",
    "VerificationGate",
]
