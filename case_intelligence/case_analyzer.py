"""Feature 1 — Case Analyzer Agent.

Extracts the structured legal skeleton of a case: parties, dates, locations,
alleged actions, evidence, legal issues, and category. Neutral — it does not
argue for any side.
"""

from __future__ import annotations

from typing import Any, Dict

from . import common as c


def analyze_case(case: Dict[str, str], llm) -> Dict[str, Any]:
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the CASE ANALYZER — a neutral legal analyst. Extract the case
structure from the scenario. Only identify what is present; do not argue.

{c.case_block(case)}

Return ONLY this JSON schema:
{{
  "summary": "3-5 sentence neutral summary of the case",
  "parties": [{{"name": "...", "role": "accused | complainant | victim | witness | counsel | other"}}],
  "dates": [{{"date": "as stated (e.g. 'Jan 12' or '2024-02-03')", "event": "what happened then"}}],
  "locations": ["place mentioned", ...],
  "alleged_actions": ["alleged act (treated as UNPROVEN)", ...],
  "evidence": ["piece of evidence mentioned or clearly implied", ...],
  "legal_issues": ["legal question the case raises", ...],
  "possible_charges": ["charge or claim that could plausibly be brought", ...],
  "case_category": "one concise category (e.g. Fraud, Cybercrime, Homicide, Property Dispute)",
  "laws_referenced": ["law or legal principle relevant here", ...],
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "CaseAnalyzer", prompt)
    result = {
        "summary": c.text(raw.get("summary")),
        "parties": c.dict_list(raw.get("parties"), {"name": "name", "role": "role"}, 20),
        "dates": c.dict_list(raw.get("dates"), {"date": "date", "event": "event"}, 30),
        "locations": c.str_list(raw.get("locations")),
        "alleged_actions": c.str_list(raw.get("alleged_actions")),
        "evidence": c.str_list(raw.get("evidence")),
        "legal_issues": c.str_list(raw.get("legal_issues")),
        "possible_charges": c.str_list(raw.get("possible_charges")),
        "case_category": c.text(raw.get("case_category"), 80) or case.get("case_type", "Other"),
        "reasoning_summary": c.text(raw.get("summary")),
        "laws_referenced": c.str_list(raw.get("laws_referenced")),
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
