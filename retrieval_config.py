"""Central, env-driven configuration for the retrieval pipeline.

Every knob added by the retrieval-quality upgrade lives here so the behaviour
is modular and configurable without touching call sites. Import
``RETRIEVAL_CONFIG`` for the process-wide singleton, or call
``load_retrieval_config()`` to re-read the environment (used by the benchmark
to construct explicit dense-only vs hybrid configs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RetrievalConfig:
    """Immutable retrieval configuration.

    Attributes:
        enable_query_rewriting: Rewrite the user question into a
            retrieval-optimized query before searching.
        enable_hybrid: Fuse dense + BM25 retrieval. When False the pipeline
            falls back to the original dense-only behaviour.
        dense_weight: Weight applied to the dense retriever in weighted RRF.
        bm25_weight: Weight applied to the BM25 retriever in weighted RRF.
        rrf_k: RRF damping constant (larger => flatter rank contribution).
        candidate_pool: How many candidates to pull from EACH retriever
            before fusion (the fused list is then truncated to top_k).
        enable_reranker: Re-rank the retrieved candidates with a cross-encoder
            before building the context. Optional and off by default.
        reranker_model: Cross-encoder model name for reranking.
        rerank_candidates: How many candidates to retrieve and feed into the
            reranker (the "Top 20" stage).
        rerank_top_k: How many chunks to keep after reranking (the "Top 5"
            context stage).
        debug: Emit retrieval diagnostics (chunk count, scores, provenance)
            at INFO level and attach a diagnostics summary to results.
    """

    enable_query_rewriting: bool = True
    enable_hybrid: bool = True
    dense_weight: float = 1.0
    bm25_weight: float = 1.0
    rrf_k: int = 60
    candidate_pool: int = 30
    enable_reranker: bool = False
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 20
    rerank_top_k: int = 5
    debug: bool = False


def load_retrieval_config() -> RetrievalConfig:
    """Build a RetrievalConfig from environment variables (with defaults)."""
    return RetrievalConfig(
        enable_query_rewriting=_get_bool("ENABLE_QUERY_REWRITING", True),
        enable_hybrid=_get_bool("ENABLE_HYBRID_RETRIEVAL", True),
        dense_weight=_get_float("DENSE_WEIGHT", 1.0),
        bm25_weight=_get_float("BM25_WEIGHT", 1.0),
        rrf_k=_get_int("RRF_K", 60),
        candidate_pool=_get_int("RETRIEVAL_CANDIDATE_POOL", 30),
        enable_reranker=_get_bool("ENABLE_RERANKER", False),
        reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base"),
        rerank_candidates=_get_int("RERANK_CANDIDATES", 20),
        rerank_top_k=_get_int("RERANK_TOP_K", 5),
        debug=_get_bool("RETRIEVAL_DEBUG", False),
    )


# Process-wide singleton, read once at import.
RETRIEVAL_CONFIG = load_retrieval_config()
