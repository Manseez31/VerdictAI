"""The three independent critics: Judge, Fact Checker, Risk Assessment.

They all critique the SAME Lawyer output and do not depend on one another, so
the pipeline fans them out in parallel (see pipeline.py).

WHY THREE SEPARATE AGENTS INSTEAD OF ONE "REVIEWER"
---------------------------------------------------
A single reviewer prompt asked to do everything tends to produce one bland pass.
Each of these has one adversarial job and is told to look for a specific failure
mode, which surfaces different defects:

  Judge        — is the REASONING sound? (weak assumptions, unsupported leaps)
  Fact Checker — are the CLAIMS actually in the retrieved text? (fabrication)
  Risk         — how UNCERTAIN is this? (contradictions, thin evidence)

They are also deliberately instructed that finding no problems is a valid
result, because a critic that must always find fault manufactures objections.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from case_intelligence import common as c

logger = logging.getLogger(__name__)

RISK_LEVELS = ["Low", "Medium", "High"]
SEVERITIES = ["minor", "moderate", "critical"]


# ---------------------------------------------------------------- Judge

def judge_review(question: str, retrieval: Dict[str, Any], analysis: Dict[str, Any], llm) -> Dict[str, Any]:
    """Critique the lawyer's REASONING: weak assumptions, unsupported conclusions."""
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the JUDGE agent. You did NOT write the analysis below — your job is
to critique it as a neutral judicial officer would. Be rigorous but fair.

Look specifically for:
- conclusions that do not follow from the cited law (unsupported leaps)
- assumptions the argument silently depends on
- evidence that would be needed but is absent
- overstated certainty

IMPORTANT: If the analysis is sound, say so. Do not manufacture objections — a
critic that must always find fault is useless.

QUESTION:
{question}

RETRIEVED LEGAL CONTEXT (the only law that exists for this analysis):
{(retrieval.get('context') or 'None retrieved.')[:1800]}

{c.upstream("LAWYER'S ANALYSIS (under review)", analysis, 1800)}

Return ONLY this JSON schema:
{{
  "objections": [{{"objection": "...", "severity": "minor|moderate|critical"}}],
  "weaknesses": ["weakness in the reasoning", ...],
  "unsupported_conclusions": ["conclusion not supported by the cited law", ...],
  "missing_evidence": ["evidence that would be needed to settle this", ...],
  "reasoning_sound": true or false,
  "assessment": "2-4 sentence neutral assessment"
}}"""
    raw = c.run_agent(llm, "Judge", prompt)

    objections = []
    if isinstance(raw.get("objections"), list):
        for o in raw["objections"][:20]:
            if isinstance(o, dict) and c.text(o.get("objection")):
                objections.append({
                    "objection": c.text(o.get("objection"), 800),
                    "severity": c.enum(o.get("severity"), SEVERITIES, "moderate"),
                })
            elif isinstance(o, str) and o.strip():
                objections.append({"objection": o.strip()[:800], "severity": "moderate"})

    result = {
        "objections": objections,
        "weaknesses": c.str_list(raw.get("weaknesses")),
        "unsupported_conclusions": c.str_list(raw.get("unsupported_conclusions")),
        "missing_evidence": c.str_list(raw.get("missing_evidence")),
        "reasoning_sound": bool(raw.get("reasoning_sound", True)),
        "assessment": c.text(raw.get("assessment"), 1500),
    }
    return c.with_parse_flag(result, raw)


# ---------------------------------------------------------------- Fact Checker

def fact_check(question: str, retrieval: Dict[str, Any], analysis: Dict[str, Any], llm) -> Dict[str, Any]:
    """Verify every claim against the retrieved text. This is the anti-fabrication
    critic: a claim not present in the context is DISPUTED, not merely 'uncited'."""
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the FACT CHECKER agent. For each substantive claim in the analysis
below, decide whether the RETRIEVED LEGAL TEXT actually supports it.

Rules:
- A claim is VERIFIED only if the retrieved text supports it. Your own legal
  knowledge is NOT evidence — if it is not in the retrieved text, it is not
  verified here.
- A claim that contradicts the retrieved text, or that asserts law not present in
  it, is DISPUTED.
- Quote the supporting text (briefly) for each verified claim.

QUESTION:
{question}

RETRIEVED LEGAL TEXT (the ONLY ground truth):
{(retrieval.get('context') or 'None retrieved.')[:2200]}

{c.upstream("ANALYSIS TO FACT-CHECK", analysis, 1600)}

Return ONLY this JSON schema:
{{
  "verified_claims": [{{"claim": "...", "support": "brief quote/reference from the retrieved text"}}],
  "disputed_claims": [{{"claim": "...", "reason": "why the retrieved text does not support it"}}],
  "unsupported_legal_claims": ["legal assertion with no basis in the retrieved text", ...],
  "assessment": "1-3 sentence summary"
}}"""
    raw = c.run_agent(llm, "FactChecker", prompt)

    result = {
        "verified_claims": c.dict_list(
            raw.get("verified_claims"), {"claim": "claim", "support": "support"}, 30
        ),
        "disputed_claims": c.dict_list(
            raw.get("disputed_claims"), {"claim": "claim", "reason": "reason"}, 30
        ),
        "unsupported_legal_claims": c.str_list(raw.get("unsupported_legal_claims")),
        "assessment": c.text(raw.get("assessment"), 1200),
    }
    return c.with_parse_flag(result, raw)


