"""Feature 6 — Judge Agent.

A neutral judicial officer that evaluates the prosecution and defense arguments,
the quality of the evidence, and the quality of the citations, then produces
balanced legal reasoning and findings. It does NOT predict the outcome — that is
the Verdict agent's job.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def judge_case(
    case: Dict[str, str],
    analysis: Dict[str, Any],
    evidence: Dict[str, Any],
    research: Dict[str, Any],
    prosecution: Dict[str, Any],
    defense: Dict[str, Any],
    llm,
) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the JUDGE agent — a neutral judicial officer. Weigh both sides
fairly, assess the strength of the evidence and the quality/grounding of the
citations, and give balanced reasoning and findings. Respect the presumption of
innocence. Do NOT state a final verdict here.

{c.case_block(case)}

{c.upstream("EVIDENCE ANALYSIS", evidence, 1600)}

{c.upstream("PROSECUTION VIEW", prosecution, 2000)}

{c.upstream("DEFENSE VIEW", defense, 2000)}

APPLICABLE LAW (from legal research):
{(research.get('research_summary') or 'No specific provisions retrieved.')[:1500]}

Return ONLY this JSON schema:
{{
  "legal_reasoning": "balanced judicial reasoning paragraph(s)",
  "findings": ["specific neutral finding", ...],
  "prosecution_assessment": "how strong the prosecution case is and why",
  "defense_assessment": "how strong the defense is and why",
  "evidence_quality": "Strong|Moderate|Weak",
  "citation_quality": "assessment of how well arguments are grounded in cited law",
  "reasoning_summary": "3-5 sentence neutral summary",
  "laws_referenced": ["law/principle relied on", ...],
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "Judge", prompt)
    result = {
        "legal_reasoning": c.text(raw.get("legal_reasoning"), 6000),
        "findings": c.str_list(raw.get("findings")),
        "prosecution_assessment": c.text(raw.get("prosecution_assessment"), 1500),
        "defense_assessment": c.text(raw.get("defense_assessment"), 1500),
        "evidence_quality": c.enum(raw.get("evidence_quality"), c.STRENGTHS, "Moderate"),
        "citation_quality": c.text(raw.get("citation_quality"), 1200),
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": c.str_list(raw.get("laws_referenced")),
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
