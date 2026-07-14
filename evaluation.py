"""Offline retrieval evaluation harness.

Measures retrieval quality with standard IR metrics so retrieval changes can
be judged on numbers instead of vibes:

    - Recall@K   (hit-rate style for source-level labels; true recall when
                  chunk-level labels are provided)
    - MRR        (mean reciprocal rank of the first relevant chunk)
    - Context Precision@K  (fraction of retrieved chunks that are relevant)
    - Context Recall@K     (fraction of the query's expected sources covered)

Ground-truth relevance
-----------------------
The seed gold set (``data/eval/gold_queries.json``, generated from
``data/arena_dataset.csv``) labels relevance at the **Act / source-file
level**: a retrieved chunk is relevant iff it comes from the Act the question
is about. This is a deliberate, documented proxy — we do not have
human-annotated span-level labels for Nepali legal text. The schema also
supports optional ``relevant_chunk_ids`` / ``relevant_section_numbers`` for a
higher-fidelity eval if those are ever annotated; the metrics automatically
use the most specific labels available.

By default retrieval runs over the **whole corpus with no arena routing**, so
the metrics reflect the retriever's own ability to find the right Act — not
the upstream classifier. Pass ``--use-routing`` to measure the full
production path instead.

Results are written as both JSON and CSV.

Usage:
    uv run python evaluation.py --build-gold          # (re)generate gold set
    uv run python evaluation.py --retriever hybrid --k 6
    uv run python evaluation.py --retriever dense --k 6 --use-routing
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

GOLD_PATH = Path("data/eval/gold_queries.json")
RESULTS_DIR = Path("data/eval/results")


# --------------------------------------------------------------------------
# Gold set construction
# --------------------------------------------------------------------------

def build_gold_from_arena_dataset(
    dataset_path: str = "data/arena_dataset.csv",
    out_path: Path = GOLD_PATH,
) -> dict:
    """Generate a source-level gold set from the arena classifier dataset.

    Each labeled (question, arena) pair becomes an eval query whose relevant
    documents are all chunks from that arena's source PDF(s).
    """
    from arena_routing import CATEGORY_TO_SOURCES

    queries = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            text = (row.get("text") or "").strip()
            arena = (row.get("arena") or "").strip()
            if not text or not arena:
                continue
            sources = CATEGORY_TO_SOURCES.get(arena)
            if not sources:
                logger.warning("Skipping query with unroutable arena %r: %r", arena, text)
                continue
            queries.append(
                {
                    "id": f"q{i:03d}",
                    "query": text,
                    "expected_arena": arena,
                    "relevant_source_files": list(sources),
                }
            )

    gold = {
        "relevance_level": "source_file",
        "description": (
            "Source/Act-level relevance proxy generated from arena_dataset.csv. "
            "A chunk is relevant iff its source_file is one of relevant_source_files. "
            "Optional relevant_chunk_ids / relevant_section_numbers may be added "
            "per query for higher-fidelity evaluation."
        ),
        "queries": queries,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(gold, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %d gold queries to %s", len(queries), out_path)
    return gold


def load_gold(path: Path = GOLD_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Relevance + metrics
# --------------------------------------------------------------------------

def is_relevant(doc: Dict[str, Any], gold_entry: Dict[str, Any]) -> bool:
    """Most-specific-label-wins relevance test for a single retrieved doc."""
    meta = doc.get("metadata", {}) or {}

    chunk_ids = gold_entry.get("relevant_chunk_ids")
    if chunk_ids:
        return doc.get("id") in set(chunk_ids)

    sections = gold_entry.get("relevant_section_numbers")
    if sections:
        return str(meta.get("section_number")) in {str(s) for s in sections}

    sources = set(gold_entry.get("relevant_source_files", []))
    return meta.get("source_file") in sources


def _num_relevant_total(gold_entry: Dict[str, Any]) -> Optional[int]:
    """Size of the relevant set when it is finite/known (chunk-level labels).
    None for source-level labels, where the relevant set is 'all chunks of the
    Act' and true recall is not meaningful — we report hit-rate instead."""
    chunk_ids = gold_entry.get("relevant_chunk_ids")
    if chunk_ids:
        return len(set(chunk_ids))
    return None


