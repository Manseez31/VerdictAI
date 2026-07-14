"""Feature 2 — Evidence Analyzer Agent.

Classifies each evidence item as Strong / Moderate / Weak, and flags Missing
evidence that would strengthen or resolve the case. Explains reasoning and
assigns confidence scores.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def analyze_evidence(case: Dict[str, str], analysis: Dict[str, Any], llm) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the EVIDENCE ANALYZER — a neutral forensic/evidentiary expert.
Assess the reliability and weight of the evidence. Do not fabricate evidence;
you MAY identify evidence that is missing but would be relevant.

{c.case_block(case)}

{c.upstream("CASE ANALYSIS", analysis)}

For each evidence item, classify strength as Strong, Moderate, or Weak and
explain why (consider witness reliability, document quality, digital-evidence
quality, and chain-of-custody concerns). Separately list Missing evidence that
would be needed to prove or disprove the allegations.

Return ONLY this JSON schema:
{{
  "items": [
    {{"evidence": "...", "classification": "Strong|Moderate|Weak", "reasoning": "...", "confidence": 0-100}}
  ],
  "missing_evidence": ["evidence not present that would be legally relevant", ...],
  "witness_reliability": "assessment (or 'No witnesses described')",
  "document_quality": "assessment (or 'No documents described')",
  "digital_evidence_quality": "assessment (or 'No digital evidence described')",
  "chain_of_custody_concerns": ["concern", ...],
  "overall_strength": "Strong|Moderate|Weak",
  "reasoning_summary": "3-5 sentence summary of the evidentiary picture",
  "laws_referenced": ["evidence-law principle applied", ...],
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "EvidenceAnalyzer", prompt)

    items = []
    if isinstance(raw.get("items"), list):
        for item in raw["items"][:25]:
            if not isinstance(item, dict):
                continue
            evidence = c.text(item.get("evidence"), 600)
            if not evidence:
                continue
            items.append({
                "evidence": evidence,
                "classification": c.enum(item.get("classification"), c.STRENGTHS, "Moderate"),
                "reasoning": c.text(item.get("reasoning"), 900),
                "confidence": c.confidence(item.get("confidence")),
            })

    result = {
        "items": items,
        "missing_evidence": c.str_list(raw.get("missing_evidence")),
        "witness_reliability": c.text(raw.get("witness_reliability"), 800),
        "document_quality": c.text(raw.get("document_quality"), 800),
        "digital_evidence_quality": c.text(raw.get("digital_evidence_quality"), 800),
        "chain_of_custody_concerns": c.str_list(raw.get("chain_of_custody_concerns")),
        "overall_strength": c.enum(raw.get("overall_strength"), c.STRENGTHS, "Moderate"),
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": c.str_list(raw.get("laws_referenced")),
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
