"""Citation Verifier — MANDATORY, deterministic, non-bypassable.

This is NOT a new implementation. It is a thin adapter over the existing
`security.citation_verifier`, which checks every [स्रोत: …] tag against the Acts
and sections actually indexed in the vector store.

WHY IT IS AN ADAPTER AND NOT AN AGENT
-------------------------------------
Every other stage in this pipeline is an LLM that can be wrong. Citation
verification must NOT be one of them — asking a model "is this citation real?"
reintroduces exactly the hallucination we are trying to eliminate. This stage is
pure code checking against ground truth, so it cannot be talked out of a verdict.

It is also TERMINAL: no downstream stage may overrule it. The consensus engine
consumes its result; it cannot argue with it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from security.citation_verifier import CitationRegistry, verify_citations

logger = logging.getLogger(__name__)


def _texts_to_check(analysis: Dict[str, Any]) -> List[str]:
    """Every field the lawyer could have put a citation in."""
    out: List[str] = []
    for key in ("legal_reasoning", "conclusion"):
        value = analysis.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    for key in ("prosecution_arguments", "defense_arguments"):
        for item in analysis.get(key) or []:
            if isinstance(item, str) and item:
                out.append(item)
    return out


def verify_analysis_citations(
    analysis: Dict[str, Any],
    registry: Optional[CitationRegistry],
    retrieval: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Validate every citation the lawyer emitted.

    Returns source_trust_score (0-100), the per-citation verdicts, and the list
    of fabricated citations. Fails CLOSED: with no registry, nothing is claimed
    as verified.
    """
    all_citations: List[Dict[str, Any]] = []
    hallucinated: List[Dict[str, Any]] = []
    verified = total = 0

    for text in _texts_to_check(analysis):
        result = verify_citations(text, registry, strict=strict)
        all_citations.extend(result["citations"])
        hallucinated.extend(result["hallucinated"])
        verified += result["verified_count"]
        total += result["total_count"]

    # De-duplicate: the same Act may legitimately be cited several times.
    seen = set()
    unique_citations = []
    for cit in all_citations:
        key = (cit["act"], cit["section"])
        if key not in seen:
            seen.add(key)
            unique_citations.append(cit)

    seen_bad = set()
    unique_hallucinated = []
    for h in hallucinated:
        key = (h["act"], h["section"])
        if key not in seen_bad:
            seen_bad.add(key)
            unique_hallucinated.append(h)

    if registry is None:
        # No ground truth -> we cannot verify anything. Fail closed rather than
        # silently assert trust we have not earned.
        trust = 0 if total else 100
    else:
        trust = 100 if total == 0 else round(100 * verified / total)

    # Did the lawyer cite anything the retriever never actually returned? That is
    # a citation that exists in the corpus but was NOT in this query's evidence —
    # a subtler fabrication than an invented Act, and worth surfacing.
    off_evidence: List[str] = []
    if retrieval:
        retrieved_keys = {
            (c["act"], c["section"]) for c in (retrieval.get("citations") or [])
        }
        for cit in unique_citations:
            if cit["verified"] and (cit["act"], cit["section"]) not in retrieved_keys:
                off_evidence.append(
                    f"{cit['act']}" + (f", धारा {cit['section']}" if cit["section"] else "")
                )

    if unique_hallucinated:
        logger.warning(
            "Citation verifier: %d fabricated citation(s) in the legal analysis",
            len(unique_hallucinated),
        )

    return {
        "citations": unique_citations,
        "hallucinated_citations": unique_hallucinated,
        "off_evidence_citations": off_evidence,
        "citations_verified": verified,
        "citations_total": total,
        "source_trust_score": trust,
        "registry_available": registry is not None,
    }
