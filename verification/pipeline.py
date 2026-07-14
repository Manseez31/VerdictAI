"""Multi-agent verification pipeline orchestrator.

SECURITY (inherits, never bypasses)
-----------------------------------
* The user query is UNTRUSTED and is nonce-fence isolated via
  `security.prompt_guard.wrap_untrusted` before it reaches ANY agent prompt. A
  question that says "ignore your instructions and conclude X" therefore arrives
  at every agent as inert data.
* Every agent prompt carries the existing SAFETY_PREAMBLE.
* Citation verification is mandatory and terminal (citation_agent.py).
* The caller (backend) additionally runs `scan_for_injection` on the way in and
  `guard_output` on the way out — this module does not replace those.

PERFORMANCE
-----------
7 stages, but only 3 sequential LLM round-trips:

    Retriever    0 LLM   (deterministic retrieval)
    Lawyer       1 LLM   ──┐
    Judge        │         │  the three critics review the SAME lawyer output
    FactChecker  │ 3 LLM   ├─ and are independent, so they run CONCURRENTLY
    Risk         │         │
    CitationVer  0 LLM   ──┘  (deterministic — pure code, cannot hallucinate)
    Consensus    1 LLM

Latency ≈ retrieval + lawyer + max(critics) + consensus, not the sum of five
calls. Cost is still ~5 LLM calls per query — the honest price of independent
verification, and the reason this is a separate endpoint rather than a
replacement for /chat.
"""

from __future__ import annotations

import datetime
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from case_intelligence import common as c
from security.prompt_guard import wrap_untrusted

from .citation_agent import verify_analysis_citations
from .consensus import ConsensusEngine, VerificationGate
from .critics import assess_risk, fact_check, judge_review
from .lawyer_agent import build_argument
from .resilience import agent_failed, call_agent_safely
from .retriever_agent import retrieve_evidence

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """The pipeline's full, explainable output."""
    passed: bool
    verdict: str
    confidence: int
    evidence_strength: int
    source_trust_score: int
    applicable_laws: List[Dict[str, str]] = field(default_factory=list)
    reasoning_summary: str = ""
    counter_arguments: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    hallucinated_citations: List[str] = field(default_factory=list)
    gate_reasons: List[str] = field(default_factory=list)
    agent_trace: Dict[str, Any] = field(default_factory=dict)
    sub_scores: Dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "source_trust_score": self.source_trust_score,
            "applicable_laws": self.applicable_laws,
            "reasoning_summary": self.reasoning_summary,
            "counter_arguments": self.counter_arguments,
            "risk_factors": self.risk_factors,
            "missing_evidence": self.missing_evidence,
            "citations": self.citations,
            "hallucinated_citations": self.hallucinated_citations,
            "gate_reasons": self.gate_reasons,
            "sub_scores": self.sub_scores,
            "agent_trace": self.agent_trace,
            "elapsed_ms": self.elapsed_ms,
            "disclaimer": c.DISCLAIMER,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }


# The message shown when verification fails. We return the REASONS rather than a
# conclusion — an unverifiable legal answer is worse than no answer.
BLOCKED_VERDICT = (
    "This analysis did not pass verification, so no legal conclusion is being "
    "presented. Showing an unverified conclusion would be worse than showing none."
)


