"""Feature 7 — Verdict Agent.

Produces the likely legal outcome, its rationale, an uncertainty analysis, and a
confidence score, based on the judge's neutral reasoning. This is an EDUCATIONAL
PREDICTION ONLY — not legal advice and not a real verdict.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def predict_verdict(
    case: Dict[str, str],
    evidence: Dict[str, Any],
    prosecution: Dict[str, Any],
    defense: Dict[str, Any],
    judge: Dict[str, Any],
    llm,
) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the VERDICT agent. Based on the judge's neutral reasoning and the
overall balance of arguments and evidence, simulate the LIKELY outcome. This is
an educational prediction, NOT a real verdict — be explicit about uncertainty
and the presumption of innocence.

{c.case_block(case)}

{c.upstream("JUDGE REASONING", judge, 2500)}

{c.upstream("EVIDENCE (overall)", {"overall_strength": evidence.get("overall_strength"), "summary": evidence.get("reasoning_summary")}, 1000)}

Choose exactly one likely_outcome: "Likely Conviction", "Uncertain Outcome", or
"Likely Acquittal".

Return ONLY this JSON schema:
{{
  "likely_outcome": "Likely Conviction|Uncertain Outcome|Likely Acquittal",
  "rationale": "why this outcome is most likely given the analysis",
  "uncertainty_analysis": "key factors that could change the outcome; what is unknown or contested",
  "key_factors": ["decisive factor", ...],
  "reasoning_summary": "3-5 sentence summary",
  "laws_referenced": ["law/principle most relevant to the outcome", ...],
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "Verdict", prompt)
    result = {
        "likely_outcome": c.enum(raw.get("likely_outcome"), c.VERDICTS, "Uncertain Outcome"),
        "rationale": c.text(raw.get("rationale"), 2500),
        "uncertainty_analysis": c.text(raw.get("uncertainty_analysis"), 2500),
        "key_factors": c.str_list(raw.get("key_factors")),
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": c.str_list(raw.get("laws_referenced")),
        "confidence": c.confidence(raw.get("confidence")),
        "disclaimer": "Educational prediction only — not legal advice.",
    }
    return c.with_parse_flag(result, raw)