# ---------------------------------------------------------------- Risk

def assess_risk(question: str, retrieval: Dict[str, Any], analysis: Dict[str, Any], llm) -> Dict[str, Any]:
    """Detect uncertainty, contradictory evidence and thin evidence."""
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the RISK ASSESSMENT agent. Judge how RELIABLE this analysis is —
not whether you agree with it.

Look for:
- uncertainty (the law is ambiguous, or the question needs facts we do not have)
- contradictory evidence (retrieved provisions that pull in different directions)
- weak/thin evidence (a conclusion resting on very little retrieved material)

QUESTION:
{question}

RETRIEVED MATERIAL: {retrieval.get('retrieved_count', 0)} chunks,
mean source score {retrieval.get('mean_source_score', 0)}/100.

{c.upstream("ANALYSIS UNDER ASSESSMENT", analysis, 1200)}

Return ONLY this JSON schema:
{{
  "risk_level": "Low|Medium|High",
  "uncertainty_factors": ["source of uncertainty", ...],
  "contradictory_evidence": ["provisions/facts that conflict", ...],
  "weak_evidence": ["conclusion resting on thin support", ...],
  "confidence_adjustments": [{{"reason": "...", "delta": -30 to 10}}],
  "assessment": "1-3 sentence summary"
}}"""
    raw = c.run_agent(llm, "RiskAssessment", prompt)

    adjustments = []
    if isinstance(raw.get("confidence_adjustments"), list):
        for a in raw["confidence_adjustments"][:10]:
            if not isinstance(a, dict):
                continue
            try:
                delta = int(round(float(a.get("delta", 0))))
            except (TypeError, ValueError):
                delta = 0
            # Clamp hard: the risk agent may LOWER confidence substantially but
            # may only nudge it upward. An agent that can inflate its own
            # confidence is exactly the failure mode this pipeline exists to stop.
            delta = max(-30, min(10, delta))
            adjustments.append({"reason": c.text(a.get("reason"), 400), "delta": delta})

    result = {
        "risk_level": c.enum(raw.get("risk_level"), RISK_LEVELS, "Medium"),
        "uncertainty_factors": c.str_list(raw.get("uncertainty_factors")),
        "contradictory_evidence": c.str_list(raw.get("contradictory_evidence")),
        "weak_evidence": c.str_list(raw.get("weak_evidence")),
        "confidence_adjustments": adjustments,
        "assessment": c.text(raw.get("assessment"), 1200),
    }
    return c.with_parse_flag(result, raw)
