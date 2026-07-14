"""Benchmark: Dense-only vs Hybrid vs Hybrid + Cross-Encoder Reranker.

Reports, for each configuration:
  - Recall@K, MRR, Context Precision@K, Context Recall@K  (retrieval quality)
  - Retrieval-stage latency                               (the reranker's cost)
  - Hallucination rate                                    (end-to-end, judged)

Retrieval metrics run over the whole gold set with no arena routing by default,
so they isolate retrieval quality (pass --use-routing for the production path).

Hallucination rate is measured end-to-end: for each config we retrieve context,
run the Generator, and have the Judge score faithfulness. A "hallucination" is
an answer the judge scores faithfulness == 0 (contradicts the context or uses
claims not present in it). Because this drives real LLM generation + judging it
is expensive, so it runs on a stratified subset (--halluc-limit, default 9);
pass --no-hallucination to skip it or --halluc-limit 0 for all queries.

Results are written to data/eval/results/ as JSON + CSV.

Usage:
    uv run python benchmark.py --k 5
    uv run python benchmark.py --k 5 --no-hallucination
    uv run python benchmark.py --k 5 --halluc-limit 0 --use-routing
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluation import (
    GOLD_PATH,
    RESULTS_DIR,
    _get_retriever,
    evaluate_retriever,
    load_gold,
)

logger = logging.getLogger(__name__)

CONFIGS = ["dense", "hybrid", "hybrid_rerank"]
CONFIG_LABELS = {
    "dense": "Dense only",
    "hybrid": "Hybrid",
    "hybrid_rerank": "Hybrid + Reranker",
}


# --------------------------------------------------------------------------
# Hallucination measurement (end-to-end: retrieve -> generate -> judge)
# --------------------------------------------------------------------------

def _stratified_subset(queries: List[dict], limit: int) -> List[dict]:
    """Pick up to `limit` queries balanced across arenas (0 => all)."""
    if not limit or limit >= len(queries):
        return queries
    by_arena: Dict[str, List[dict]] = defaultdict(list)
    for q in queries:
        by_arena[q["expected_arena"]].append(q)
    picked: List[dict] = []
    arenas = list(by_arena.values())
    i = 0
    while len(picked) < limit and any(arenas):
        bucket = arenas[i % len(arenas)]
        if bucket:
            picked.append(bucket.pop(0))
        i += 1
        if all(not b for b in arenas):
            break
    return picked[:limit]


def measure_hallucination(
    config: str,
    queries: List[dict],
    k: int,
    use_routing: bool,
) -> Dict[str, Any]:
    """Retrieve -> generate -> judge for each query; aggregate faithfulness."""
    import rag_pipeline as rp
    from arena_routing import build_where_for_category
    from evaluate import judge_against_context  # reuse the production judge

    retrieve_fn = _get_retriever(config)

    n = len(queries)
    hallucinated = 0      # faithfulness == 0
    weak = 0              # faithfulness <= 1
    grounded = 0         # faithfulness == 2
    faith_sum = 0.0
    correct_sum = 0.0
    gen_latency_sum = 0.0
    per_query = []

    for entry in queries:
        q = entry["query"]
        where = build_where_for_category(entry["expected_arena"]) if use_routing else None

        t0 = time.perf_counter()
        hits = retrieve_fn(q, k, where)
        context = rp.build_context_from_results(hits)
        # Generator (skip the extra spelling-correction LLM call for the benchmark)
        answer = rp.generate_answer_from_context(q, q, context, rp.llm, spellcheck=False)
        gen_latency_ms = (time.perf_counter() - t0) * 1000.0

        scores = judge_against_context(q, answer, context) if context else {"correctness": 0, "faithfulness": 0}
        faithfulness = int(scores.get("faithfulness", 0))
        correctness = int(scores.get("correctness", 0))

        is_halluc = faithfulness == 0
        hallucinated += int(is_halluc)
        weak += int(faithfulness <= 1)
        grounded += int(faithfulness == 2)
        faith_sum += faithfulness
        correct_sum += correctness
        gen_latency_sum += gen_latency_ms

        per_query.append({
            "id": entry["id"],
            "query": q,
            "expected_arena": entry["expected_arena"],
            "faithfulness": faithfulness,
            "correctness": correctness,
            "hallucinated": is_halluc,
            "answer_preview": answer[:160],
        })

    return {
        "num_queries": n,
        "hallucination_rate": hallucinated / n if n else 0.0,
        "weak_grounding_rate": weak / n if n else 0.0,
        "grounded_rate": grounded / n if n else 0.0,
        "mean_faithfulness": faith_sum / n if n else 0.0,
        "mean_correctness": correct_sum / n if n else 0.0,
        "mean_gen_latency_ms": gen_latency_sum / n if n else 0.0,
        "per_query": per_query,
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _print_table(rows: List[tuple], retr: Dict[str, dict], halluc: Optional[Dict[str, dict]], k: int) -> None:
    width = 26
    col = 20
    header = f"{'Metric':<{width}}" + "".join(f"{CONFIG_LABELS[c]:>{col}}" for c in CONFIGS)
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for key, label, fmt in rows:
        line = f"{label:<{width}}"
        for c in CONFIGS:
            src = halluc if (halluc and key in (halluc.get(CONFIGS[0], {}) or {})) else retr
            val = src[c][key]
            line += f"{fmt.format(val):>{col}}"
        print(line)
    print("=" * len(header))


def run(k: int, use_routing: bool, halluc_limit: Optional[int], do_hallucination: bool, gold_path: Path, out_prefix: str) -> dict:
    gold = load_gold(gold_path)

    # ---- Retrieval metrics (all queries) ----
    retr: Dict[str, dict] = {}
    for c in CONFIGS:
        logger.info("Evaluating retrieval: %s ...", c)
        res = evaluate_retriever(_get_retriever(c), gold, k=k, use_routing=use_routing)
        retr[c] = res["aggregate"]
        retr[c]["_per_query"] = res["per_query"]

    # ---- Hallucination (subset, end-to-end) ----
    halluc: Optional[Dict[str, dict]] = None
    subset: List[dict] = []
    if do_hallucination:
        subset = _stratified_subset(gold["queries"], halluc_limit or 0)
        halluc = {}
        for c in CONFIGS:
            logger.info("Measuring hallucination (%d queries): %s ...", len(subset), c)
            halluc[c] = measure_hallucination(c, subset, k=k, use_routing=use_routing)

    # ---- Report ----
    print(f"\n{'#'*72}\nRETRIEVAL & HALLUCINATION BENCHMARK")
    print(f"queries(retrieval)={retr[CONFIGS[0]]['num_queries']}  k={k}  use_routing={use_routing}", end="")
    if halluc:
        print(f"  queries(hallucination)={len(subset)}")
    else:
        print()

    _print_table([
        ("recall_at_k", f"Recall@{k}", "{:.3f}"),
        ("mrr", "MRR", "{:.3f}"),
        ("context_precision_at_k", f"Context Precision@{k}", "{:.3f}"),
        ("context_recall_at_k", f"Context Recall@{k}", "{:.3f}"),
        ("latency_ms", "Retrieval latency (ms)", "{:.1f}"),
    ], retr, None, k)

    if halluc:
        _print_table([
            ("hallucination_rate", "Hallucination rate", "{:.3f}"),
            ("weak_grounding_rate", "Weak-grounding rate", "{:.3f}"),
            ("grounded_rate", "Fully-grounded rate", "{:.3f}"),
            ("mean_faithfulness", "Mean faithfulness /2", "{:.2f}"),
            ("mean_correctness", "Mean correctness /2", "{:.2f}"),
        ], retr, halluc, k)
        print("Hallucination rate = fraction of answers judged faithfulness==0")
        print("(contradicts context or uses claims not present in it). Lower is better.\n")

    # ---- Persist ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    combined = {
        "config": {"k": k, "use_routing": use_routing,
                    "num_retrieval_queries": retr[CONFIGS[0]]["num_queries"],
                    "num_hallucination_queries": len(subset) if halluc else 0},
        "retrieval": {c: {kk: vv for kk, vv in retr[c].items() if not kk.startswith("_")} for c in CONFIGS},
        "hallucination": {c: {kk: vv for kk, vv in halluc[c].items() if kk != "per_query"} for c in CONFIGS} if halluc else None,
        "hallucination_per_query": {c: halluc[c]["per_query"] for c in CONFIGS} if halluc else None,
    }
    with open(RESULTS_DIR / f"{out_prefix}.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    with open(RESULTS_DIR / f"{out_prefix}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric"] + [CONFIG_LABELS[c] for c in CONFIGS])
        retrieval_metrics = [
            ("recall_at_k", f"Recall@{k}"),
            ("mrr", "MRR"),
            ("context_precision_at_k", f"Context Precision@{k}"),
            ("context_recall_at_k", f"Context Recall@{k}"),
            ("latency_ms", "Retrieval latency (ms)"),
        ]
        for key, label in retrieval_metrics:
            writer.writerow([label] + [retr[c][key] for c in CONFIGS])
        if halluc:
            for key, label in [
                ("hallucination_rate", "Hallucination rate"),
                ("weak_grounding_rate", "Weak-grounding rate"),
                ("grounded_rate", "Fully-grounded rate"),
                ("mean_faithfulness", "Mean faithfulness /2"),
                ("mean_correctness", "Mean correctness /2"),
            ]:
                writer.writerow([label] + [halluc[c][key] for c in CONFIGS])

    logger.info("Wrote benchmark results to %s.{json,csv}", RESULTS_DIR / out_prefix)
    return combined


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Benchmark dense vs hybrid vs hybrid+reranker")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--use-routing", action="store_true")
    parser.add_argument("--no-hallucination", dest="hallucination", action="store_false",
                        help="Skip the (expensive) end-to-end hallucination measurement")
    parser.add_argument("--halluc-limit", type=int, default=9,
                        help="Max queries for hallucination measurement (0 = all)")
    parser.add_argument("--gold", default=str(GOLD_PATH))
    parser.add_argument("--out-prefix", default=None)
    args = parser.parse_args()

    out_prefix = args.out_prefix or f"benchmark3_k{args.k}{'_routed' if args.use_routing else ''}"
    run(
        k=args.k,
        use_routing=args.use_routing,
        halluc_limit=args.halluc_limit,
        do_hallucination=args.hallucination,
        gold_path=Path(args.gold),
        out_prefix=out_prefix,
    )


if __name__ == "__main__":
    main()