def per_query_metrics(
    hits: List[Dict[str, Any]],
    gold_entry: Dict[str, Any],
    k: int,
) -> Dict[str, float]:
    top = hits[:k]
    rel_flags = [is_relevant(d, gold_entry) for d in top]
    num_rel_retrieved = sum(rel_flags)

    # Recall@K
    total_rel = _num_relevant_total(gold_entry)
    if total_rel:  # chunk-level: true recall
        recall = num_rel_retrieved / total_rel
    else:  # source-level: hit-rate (>=1 relevant retrieved)
        recall = 1.0 if num_rel_retrieved > 0 else 0.0

    # MRR
    mrr = 0.0
    for rank, flag in enumerate(rel_flags, start=1):
        if flag:
            mrr = 1.0 / rank
            break

    # Context Precision@K
    precision = num_rel_retrieved / k if k else 0.0

    # Context Recall@K: fraction of expected sources covered in top-K
    expected_sources = set(gold_entry.get("relevant_source_files", []))
    if expected_sources:
        covered = {
            (d.get("metadata", {}) or {}).get("source_file")
            for d in top
            if is_relevant(d, gold_entry)
        }
        context_recall = len(covered & expected_sources) / len(expected_sources)
    else:
        context_recall = recall

    return {
        "recall_at_k": recall,
        "mrr": mrr,
        "context_precision_at_k": precision,
        "context_recall_at_k": context_recall,
        "num_relevant_retrieved": float(num_rel_retrieved),
    }


def native_score(hits: List[Dict[str, Any]], k: int) -> float:
    """Mean native retrieval score over top-K (scale differs per retriever):
    cosine similarity (1 - distance) for dense, RRF score for hybrid."""
    top = hits[:k]
    if not top:
        return 0.0
    vals = []
    for d in top:
        if d.get("rrf_score") is not None:
            vals.append(d["rrf_score"])
        elif d.get("distance") is not None:
            vals.append(1.0 - d["distance"])
        elif d.get("bm25_score") is not None:
            vals.append(d["bm25_score"])
    return sum(vals) / len(vals) if vals else 0.0


# --------------------------------------------------------------------------
# Evaluation driver
# --------------------------------------------------------------------------

