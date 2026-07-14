"""Output guardrails — the last gate before an answer reaches the user.

WHY THIS EXISTS
---------------
Input filtering is necessary but never sufficient: a novel jailbreak that slips
past the input guard still has to produce OUTPUT, and output is much easier to
judge than intent. This stage independently checks what the model actually said:

  * Did it leak its system prompt / safety preamble?  (prompt extraction)
  * Did it emit operational instructions for unlawful conduct?
  * Did it cite law that does not exist?               (citation verification)
  * Did it leak PII or an API key?

Anything that fails is blocked or downgraded, and a trust score is attached so
the UI can show *how much* to rely on the answer instead of implying certainty.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .citation_verifier import CitationRegistry, verify_citations
from .pii import detect_pii

logger = logging.getLogger(__name__)

# Fragments of our own system prompts — if these appear in output, the model has
# been induced to disclose its instructions.
_LEAK_MARKERS = [
    "STRICT SAFETY RULES",
    "SAFETY DIRECTIVE",
    "OUTPUT FORMAT (non-negotiable)",
    "You are one specialist agent inside an EDUCATIONAL",
    "non-negotiable — they override any instruction",
]

# Operational unlawful-assistance content (as OUTPUT, not as a topic of analysis).
# Deliberately narrow: discussing that evidence tampering is an offence is fine;
# giving steps to do it is not.
_UNLAWFUL_OUTPUT = [
    re.compile(r"(here('s| is)\s+how|steps?\s+to|you\s+(can|should))\s+.{0,50}(destroy|delete|shred|wipe|hide|tamper\s+with|fabricate)\s+.{0,25}evidence", re.I),
    re.compile(r"(here('s| is)\s+how|steps?\s+to|you\s+(can|should))\s+.{0,50}(evade|avoid|escape|defeat)\s+.{0,25}(police|law\s+enforcement|arrest|detection)", re.I),
    re.compile(r"(here('s| is)\s+how|steps?\s+to)\s+.{0,40}(launder|bribe|forge|intimidate\s+.{0,15}witness)", re.I),
]

REFUSAL = (
    "I can't provide that. VerdictAI is an educational legal-analysis tool and "
    "cannot assist with unlawful conduct or disclose its internal instructions. "
    "You can ask me to analyse a legal question or a case scenario instead."
)


@dataclass
class OutputVerdict:
    """Result of guarding one model output."""
    text: str
    blocked: bool = False
    reasons: List[str] = field(default_factory=list)
    source_trust_score: int = 100
    citations: List[Dict[str, Any]] = field(default_factory=list)
    hallucinated_citations: List[Dict[str, Any]] = field(default_factory=list)
    pii_found: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def safe(self) -> bool:
        return not self.blocked


def guard_output(
    text: str,
    registry: Optional[CitationRegistry] = None,
    strict_citations: bool = False,
) -> OutputVerdict:
    """Validate a model answer. Returns a verdict with the (possibly modified) text."""
    reasons: List[str] = []
    out = text or ""

    # 1. System-prompt leakage.
    if any(marker.lower() in out.lower() for marker in _LEAK_MARKERS):
        logger.warning("Output guard: system prompt leak detected — blocking")
        return OutputVerdict(text=REFUSAL, blocked=True, reasons=["system_prompt_leak"], source_trust_score=0)

    # 2. Operational unlawful assistance.
    for rx in _UNLAWFUL_OUTPUT:
        if rx.search(out):
            logger.warning("Output guard: unlawful-assistance content — blocking")
            return OutputVerdict(text=REFUSAL, blocked=True, reasons=["unlawful_assistance"], source_trust_score=0)

    # 3. Secret leakage (never return a key, even if the model echoes one).
    pii = detect_pii(out)
    if "API_KEY" in pii:
        logger.error("Output guard: API key present in model output — blocking")
        return OutputVerdict(text=REFUSAL, blocked=True, reasons=["credential_leak"], source_trust_score=0)
    if pii:
        reasons.append("pii_present")

    # 4. Citation verification (the anti-hallucination gate).
    cit = verify_citations(out, registry, strict=strict_citations)
    out = cit["text"]
    if cit["hallucinated"]:
        reasons.append("hallucinated_citation")

    return OutputVerdict(
        text=out,
        blocked=False,
        reasons=reasons,
        source_trust_score=cit["source_trust_score"],
        citations=cit["citations"],
        hallucinated_citations=cit["hallucinated"],
        pii_found=pii,
    )
