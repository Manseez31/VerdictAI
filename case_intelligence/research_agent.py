"""Feature 3 — Legal Research Agent (RAG-grounded).

This is the agent that connects the Case Intelligence Suite to VerdictAI's
existing legal knowledge base. It reuses the production hybrid retriever
(dense + BM25 + RRF) and the same `[स्रोत: …]` citation format the chat UI
already renders, so every downstream agent argues from REAL retrieved law
instead of hallucinated statutes.

The retriever and llm are injectable, so the agent (and the whole suite) runs
offline in tests with a fake retriever — the heavy `rag_pipeline` import only
happens on the default production path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import common as c

logger = logging.getLogger(__name__)


def _default_retriever():
    # Lazy — importing rag_pipeline loads embedding models + vector store.
    from rag_pipeline import hybrid_retriever
    return hybrid_retriever


def build_research_queries(case: Dict[str, str], analysis: Dict[str, Any]) -> List[str]:
    """Derive a small set of retrieval queries from the analysis."""
    queries: List[str] = []
    for issue in (analysis.get("legal_issues") or [])[:3]:
        queries.append(str(issue))
    for charge in (analysis.get("possible_charges") or [])[:2]:
        queries.append(str(charge))
    if not queries:
        # Fall back to the case type + a slice of the description.
        queries.append(f"{case.get('case_type', '')} {case.get('description', '')[:200]}".strip())
    # De-dupe while preserving order.
    seen, unique = set(), []
    for q in queries:
        key = q.strip().lower()
        if q.strip() and key not in seen:
            seen.add(key)
            unique.append(q.strip())
    return unique[:5]


def _citation_from_chunk(chunk: Dict[str, Any]) -> Dict[str, str]:
    meta = chunk.get("metadata") or {}
    return {
        "act": str(meta.get("act_name", "Unknown Act")),
        "section": str(meta.get("section_number")) if meta.get("section_number") else "",
        "source": str(meta.get("source_file", "")),
    }


def _citation_tag(cit: Dict[str, str]) -> str:
    # Mirrors rag_pipeline.format_chunk_with_citation's tag so the frontend's
    # existing chip renderer works identically.
    if cit["section"]:
        return f"[स्रोत: {cit['act']}, धारा {cit['section']}]"
    return f"[स्रोत: {cit['act']}]"


def retrieve_context(
    case: Dict[str, str],
    analysis: Dict[str, Any],
    retriever,
    per_query_k: int = 4,
    max_chunks: int = 8,
) -> Dict[str, Any]:
    """Run retrieval and assemble deduped chunks, a cited context string, and
    the unique citation list — no LLM involved (pure retrieval)."""
    queries = build_research_queries(case, analysis)
    seen_ids, chunks = set(), []
    for q in queries:
        try:
            hits = retriever.retrieve(q, top_k=per_query_k)
        except Exception:
            logger.exception("retrieval failed for query %r", q)
            hits = []
        for h in hits:
            hid = h.get("id") or h.get("content", "")[:60]
            if hid in seen_ids:
                continue
            seen_ids.add(hid)
            chunks.append(h)
        if len(chunks) >= max_chunks:
            break
    chunks = chunks[:max_chunks]

    citations, seen_cit = [], set()
    context_parts = []
    for ch in chunks:
        cit = _citation_from_chunk(ch)
        key = (cit["act"], cit["section"])
        if key not in seen_cit:
            seen_cit.add(key)
            citations.append(cit)
        context_parts.append(f"{_citation_tag(cit)}\n{ch.get('content', '')}")

    return {
        "queries": queries,
        "chunks": chunks,
        "citations": citations,
        "context": "\n\n".join(context_parts),
    }


def research_case(
    case: Dict[str, str],
    analysis: Dict[str, Any],
    retriever=None,
    llm=None,
) -> Dict[str, Any]:
    """Retrieve applicable law and synthesize it into structured provisions.

    Returns applicable_laws, the structured citation list, a grounded summary,
    and the raw retrieved context (passed to the argument agents downstream).
    """
    retriever = retriever if retriever is not None else _default_retriever()
    llm = llm or c.get_case_llm()

    retrieval = retrieve_context(case, analysis, retriever)

    if not retrieval["context"]:
        # No knowledge-base hits — be honest rather than invent law.
        return {
            "applicable_laws": [],
            "citations": [],
            "research_summary": (
                "No directly applicable provisions were found in the indexed legal "
                "knowledge base for this scenario. Downstream analysis proceeds on "
                "general legal principles only."
            ),
            "queries": retrieval["queries"],
            "context": "",
            "laws_referenced": [],
            "confidence": 20,
        }

    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the LEGAL RESEARCH agent. Using ONLY the retrieved legal context
below (from official statutes), identify the provisions applicable to this case.
Do NOT invent laws or sections not present in the context. Keep every [स्रोत: …]
citation tag exactly as written when you refer to a provision.

{c.case_block(case)}

{c.upstream("CASE ANALYSIS", analysis, 1800)}

RETRIEVED LEGAL CONTEXT (authoritative — cite only from here):
{retrieval['context'][:6000]}

Return ONLY this JSON schema:
{{
  "applicable_laws": [
    {{"act": "Act name", "section": "section number or ''", "provision": "what it says / requires", "relevance": "why it applies here"}}
  ],
  "research_summary": "3-6 sentence grounded summary of the applicable legal framework, referencing the [स्रोत: …] tags",
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "LegalResearch", prompt)

    applicable = c.dict_list(
        raw.get("applicable_laws"),
        {"act": "act", "section": "section", "provision": "provision", "relevance": "relevance"},
        20,
    )

    result = {
        "applicable_laws": applicable,
        "citations": retrieval["citations"],
        "research_summary": c.text(raw.get("research_summary")),
        "reasoning_summary": c.text(raw.get("research_summary")),
        "queries": retrieval["queries"],
        "context": retrieval["context"],
        "laws_referenced": [
            f"{cit['act']}" + (f", धारा {cit['section']}" if cit["section"] else "")
            for cit in retrieval["citations"]
        ],
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
