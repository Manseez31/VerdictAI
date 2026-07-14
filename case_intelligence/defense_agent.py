"""Feature 5 — Defense Agent.

Builds the strongest LAWFUL defense: challenges the prosecution, raises
evidentiary and procedural concerns, offers alternative interpretations, and
notes mitigating circumstances. Must not fabricate evidence or suggest illegal
conduct. Educational analysis only.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def defense_view(
    case: Dict[str, str],
    analysis: Dict[str, Any],
    evidence: Dict[str, Any],
    research: Dict[str, Any],
    prosecution: Dict[str, Any],
    llm,
) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the DEFENSE agent — simulate how a competent defense lawyer would
build the strongest LAWFUL defense. Ground legal citations in the retrieved
context; keep [स्रोत: …] tags intact.

ADDITIONAL RULES FOR YOU:
- Do NOT fabricate evidence or facts.
- Do NOT suggest illegal conduct (hiding/destroying evidence, influencing
  witnesses, absconding). Only lawful defense: challenging evidence, procedure,
  interpretation, burden of proof, and raising lawful mitigation.

{c.case_block(case)}

{c.upstream("CASE ANALYSIS", analysis, 1400)}

{c.upstream("EVIDENCE ANALYSIS", evidence, 1600)}

{c.upstream("PROSECUTION VIEW", prosecution, 1800)}

RETRIEVED LEGAL CONTEXT (cite from here):
{(research.get('context') or 'No specific provisions retrieved.')[:3000]}

Return ONLY this JSON schema:
{{
  "arguments": ["defense legal argument", ...],
  "prosecution_weaknesses": ["weakness/gap in the prosecution case", ...],
  "evidentiary_concerns": ["reliability/admissibility/chain-of-custody concern", ...],
  "alternative_interpretations": ["alternative lawful reading of the facts", ...],
  "mitigating_circumstances": ["lawful mitigating factor", ...],
  "legal_references": ["law/section supporting the defense, with [स्रोत: …] where possible", ...],
  "reasoning_summary": "3-5 sentence summary of the defense theory",
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "Defense", prompt)
    result = {
        "arguments": c.str_list(raw.get("arguments")),
        "prosecution_weaknesses": c.str_list(raw.get("prosecution_weaknesses")),
        "evidentiary_concerns": c.str_list(raw.get("evidentiary_concerns")),
        "alternative_interpretations": c.str_list(raw.get("alternative_interpretations")),
        "mitigating_circumstances": c.str_list(raw.get("mitigating_circumstances")),
        "legal_references": c.str_list(raw.get("legal_references")),
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": c.str_list(raw.get("legal_references")),
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
