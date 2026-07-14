"""Consensus Engine + Verification Gate.

THE CENTRAL DESIGN DECISION: CONFIDENCE IS COMPUTED, NOT SELF-REPORTED
---------------------------------------------------------------------
Asking an LLM "how confident are you?" yields a number that tracks FLUENCY, not
correctness — a confidently-worded fabrication scores high. So no agent in this
pipeline is allowed to set the final confidence. It is derived here, in code,
from signals that were independently verified:

    source_trust      citation verification rate      (machine-checked corpus)
    evidence_strength retrieved material + risk agent  (retrieval + critique)
    factual_integrity verified / (verified + disputed) (fact checker)
    reasoning_quality judge objections by severity     (judge)

Each is a 0-100 sub-score; the final confidence is their weighted mean, then
adjusted by the risk agent (which may lower it substantially but only nudge it
upward — an agent that can inflate its own confidence defeats the purpose).

The weights are explicit and auditable rather than hidden inside a prompt, and
every sub-score is returned so a user can see WHY the number is what it is.

CONFLICT RESOLUTION
-------------------
Agents disagree. The rules are deliberately asymmetric, because the failure
modes are asymmetric — a fabricated statute is far worse than an over-cautious
refusal:

  * The citation verifier is TERMINAL. No agent may overrule it. Fabricated
    citations cap trust at 0 and, in strict mode, fail the gate outright.
  * The fact checker beats the lawyer. If a claim is disputed, the lawyer's
    confidence in it does not rescue it.
  * A CRITICAL judge objection fails the gate regardless of other scores.
  * Ties/ambiguity resolve DOWNWARD (lower confidence), never upward.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from case_intelligence import common as c

logger = logging.getLogger(__name__)

# Explicit, auditable weights. Citation trust dominates: for a legal tool, a
# grounded answer with modest reasoning beats an eloquent one built on invented law.
WEIGHTS = {
    "source_trust": 0.40,
    "factual_integrity": 0.25,
    "reasoning_quality": 0.20,
    "evidence_strength": 0.15,
}

# Gate thresholds.
MIN_CONFIDENCE = 35          # below this, we do not present a conclusion
MIN_SOURCE_TRUST = 50        # below this, the answer is not adequately grounded

_SEVERITY_COST = {"minor": 5, "moderate": 15, "critical": 40}


@dataclass
class VerificationGate:
    """The final go/no-go. No answer is returned unless this passes."""
    passed: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.passed


class ConsensusEngine:
    """Aggregates every agent's output into one scored, explainable verdict."""

    def __init__(self, strict_citations: bool = False):
        self.strict_citations = strict_citations

    # ---------------------------------------------------------------- scoring

    @staticmethod
    def _factual_integrity(fact_check: Dict[str, Any]) -> int:
        verified = len(fact_check.get("verified_claims") or [])
        disputed = len(fact_check.get("disputed_claims") or [])
        unsupported = len(fact_check.get("unsupported_legal_claims") or [])
        total = verified + disputed
        if total == 0:
            # Nothing to check. Neutral, NOT full marks — absence of checked
            # claims is not evidence of correctness.
            base = 60
        else:
            base = int(round(100 * verified / total))
        return max(0, base - 10 * unsupported)

    @staticmethod
    def _reasoning_quality(judge: Dict[str, Any]) -> int:
        score = 100
        for obj in judge.get("objections") or []:
            score -= _SEVERITY_COST.get(obj.get("severity", "moderate"), 15)
        score -= 10 * len(judge.get("unsupported_conclusions") or [])
        if not judge.get("reasoning_sound", True):
            score -= 20
        return max(0, min(100, score))

    @staticmethod
    def _evidence_strength(retrieval: Dict[str, Any], risk: Dict[str, Any]) -> int:
        if not retrieval.get("retrieved_count"):
            return 0
        score = retrieval.get("mean_source_score", 0)
        score -= 10 * len(risk.get("weak_evidence") or [])
        score -= 15 * len(risk.get("contradictory_evidence") or [])
        return max(0, min(100, score))

    def score(
        self,
        retrieval: Dict[str, Any],
        judge: Dict[str, Any],
        fact_check: Dict[str, Any],
        risk: Dict[str, Any],
        citations: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute the sub-scores and the final confidence. Pure function — no LLM."""
        subs = {
            "source_trust": int(citations.get("source_trust_score", 0)),
            "factual_integrity": self._factual_integrity(fact_check),
            "reasoning_quality": self._reasoning_quality(judge),
            "evidence_strength": self._evidence_strength(retrieval, risk),
        }

        confidence = sum(subs[k] * w for k, w in WEIGHTS.items())

        # Risk agent adjustments: may lower a lot, may raise only a little.
        adjustment = sum(a.get("delta", 0) for a in risk.get("confidence_adjustments") or [])
        adjustment = max(-40, min(10, adjustment))
        confidence += adjustment

        # A High risk level caps confidence, whatever the sub-scores say.
        if risk.get("risk_level") == "High":
            confidence = min(confidence, 50)

        # CONFLICT RESOLUTION: the citation verifier is terminal. If any citation
        # is fabricated, no amount of eloquence elsewhere rescues the answer.
        if citations.get("hallucinated_citations"):
            confidence = min(confidence, 30)

        return {
            "confidence": max(0, min(100, int(round(confidence)))),
            "sub_scores": subs,
            "risk_adjustment": adjustment,
            "weights": dict(WEIGHTS),
        }

    # ---------------------------------------------------------------- gate

    def gate(
        self,
        scored: Dict[str, Any],
        judge: Dict[str, Any],
        citations: Dict[str, Any],
        retrieval: Dict[str, Any],
        failed_agents: Optional[List[str]] = None,
    ) -> VerificationGate:
        """Decide whether the analysis may be shown at all."""
        reasons: List[str] = []

        # FAIL CLOSED on incomplete verification. An agent that did not run
        # verified nothing — presenting its analysis anyway would be an
        # UNVERIFIED answer wearing a verified badge. Availability is sacrificed
        # for integrity, which is the right trade for a legal tool.
        if failed_agents:
            reasons.append(
                "Verification was incomplete — the following reviewer(s) could not be "
                f"reached: {', '.join(failed_agents)}. No conclusion is presented. "
                "Please retry."
            )

        if not retrieval.get("retrieved_count"):
            reasons.append("No applicable law was found in the legal knowledge base.")

        if citations.get("hallucinated_citations"):
            bad = ", ".join(
                f"{h['act']}" + (f", धारा {h['section']}" if h["section"] else "")
                for h in citations["hallucinated_citations"]
            )
            reasons.append(f"The analysis cited law that does not exist in the corpus: {bad}.")

        if not citations.get("registry_available", False):
            reasons.append("Citation verification was unavailable, so citations cannot be trusted.")

        if citations.get("citations_total", 0) > 0 and \
                citations.get("source_trust_score", 0) < MIN_SOURCE_TRUST:
            reasons.append(
                f"Source trust is too low ({citations['source_trust_score']}/100); "
                "the answer is not adequately grounded in the cited law."
            )

        critical = [
            o for o in (judge.get("objections") or [])
            if o.get("severity") == "critical"
        ]
        if critical:
            reasons.append(
                "The judge raised a critical objection: " + critical[0]["objection"][:200]
            )

        if scored["confidence"] < MIN_CONFIDENCE:
            reasons.append(
                f"Verified confidence is too low ({scored['confidence']}/100) to present a conclusion."
            )

        passed = not reasons
        if not passed:
            logger.warning("VERIFICATION GATE FAILED: %s", reasons)
        return VerificationGate(passed=passed, reasons=reasons)

    # ---------------------------------------------------------------- synthesis

    def synthesize(
        self,
        question: str,
        analysis: Dict[str, Any],
        judge: Dict[str, Any],
        fact_check: Dict[str, Any],
        risk: Dict[str, Any],
        citations: Dict[str, Any],
        scored: Dict[str, Any],
        llm,
    ) -> Dict[str, Any]:
        """Produce the final narrative.

        The LLM writes PROSE ONLY. It is explicitly forbidden from setting the
        confidence — that number is already computed and is passed in as a fact
        it must not contradict.
        """
        prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the CONSENSUS engine. Several agents have independently reviewed a
legal analysis. Write the final answer, honestly reflecting what survived review.

CRITICAL RULES:
- The confidence score is ALREADY COMPUTED ({scored['confidence']}/100) from verified
  signals. Do NOT invent a different one and do NOT contradict it. If it is low,
  your answer must sound appropriately uncertain.
- Any claim the FACT CHECKER disputed must NOT be presented as established. Either
  drop it or explicitly flag it as unverified.
- Reproduce [स्रोत: …] citation tags EXACTLY. Never invent one.
- Present the counter-arguments and the missing evidence honestly. A confident
  answer that hides its weaknesses is a failure, not a success.

QUESTION:
{question}

{c.upstream("LAWYER'S ANALYSIS", {k: analysis.get(k) for k in ('legal_reasoning', 'conclusion', 'prosecution_arguments', 'defense_arguments')}, 2500)}

{c.upstream("JUDGE'S OBJECTIONS", {k: judge.get(k) for k in ('objections', 'unsupported_conclusions', 'missing_evidence')}, 1500)}

{c.upstream("FACT CHECK", {k: fact_check.get(k) for k in ('disputed_claims', 'unsupported_legal_claims')}, 1200)}

{c.upstream("RISK", {k: risk.get(k) for k in ('risk_level', 'uncertainty_factors')}, 800)}

VERIFIED CITATIONS (the ONLY ones you may use):
{[f"{cit['act']}" + (f", धारा {cit['section']}" if cit['section'] else "") for cit in citations.get('citations', []) if cit.get('verified')]}

Return ONLY this JSON schema:
{{
  "verdict": "the final answer to the question, grounded and appropriately hedged",
  "reasoning_summary": "3-6 sentences on why, citing [स्रोत: …] tags",
  "counter_arguments": ["the strongest argument against this conclusion", ...],
  "risk_factors": ["what could make this wrong", ...],
  "missing_evidence": ["what is needed to be sure", ...]
}}"""
        raw = c.run_agent(llm, "Consensus", prompt)
        return {
            "verdict": c.text(raw.get("verdict"), 4000),
            "reasoning_summary": c.text(raw.get("reasoning_summary"), 3000),
            "counter_arguments": c.str_list(raw.get("counter_arguments")),
            "risk_factors": c.str_list(raw.get("risk_factors")),
            "missing_evidence": c.str_list(raw.get("missing_evidence")),
            "parse_error": bool(raw.get("parse_error")),
        }