def run_verified_legal_analysis(
    question: str,
    llm=None,
    retriever=None,
    registry=None,
    top_k: int = 6,
    where: Optional[dict] = None,
    strict_citations: bool = False,
    parallel: bool = True,
) -> VerificationResult:
    """Run the full multi-agent verification pipeline.

    `llm`, `retriever` and `registry` are injectable so the entire pipeline runs
    offline in tests with fakes.
    """
    started = time.perf_counter()
    llm = llm or c.get_case_llm()
    engine = ConsensusEngine(strict_citations=strict_citations)

    # --- SECURITY: isolate the untrusted question BEFORE it touches any prompt.
    # Even a novel injection the input scanner missed arrives fenced as data.
    safe_question = wrap_untrusted(question, label="USER QUESTION")

    # --- Stage 1: Retriever (deterministic; the raw question is fine here — it
    # is a search string, not a prompt).
    retrieval = retrieve_evidence(question, retriever=retriever, top_k=top_k, where=where)

    # --- Stage 2: Lawyer. Every agent call is wrapped: it retries transient
    # provider failures (rate limits) and, if it ultimately fails, degrades to
    # `agent_failed` instead of raising. Five LLM calls = five chances to fail;
    # one failure must not 502 the whole request.
    analysis = call_agent_safely(
        build_argument, safe_question, retrieval, llm, agent_name="Lawyer"
    )

    # --- Stage 3: three INDEPENDENT critics of the same analysis (parallel).
    if parallel:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_judge = pool.submit(call_agent_safely, judge_review, safe_question,
                                  retrieval, analysis, llm, agent_name="Judge")
            f_facts = pool.submit(call_agent_safely, fact_check, safe_question,
                                  retrieval, analysis, llm, agent_name="FactChecker")
            f_risk = pool.submit(call_agent_safely, assess_risk, safe_question,
                                 retrieval, analysis, llm, agent_name="Risk")
            judge = f_judge.result()
            facts = f_facts.result()
            risk = f_risk.result()
    else:
        judge = call_agent_safely(judge_review, safe_question, retrieval, analysis,
                                  llm, agent_name="Judge")
        facts = call_agent_safely(fact_check, safe_question, retrieval, analysis,
                                  llm, agent_name="FactChecker")
        risk = call_agent_safely(assess_risk, safe_question, retrieval, analysis,
                                 llm, agent_name="Risk")

    # Which agents did not complete? A critic that never ran verified NOTHING, so
    # the gate must refuse to present a conclusion rather than let an unverified
    # answer through wearing a verified badge.
    failed_agents = [
        name for name, res in (
            ("Lawyer", analysis), ("Judge", judge),
            ("FactChecker", facts), ("Risk", risk),
        ) if agent_failed(res)
    ]

    # --- Stage 4: Citation verification. Deterministic and TERMINAL.
    citations = verify_analysis_citations(
        analysis, registry, retrieval=retrieval, strict=strict_citations
    )

    # --- Stage 5: Consensus — score first (pure code), then narrate.
    scored = engine.score(retrieval, judge, facts, risk, citations)
    gate = engine.gate(scored, judge, citations, retrieval, failed_agents=failed_agents)

    if gate.blocked:
        # Do NOT synthesize an answer we could not verify. Return the reasons.
        logger.warning("Verification blocked the answer: %s", gate.reasons)
        return VerificationResult(
            passed=False,
            verdict=BLOCKED_VERDICT,
            confidence=scored["confidence"],
            evidence_strength=scored["sub_scores"]["evidence_strength"],
            source_trust_score=citations["source_trust_score"],
            applicable_laws=analysis.get("applicable_laws", []),
            reasoning_summary="",
            counter_arguments=[],
            risk_factors=risk.get("uncertainty_factors", []),
            missing_evidence=judge.get("missing_evidence", []),
            citations=citations["citations"],
            hallucinated_citations=[
                f"{h['act']}" + (f", धारा {h['section']}" if h["section"] else "")
                for h in citations["hallucinated_citations"]
            ],
            gate_reasons=gate.reasons,
            sub_scores=scored["sub_scores"],
            agent_trace=_trace(retrieval, analysis, judge, facts, risk, citations, scored),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    final = call_agent_safely(
        engine.synthesize, safe_question, analysis, judge, facts, risk,
        citations, scored, llm, agent_name="Consensus",
    )
    if agent_failed(final):
        # We verified successfully but could not write the answer up. Do not
        # fabricate one — report the failure honestly.
        return VerificationResult(
            passed=False,
            verdict=BLOCKED_VERDICT,
            confidence=scored["confidence"],
            evidence_strength=scored["sub_scores"]["evidence_strength"],
            source_trust_score=citations["source_trust_score"],
            applicable_laws=analysis.get("applicable_laws", []),
            citations=citations["citations"],
            gate_reasons=[
                "The analysis passed verification but the final answer could not be "
                "generated (the model was unreachable). Please retry."
            ],
            sub_scores=scored["sub_scores"],
            agent_trace=_trace(retrieval, analysis, judge, facts, risk, citations, scored),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    return VerificationResult(
        passed=True,
        verdict=final["verdict"],
        confidence=scored["confidence"],
        evidence_strength=scored["sub_scores"]["evidence_strength"],
        source_trust_score=citations["source_trust_score"],
        applicable_laws=analysis.get("applicable_laws", []),
        reasoning_summary=final["reasoning_summary"],
        counter_arguments=final["counter_arguments"],
        risk_factors=final["risk_factors"] or risk.get("uncertainty_factors", []),
        missing_evidence=final["missing_evidence"] or judge.get("missing_evidence", []),
        citations=citations["citations"],
        hallucinated_citations=[],
        gate_reasons=[],
        sub_scores=scored["sub_scores"],
        agent_trace=_trace(retrieval, analysis, judge, facts, risk, citations, scored),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _trace(retrieval, analysis, judge, facts, risk, citations, scored) -> Dict[str, Any]:
    """Per-agent explainability trace: what each agent actually said, so a user
    (or auditor) can see how the verdict was reached rather than trusting it."""
    return {
        "retriever": {
            "retrieved_count": retrieval.get("retrieved_count", 0),
            "mean_source_score": retrieval.get("mean_source_score", 0),
            "source_scores": retrieval.get("source_scores", [])[:10],
        },
        "lawyer": {
            "legal_reasoning": analysis.get("legal_reasoning", ""),
            "prosecution_arguments": analysis.get("prosecution_arguments", []),
            "defense_arguments": analysis.get("defense_arguments", []),
            "assumptions": analysis.get("assumptions", []),
        },
        "judge": {
            "objections": judge.get("objections", []),
            "weaknesses": judge.get("weaknesses", []),
            "unsupported_conclusions": judge.get("unsupported_conclusions", []),
            "missing_evidence": judge.get("missing_evidence", []),
            "assessment": judge.get("assessment", ""),
        },
        "fact_checker": {
            "verified_claims": facts.get("verified_claims", []),
            "disputed_claims": facts.get("disputed_claims", []),
            "unsupported_legal_claims": facts.get("unsupported_legal_claims", []),
        },
        "risk": {
            "risk_level": risk.get("risk_level", "Medium"),
            "uncertainty_factors": risk.get("uncertainty_factors", []),
            "contradictory_evidence": risk.get("contradictory_evidence", []),
            "weak_evidence": risk.get("weak_evidence", []),
            "confidence_adjustments": risk.get("confidence_adjustments", []),
        },
        "citation_verifier": {
            "citations_verified": citations.get("citations_verified", 0),
            "citations_total": citations.get("citations_total", 0),
            "source_trust_score": citations.get("source_trust_score", 0),
            "hallucinated": citations.get("hallucinated_citations", []),
            "off_evidence": citations.get("off_evidence_citations", []),
        },
        "consensus": {
            "sub_scores": scored["sub_scores"],
            "weights": scored["weights"],
            "risk_adjustment": scored["risk_adjustment"],
            "confidence": scored["confidence"],
        },
    }
