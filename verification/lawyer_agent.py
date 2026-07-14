"""Lawyer Agent — builds the legal argument from retrieved law.

Applies statutes to the question and argues BOTH sides (prosecution and
defense), so the downstream critics have something adversarial to test rather
than a one-sided narrative that is easy to rubber-stamp.

Grounding rule: it may cite ONLY the retrieved context. Everything it produces
is then independently checked by the Judge, Fact Checker, Risk and Citation
agents — nothing it says is trusted on its own.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from case_intelligence import common as c

logger = logging.getLogger(__name__)


def build_argument(question: str, retrieval: Dict[str, Any], llm) -> Dict[str, Any]:
    context = retrieval.get("context") or ""

    if not context:
        # Be honest rather than argue from nothing. An empty corpus hit is a
        # real answer ("we don't hold law on this"), not an invitation to invent.
        return {
            "legal_reasoning": (
                "No applicable provisions were found in the indexed legal knowledge base "
                "for this question, so no grounded legal argument can be constructed."
            ),
            "prosecution_arguments": [],
            "defense_arguments": [],
            "applicable_laws": [],
            "assumptions": [],
            "conclusion": "",
            "no_context": True,
        }

    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the LAWYER agent. Apply the retrieved statutes to the question and
construct the legal analysis. Argue BOTH sides fairly — your output will be
independently challenged by a Judge, a Fact Checker and a Risk agent, so an
overstated or unsupported claim will be caught.

STRICT GROUNDING RULES:
- Use ONLY the retrieved legal context below. Do NOT introduce statutes, sections
  or Act names that do not appear in it.
- Reproduce [स्रोत: …] citation tags EXACTLY as written. Do not translate,
  renumber, or paraphrase them — they are verified character-by-character
  against the legal corpus, and an altered tag will be rejected as fabricated.
- State your ASSUMPTIONS explicitly. An unstated assumption is an unsupported
  conclusion.
- If the context does not answer the question, say so.

QUESTION:
{question}

RETRIEVED LEGAL CONTEXT (the ONLY permissible source of law):
{context[:3500]}

Return ONLY this JSON schema:
{{
  "legal_reasoning": "the core legal analysis, citing [स्रोत: …] tags",
  "prosecution_arguments": ["argument supporting liability/the claim", ...],
  "defense_arguments": ["argument against liability/the claim", ...],
  "applicable_laws": [{{"act": "...", "section": "... or ''", "provision": "what it requires"}}],
  "assumptions": ["assumption this analysis depends on", ...],
  "conclusion": "the position best supported by the cited law"
}}"""

    raw = c.run_agent(llm, "Lawyer", prompt)
    result = {
        "legal_reasoning": c.text(raw.get("legal_reasoning"), 6000),
        "prosecution_arguments": c.str_list(raw.get("prosecution_arguments")),
        "defense_arguments": c.str_list(raw.get("defense_arguments")),
        "applicable_laws": c.dict_list(
            raw.get("applicable_laws"),
            {"act": "act", "section": "section", "provision": "provision"},
            20,
        ),
        "assumptions": c.str_list(raw.get("assumptions")),
        "conclusion": c.text(raw.get("conclusion"), 2000),
        "no_context": False,
    }
    return c.with_parse_flag(result, raw)