def evaluate_retriever(
    retrieve_fn: Callable[[str, int, Optional[dict]], List[Dict[str, Any]]],
    gold: dict,
    k: int,
    use_routing: bool = False,
    query_transform: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Run a retriever over the gold set and aggregate metrics.

    ``retrieve_fn(query, top_k, where)`` must return the project's standard
    list-of-dict hits. ``query_transform`` optionally preprocesses each query
    (e.g. the rewriter); by default the raw gold query is used for a
    deterministic comparison.
    """
    from arena_routing import build_where_for_category

    per_query = []
    agg = {
        "recall_at_k": 0.0,
        "mrr": 0.0,
        "context_precision_at_k": 0.0,
        "context_recall_at_k": 0.0,
        "native_score": 0.0,
        "latency_ms": 0.0,
    }

    queries = gold["queries"]
    for entry in queries:
        raw_query = entry["query"]
        query = query_transform(raw_query) if query_transform else raw_query
        where = build_where_for_category(entry["expected_arena"]) if use_routing else None

        t0 = time.perf_counter()
        hits = retrieve_fn(query, k, where)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        m = per_query_metrics(hits, entry, k)
        score = native_score(hits, k)

        record = {
            "id": entry["id"],
            "query": raw_query,
            "expected_arena": entry["expected_arena"],
            "retrieved": len(hits),
            "native_score": score,
            "latency_ms": latency_ms,
            **m,
            "top_sources": [
                (d.get("metadata", {}) or {}).get("source_file") for d in hits[:k]
            ],
        }
        per_query.append(record)

        for key in ("recall_at_k", "mrr", "context_precision_at_k", "context_recall_at_k"):
            agg[key] += m[key]
        agg["native_score"] += score
        agg["latency_ms"] += latency_ms

    n = len(queries) or 1
    aggregate = {key: value / n for key, value in agg.items()}
    aggregate["num_queries"] = len(queries)
    aggregate["k"] = k
    aggregate["use_routing"] = use_routing

    return {"aggregate": aggregate, "per_query": per_query}


def write_results(results: Dict[str, Any], out_prefix: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{out_prefix}.json"
    csv_path = RESULTS_DIR / f"{out_prefix}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    per_query = results["per_query"]
    if per_query:
        fieldnames = [
            "id", "query", "expected_arena", "retrieved",
            "recall_at_k", "mrr", "context_precision_at_k", "context_recall_at_k",
            "native_score", "latency_ms",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in per_query:
                writer.writerow(row)

    logger.info("Wrote results to %s and %s", json_path, csv_path)


def _get_retriever(name: str):
    """Return a `retrieve_fn(query, k, where)` for the named retriever.

    'hybrid_rerank' retrieves a larger candidate pool (rerank_candidates, the
    "Top-20" stage) via hybrid retrieval and then cross-encoder reranks down to
    k, so Recall@K / MRR are measured at the same cutoff as the other configs.
    """
    import rag_pipeline as rp

    if name == "dense":
        return lambda q, k, where: rp.rag_retriever.retrieve(q, top_k=k, where=where)
    if name == "hybrid":
        return lambda q, k, where: rp.hybrid_retriever.retrieve(q, top_k=k, where=where)
    if name == "hybrid_rerank":
        from reranker import get_reranker
        from retrieval_config import RETRIEVAL_CONFIG

        def _retrieve_rerank(q, k, where):
            candidate_k = max(RETRIEVAL_CONFIG.rerank_candidates, k)
            candidates = rp.hybrid_retriever.retrieve(q, top_k=candidate_k, where=where)
            if not candidates:
                return []
            return get_reranker().rerank(q, candidates, top_k=k)

        return _retrieve_rerank
    raise ValueError(f"Unknown retriever: {name!r} (expected dense|hybrid|hybrid_rerank)")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Offline retrieval evaluation")
    parser.add_argument("--build-gold", action="store_true", help="Regenerate the gold set and exit")
    parser.add_argument("--retriever", choices=["dense", "hybrid", "hybrid_rerank"], default="hybrid")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--use-routing", action="store_true", help="Apply arena where-filter (production path)")
    parser.add_argument("--rewrite", action="store_true", help="Apply the query rewriter before retrieval")
    parser.add_argument("--gold", default=str(GOLD_PATH))
    parser.add_argument("--out-prefix", default=None)
    args = parser.parse_args()

    if args.build_gold:
        build_gold_from_arena_dataset(out_path=Path(args.gold))
        return

    gold = load_gold(Path(args.gold))
    retrieve_fn = _get_retriever(args.retriever)

    query_transform = None
    if args.rewrite:
        import rag_pipeline as rp
        query_transform = lambda q: rp.build_retrieval_query(q, rp.llm)

    results = evaluate_retriever(
        retrieve_fn, gold, k=args.k,
        use_routing=args.use_routing,
        query_transform=query_transform,
    )

    agg = results["aggregate"]
    print("\n=== Retrieval evaluation ===")
    print(f"retriever={args.retriever}  k={args.k}  use_routing={args.use_routing}  rewrite={args.rewrite}")
    print(f"queries              : {agg['num_queries']}")
    print(f"Recall@{args.k}            : {agg['recall_at_k']:.3f}")
    print(f"MRR                  : {agg['mrr']:.3f}")
    print(f"Context Precision@{args.k} : {agg['context_precision_at_k']:.3f}")
    print(f"Context Recall@{args.k}    : {agg['context_recall_at_k']:.3f}")
    print(f"Avg native score     : {agg['native_score']:.4f}")
    print(f"Avg latency (ms)     : {agg['latency_ms']:.1f}")

    out_prefix = args.out_prefix or f"eval_{args.retriever}_k{args.k}{'_routed' if args.use_routing else ''}"
    write_results(results, out_prefix)


if __name__ == "__main__":
    main()
