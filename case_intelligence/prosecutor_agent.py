"""Feature 4 — Prosecutor Agent.

Builds the strongest LAWFUL prosecution case, grounded in the research agent's
retrieved citations. Educational analysis only.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def prosecution_view(
    case: Dict[str, str],
    analysis: Dict[str, Any],
    evidence: Dict[str, Any],
    research: Dict[str, Any],
    llm,
) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the PROSECUTOR agent — simulate how a competent prosecutor would
build the strongest LAWFUL case. Ground your legal citations in the retrieved
context; keep [स्रोत: …] tags intact. Treat allegations as unproven and argue
what the evidence, IF accepted, would establish.

{c.case_block(case)}

{c.upstream("CASE ANALYSIS", analysis, 1500)}

{c.upstream("EVIDENCE ANALYSIS", evidence, 1800)}

RETRIEVED LEGAL CONTEXT (cite from here):
{(research.get('context') or 'No specific provisions retrieved.')[:3500]}

Return ONLY this JSON schema:
{{
  "arguments": ["prosecution legal argument", ...],
  "evidence_based_arguments": ["argument tied to a specific evidence item", ...],
  "legal_references": ["applicable law/section with [स्रोत: …] tag where possible", ...],
  "aggravating_factors": ["factor that would aggravate the offence", ...],
  "why_charges_apply": "short explanation connecting facts + law to the charges",
  "reasoning_summary": "3-5 sentence summary of the prosecution theory",
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "Prosecutor", prompt)
    result = {
        "arguments": c.str_list(raw.get("arguments")),
        "evidence_based_arguments": c.str_list(raw.get("evidence_based_arguments")),
        "legal_references": c.str_list(raw.get("legal_references")),
        "aggravating_factors": c.str_list(raw.get("aggravating_factors")),
        "why_charges_apply": c.text(raw.get("why_charges_apply")),
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": c.str_list(raw.get("legal_references")),
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
