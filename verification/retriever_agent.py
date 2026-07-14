"""Retriever Agent — evidence + legal sources + ranked source scores.

Reuses the PRODUCTION retrieval stack (hybrid dense + BM25 + RRF). It adds no
new retrieval logic; it adapts the existing retriever's output into the
evidence/citation/score shape the verification pipeline consumes.

No LLM call — this stage is deterministic, so it is fast and cannot hallucinate.

SOURCE SCORING
--------------
The retriever's native scores are not comparable across methods (RRF score for
hybrid, cosine similarity for dense), so we normalize to a 0-100 `source_score`
by rank position rather than by raw value. Rank is the one signal that means the
same thing regardless of which retriever produced it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 3500  # keeps the 5-call pipeline inside provider TPM budgets


def _default_retriever():
    # Lazy: importing rag_pipeline loads embedding models + the vector store.
    from rag_pipeline import hybrid_retriever
    return hybrid_retriever


def _citation_of(chunk: Dict[str, Any]) -> Dict[str, str]:
    meta = chunk.get("metadata") or {}
    return {
        "act": str(meta.get("act_name", "Unknown Act")),
        "section": str(meta.get("section_number")) if meta.get("section_number") else "",
        "source": str(meta.get("source_file", "")),
    }


def _tag(cit: Dict[str, str]) -> str:
    """The project's canonical citation tag — the SAME format the citation
    verifier checks and the UI renders. Using anything else here would silently
    break verification downstream."""
    if cit["section"]:
        return f"[स्रोत: {cit['act']}, धारा {cit['section']}]"
    return f"[स्रोत: {cit['act']}]"


def _rank_score(rank: int, total: int) -> int:
    """Map 1-indexed rank to 0-100. Rank is comparable across retrievers; raw
    RRF/cosine scores are not."""
    if total <= 1:
        return 100
    return int(round(100 * (1 - (rank - 1) / total)))


def retrieve_evidence(
    query: str,
    retriever=None,
    top_k: int = 6,
    where: Optional[dict] = None,
) -> Dict[str, Any]:
    """Retrieve and rank the legal sources for a query.

    Returns {evidence, citations, source_scores, context, retrieved_count}.
    """
    retriever = retriever if retriever is not None else _default_retriever()

    try:
        hits = retriever.retrieve(query, top_k=top_k, where=where)
    except Exception:
        logger.exception("Retriever agent: retrieval failed for %r", query)
        hits = []

    evidence: List[Dict[str, Any]] = []
    citations: List[Dict[str, str]] = []
    source_scores: List[Dict[str, Any]] = []
    seen_cit = set()
    context_parts: List[str] = []
    used = 0

    total = len(hits)
    for i, hit in enumerate(hits, start=1):
        cit = _citation_of(hit)
        tag = _tag(cit)
        content = hit.get("content", "") or ""
        score = _rank_score(i, total)

        evidence.append({
            "id": hit.get("id", f"chunk-{i}"),
            "content": content,
            "citation": tag,
            "act": cit["act"],
            "section": cit["section"],
            "rank": i,
            "source_score": score,
        })

        key = (cit["act"], cit["section"])
        if key not in seen_cit:
            seen_cit.add(key)
            citations.append(cit)

        source_scores.append({
            "citation": tag,
            "rank": i,
            "score": score,
            # Provenance for the audit trail / debugging.
            "retrieval_sources": hit.get("retrieval_sources", []),
        })

        block = f"{tag}\n{content}"
        if used + len(block) <= MAX_CONTEXT_CHARS:
            context_parts.append(block)
            used += len(block)

    logger.info("Retriever agent: %d chunks, %d unique citations", total, len(citations))

    return {
        "evidence": evidence,
        "citations": citations,
        "source_scores": source_scores,
        "context": "\n\n".join(context_parts),
        "retrieved_count": total,
        # Mean rank score across what we retrieved: a crude but honest proxy for
        # "how much material did we actually find". Zero hits => zero.
        "mean_source_score": (
            int(round(sum(s["score"] for s in source_scores) / len(source_scores)))
            if source_scores else 0
        ),
    }
