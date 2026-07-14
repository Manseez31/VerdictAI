"""Cross-encoder reranking stage.

A bi-encoder (the dense retriever) embeds query and passage independently, so
it can only approximate their joint relevance. A cross-encoder scores the
(query, passage) pair *jointly*, which is far more accurate — but far too slow
to run over the whole corpus. The standard pattern, implemented here, is to use
cheap retrieval to shortlist candidates and then let the cross-encoder re-rank
only that shortlist:

    query → retrieve Top-N (20) → cross-encoder rerank → Top-K (5) → context

Reranking is optional (``RETRIEVAL_CONFIG.enable_reranker``) and the model is
loaded lazily on first use, so nothing is downloaded or held in memory unless
reranking is actually turned on.

Model: ``BAAI/bge-reranker-base`` by default. Note it is primarily trained on
English/Chinese; on Devanagari legal text its benefit is an empirical question
— run ``benchmark.py`` to measure it rather than assuming.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from retrieval_config import RETRIEVAL_CONFIG, RetrievalConfig

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Wraps a sentence-transformers ``CrossEncoder`` and reorders retrieved
    hits by joint (query, passage) relevance."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", device: Optional[str] = None):
        # Imported lazily so importing this module (e.g. in unit tests with a
        # fake reranker) does not require torch/sentence-transformers.
        from sentence_transformers import CrossEncoder

        logger.info("Loading cross-encoder reranker: %s", model_name)
        t0 = time.time()
        self.model_name = model_name
        self.model = CrossEncoder(model_name, device=device)
        logger.info("Cross-encoder loaded in %.1fs", time.time() - t0)

    def rerank(
        self,
        query: str,
        docs: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return the top_k docs reordered by cross-encoder score.

        Each returned doc gets a ``rerank_score`` and a rewritten 1-indexed
        ``rank``; ``retrieval_sources`` gains a ``rerank`` marker for
        diagnostics. Input docs are the project's standard hit dicts.
        """
        if not docs:
            return []

        pairs = [[query, d.get("content", "")] for d in docs]
        scores = self.model.predict(pairs)

        for doc, score in zip(docs, scores):
            doc["rerank_score"] = float(score)
            sources = doc.setdefault("retrieval_sources", [])
            if "rerank" not in sources:
                sources.append("rerank")

        reranked = sorted(docs, key=lambda d: d["rerank_score"], reverse=True)[:top_k]
        for i, doc in enumerate(reranked, start=1):
            doc["rank"] = i
        return reranked


# --------------------------------------------------------------------------
# Lazy process-wide singleton
# --------------------------------------------------------------------------

_reranker: Optional[CrossEncoderReranker] = None


def get_reranker(config: RetrievalConfig = RETRIEVAL_CONFIG) -> CrossEncoderReranker:
    """Return the shared reranker, loading the model on first use."""
    global _reranker
    if _reranker is None or _reranker.model_name != config.reranker_model:
        _reranker = CrossEncoderReranker(config.reranker_model)
    return _reranker
