"""Hybrid retrieval: fuse dense (Chroma) and BM25 (lexical) rankings with
weighted Reciprocal Rank Fusion (RRF).

RRF is rank-based rather than score-based, so it sidesteps the fact that
cosine distances and BM25 scores live on different, incomparable scales. Each
retriever contributes ``weight / (rrf_k + rank)`` for every document it ranks;
documents surfaced by *both* retrievers accumulate both contributions and rise
to the top. That agreement signal is what improves grounded-answer recall over
either retriever alone.

The public ``retrieve()`` is drop-in compatible with
``rag_pipeline.RAGRetriever.retrieve()`` (same ``query, top_k, where`` shape),
so it can be swapped in without changing call sites.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from retrieval_config import RETRIEVAL_CONFIG, RetrievalConfig

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    dense_hits: List[Dict[str, Any]],
    bm25_hits: List[Dict[str, Any]],
    dense_weight: float = 1.0,
    bm25_weight: float = 1.0,
    rrf_k: int = 60,
) -> List[Dict[str, Any]]:
    """Fuse two ranked hit lists into one, ordered by weighted RRF score.

    Hits are matched across lists by their ``id``. Each returned doc carries
    diagnostics: ``rrf_score``, ``dense_rank``/``bm25_rank`` (1-indexed, or
    None if that retriever didn't surface it), the original ``distance`` /
    ``bm25_score`` when available, and ``retrieval_sources`` (which retrievers
    contributed).
    """
    fused: Dict[str, Dict[str, Any]] = {}

    def _ensure(doc: Dict[str, Any]) -> Dict[str, Any]:
        entry = fused.get(doc["id"])
        if entry is None:
            entry = {
                "id": doc["id"],
                "content": doc["content"],
                "metadata": doc.get("metadata", {}),
                "rrf_score": 0.0,
                "dense_rank": None,
                "bm25_rank": None,
                "distance": None,
                "bm25_score": None,
                "retrieval_sources": [],
            }
            fused[doc["id"]] = entry
        return entry

    for rank, doc in enumerate(dense_hits, start=1):
        entry = _ensure(doc)
        entry["dense_rank"] = rank
        entry["distance"] = doc.get("distance")
        entry["rrf_score"] += dense_weight / (rrf_k + rank)
        entry["retrieval_sources"].append("dense")

    for rank, doc in enumerate(bm25_hits, start=1):
        entry = _ensure(doc)
        entry["bm25_rank"] = rank
        entry["bm25_score"] = doc.get("bm25_score")
        entry["rrf_score"] += bm25_weight / (rrf_k + rank)
        entry["retrieval_sources"].append("bm25")

    fused_list = sorted(fused.values(), key=lambda d: d["rrf_score"], reverse=True)
    for i, doc in enumerate(fused_list, start=1):
        doc["rank"] = i
    return fused_list


class HybridRetriever:
    """Wraps a dense retriever + a BM25 index and fuses their results.

    Fully configurable and additive: when ``config.enable_hybrid`` is False it
    delegates to the dense retriever unchanged, preserving the original
    behaviour exactly.
    """

    def __init__(
        self,
        dense_retriever,
        bm25_index,
        config: RetrievalConfig = RETRIEVAL_CONFIG,
    ):
        self.dense_retriever = dense_retriever
        self.bm25_index = bm25_index
        self.config = config

    def retrieve(
        self,
        query: str,
        top_k: int = 6,
        where: Optional[dict] = None,
        debug: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        debug = self.config.debug if debug is None else debug

        if not self.config.enable_hybrid:
            results = self.dense_retriever.retrieve(query, top_k=top_k, where=where)
            if debug:
                self._log_diagnostics("dense-only", query, results)
            return results

        pool = max(self.config.candidate_pool, top_k)
        dense_hits = self.dense_retriever.retrieve(query, top_k=pool, where=where)
        bm25_hits = self.bm25_index.search(query, top_k=pool, where=where)

        fused = reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            dense_weight=self.config.dense_weight,
            bm25_weight=self.config.bm25_weight,
            rrf_k=self.config.rrf_k,
        )
        results = fused[:top_k]

        if debug:
            self._log_diagnostics("hybrid", query, results, len(dense_hits), len(bm25_hits))
        return results

    @staticmethod
    def _log_diagnostics(
        mode: str,
        query: str,
        results: List[Dict[str, Any]],
        n_dense: Optional[int] = None,
        n_bm25: Optional[int] = None,
    ) -> None:
        """Emit retrieval diagnostics (debug mode only)."""
        logger.info(
            "[retrieval-diagnostics] mode=%s query=%r returned=%d dense_pool=%s bm25_pool=%s",
            mode,
            query,
            len(results),
            n_dense,
            n_bm25,
        )
        for doc in results:
            meta = doc.get("metadata", {})
            logger.info(
                "  rank=%s id=%s src=%s section=%s sources=%s rrf=%.5f dense_rank=%s bm25_rank=%s "
                "distance=%s bm25_score=%s",
                doc.get("rank"),
                doc.get("id"),
                meta.get("source_file"),
                meta.get("section_number"),
                "+".join(doc.get("retrieval_sources", [])) or "dense",
                doc.get("rrf_score", 0.0),
                doc.get("dense_rank"),
                doc.get("bm25_rank"),
                _fmt(doc.get("distance")),
                _fmt(doc.get("bm25_score")),
            )


def _fmt(value: Optional[float]) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else str(value)
