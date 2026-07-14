# Retrieval pipeline

This document describes the retrieval-quality layer added on top of the base
dense RAG pipeline: query rewriting, hybrid (dense + BM25) retrieval with
Reciprocal Rank Fusion, retrieval diagnostics, and the offline evaluation /
benchmark harness.

All of it is **modular and configurable** (see [Configuration](#configuration))
and **additive** — with hybrid retrieval disabled, the pipeline behaves exactly
as the original dense-only system.

## Pipeline overview

```
user question
    │
    ▼
┌─────────────────────────┐
│ Query Rewriting Layer   │  query_rewriter.py   (ENABLE_QUERY_REWRITING)
│ - preserve intent       │  rewrites the question into a retrieval-optimized
│ - expand legal terms    │  query; logs original + rewritten
└─────────────────────────┘
    │  (normalized to Devanagari)
    ▼
┌─────────────────────────┐
│ Arena routing → where   │  arena_routing.py    (source_file filter)
└─────────────────────────┘
    │
    ├───────────────────────────────┬───────────────────────────────┐
    ▼                               ▼                               │
┌───────────────────┐        ┌───────────────────┐                 │
│ Dense retriever   │        │ BM25 lexical index│  bm25_index.py   │
│ Chroma, cosine    │        │ Okapi BM25        │                 │
│ (RAGRetriever)    │        │ built from Chroma │                 │
└───────────────────┘        └───────────────────┘                 │
    │  ranked hits                 │  ranked hits                   │
    └───────────────┬───────────────┘                               │
                    ▼                                               │
        ┌─────────────────────────────┐                            │
        │ Reciprocal Rank Fusion (RRF)│  hybrid_retriever.py       │
        │ weighted, rank-based        │  (ENABLE_HYBRID_RETRIEVAL) │
        └─────────────────────────────┘                            │
                    │  fused top-N (candidates)  ◄──────────────────┘
                    ▼
        ┌─────────────────────────────┐
        │ Cross-Encoder Reranker      │  reranker.py
        │ Top-20 → rerank → Top-5     │  (ENABLE_RERANKER, optional)
        └─────────────────────────────┘
                    │  reranked top-k (+ diagnostics)
                    ▼
        citation-tagged context → Generator (LLM) → Judge
```

## Query rewriting layer

`query_rewriter.rewrite_query_for_retrieval()` uses the LLM to rewrite the
user's question into a query optimized for *retrieval* (as opposed to
`expand_query_with_llm`, which is optimized for *answering*). It:

- preserves the original intent, topic, and Act (no topic drift);
- expands relevant legal terminology / synonyms (e.g. दर्ता → प्रमाणपत्र /
  इजाजतपत्र, खोप → प्रतिरक्षण) so the query overlaps more with the wording of
  the target legal chunk;
- transliterates romanized/English input to Devanagari;
- logs both the original and rewritten query (`[query-rewrite] original=… rewritten=…`);
- falls back to the original query on any failure, so it can never make
  retrieval worse by dropping the query.

Toggle with `ENABLE_QUERY_REWRITING`. When disabled, the pipeline uses the
original expand-then-normalize path.

## Ranking algorithm: weighted Reciprocal Rank Fusion

Dense cosine distances and BM25 scores are on different, incomparable scales,
so we fuse by **rank**, not score. For a document `d`:

```
rrf_score(d) =  dense_weight / (rrf_k + rank_dense(d))
              + bm25_weight  / (rrf_k + rank_bm25(d))
```

where `rank_*(d)` is `d`'s 1-indexed position in that retriever's list (a term
is omitted if the retriever didn't surface `d`). Documents found by **both**
retrievers accumulate both contributions and rise to the top — that agreement
signal is the source of the precision/recall gains.

- `rrf_k` (default 60) damps the influence of exact rank position; larger =
  flatter.
- `dense_weight` / `bm25_weight` (default 1.0 / 1.0) trade off semantic vs
  lexical matching.
- `candidate_pool` (default 30) is how many candidates are pulled from **each**
  retriever before fusion; the fused list is then truncated to `top_k`.

Implementation: `hybrid_retriever.reciprocal_rank_fusion()`.

### BM25 index

`bm25_index.BM25Index` is a dependency-free Okapi BM25 (`k1=1.5, b=0.75`) built
directly from the Chroma collection (`BM25Index.from_vector_store`), so it
always reflects whatever has been embedded — there is no second copy of the
corpus to maintain. The tokenizer handles mixed Devanagari / romanized-Nepali /
English and Devanagari digits, and the index honors the same `where`
(source_file) filter as the dense retriever so both legs respect arena routing.

## Cross-encoder reranking (optional)

A bi-encoder (the dense retriever) embeds the query and each passage
*separately*, so it can only approximate their joint relevance. A cross-encoder
(`reranker.CrossEncoderReranker`) scores the `(query, passage)` pair *jointly* —
much more accurate, but too slow to run over the whole corpus. So it runs only
over the retrieved shortlist:

```
query → retrieve Top-N (RERANK_CANDIDATES=20) → cross-encoder rerank → Top-K (RERANK_TOP_K=5) → context
```

- Model: `BAAI/bge-reranker-base` (configurable via `RERANKER_MODEL`), loaded
  lazily on first use — nothing is downloaded or held in memory unless
  reranking is enabled.
- Off by default (`ENABLE_RERANKER=false`); it is fully optional and additive.
- Implemented as a stage inside `rag_pipeline.retrieve_for_answer`, so it
  composes with dense or hybrid retrieval and with arena routing.

> **Caveat (measured, not assumed):** `bge-reranker-base` is trained primarily
> on English/Chinese. On this Devanagari legal corpus it does **not** help — see
> the benchmark below, where it *regresses* retrieval quality because the model
> cannot reliably score Nepali passages (rerank scores collapse toward zero).
> It is implemented, tested, and left off by default. A multilingual
> cross-encoder such as `BAAI/bge-reranker-v2-m3` is the recommended model to
> try if reranking is desired — swap `RERANKER_MODEL` and re-run the benchmark.

## Retrieval diagnostics (debug mode)

With `RETRIEVAL_DEBUG=1` (or `HybridRetriever.retrieve(..., debug=True)`), each
retrieval logs, per returned chunk:

- retrieved chunk count and per-retriever pool sizes;
- retrieval source provenance (`dense`, `bm25`, or `dense+bm25`);
- the fused `rrf_score`, the per-retriever ranks (`dense_rank`, `bm25_rank`),
  and the native scores (`distance`, `bm25_score`).

These fields are also attached to every returned hit, which is what the
evaluation harness consumes. Diagnostics are emitted via logging only — the
HTTP `/chat` response contract is unchanged.

## Offline evaluation harness

`evaluation.py` scores a retriever over a gold set and writes JSON + CSV to
`data/eval/results/`.

```bash
uv run python evaluation.py --build-gold                 # (re)generate gold set
uv run python evaluation.py --retriever hybrid --k 6
uv run python evaluation.py --retriever dense  --k 6 --use-routing
```

### Metrics

Given a query whose relevant set is defined by the gold labels (see below),
over the top-K retrieved chunks:

| Metric | Definition |
|--------|------------|
| **Recall@K** | Chunk-level labels: `|relevant ∩ top-K| / |relevant|`. Source-level labels: hit-rate = 1 if ≥1 relevant chunk in top-K else 0 (true recall is undefined when the relevant set is "all chunks of the Act"). |
| **MRR** | `1 / rank` of the first relevant chunk (0 if none). |
| **Context Precision@K** | `|relevant ∩ top-K| / K` — how much of the context window is on-target. This is the primary hallucination-reduction signal. |
| **Context Recall@K** | Fraction of the query's expected source Acts represented in top-K (source coverage). |
| **Retrieval latency** | Wall-clock ms for the retrieval (+rerank) stage, averaged over queries. Isolates the reranker's cost. |
| **Hallucination rate** | End-to-end: fraction of generated answers the Judge scores `faithfulness == 0` (contradicts the context or asserts claims not present in it). Lower is better. Also reported: weak-grounding rate (`faithfulness ≤ 1`), fully-grounded rate (`== 2`), and mean faithfulness/correctness. |

### Ground-truth relevance (and its honest limitations)

The seed gold set (`data/eval/gold_queries.json`, generated from
`data/arena_dataset.csv` via `--build-gold`) labels relevance at the **Act /
source-file level**: a chunk is relevant iff it comes from the Act the question
is about. This is a deliberate, documented **proxy** — there are no
human-annotated span-level labels for this Nepali legal corpus. It measures
"did we retrieve from the right law," which is exactly the failure mode behind
most hallucinations here, but it does **not** measure whether the specific
retrieved passage answers the question.

The gold schema supports a higher-fidelity upgrade without code changes: add
`relevant_chunk_ids` or `relevant_section_numbers` to any query and the metrics
automatically use the most specific labels available (chunk > section > source).

By default the harness retrieves over the **whole corpus with no arena
routing**, so the metrics reflect the retriever's own ability to find the right
Act, not the upstream classifier. `--use-routing` measures the full production
path instead.

## Benchmark procedure

`benchmark.py` compares the three configurations — **Dense only**, **Hybrid**,
and **Hybrid + Reranker** — over the same gold set and reports Recall@K, MRR,
Context Precision/Recall, retrieval latency, and (end-to-end) hallucination
rate, writing combined results to `data/eval/results/` as JSON + CSV.

```bash
uv run python benchmark.py --k 5                    # all 3 configs, incl. hallucination
uv run python benchmark.py --k 5 --no-hallucination # retrieval metrics only (fast)
uv run python benchmark.py --k 5 --halluc-limit 0   # hallucination over all queries
uv run python benchmark.py --k 5 --use-routing      # full production path
```

Retrieval metrics run over all gold queries (cheap, deterministic). The
hallucination measurement drives real generation + judging, so it runs on a
stratified subset by default (`--halluc-limit`, default 9) to bound cost.

### Reference result (seed gold set, k=5, no routing)

Retrieval metrics (30 queries):

| Metric | Dense only | Hybrid | Hybrid + Reranker |
|--------|-----------:|-------:|------------------:|
| Recall@5 | 0.767 | **0.867** | 0.800 |
| MRR | 0.708 | **0.786** | 0.734 |
| Context Precision@5 | 0.573 | **0.667** | 0.567 |
| Context Recall@5 | 0.767 | **0.867** | 0.800 |
| Retrieval latency | 22.1 ms | 22.4 ms | **3599 ms** |

Hallucination, end-to-end (9-query stratified subset):

| Metric | Dense only | Hybrid | Hybrid + Reranker |
|--------|-----------:|-------:|------------------:|
| Hallucination rate (faithfulness==0) | 0.222 | 0.222 | 0.111 |
| Mean faithfulness /2 | 1.56 | 1.33 | 1.33 |

**Reading the results honestly:**

- **Hybrid is the clear winner on retrieval** — +13% Recall, +11% MRR, +16%
  Context Precision over dense, at no latency cost. This is the robust,
  large-N signal.
- **`bge-reranker-base` regresses retrieval and costs ~160× latency** (22 ms →
  3.6 s/query on CPU). It cannot score Devanagari passages reliably — rerank
  scores collapse toward zero — so it reorders good candidates into worse
  ones. This is a genuine negative result, which is exactly why the reranker
  is **off by default** and why measuring beats assuming.
- **The hallucination numbers on 9 queries are within noise** (one query =
  11.1%); the mixed signal (reranker's lower faithfulness==0 rate but also
  lower mean faithfulness) is not conclusive at this N. Run `--halluc-limit 0`
  for a full-set measurement, and prefer a multilingual reranker
  (`BAAI/bge-reranker-v2-m3`) before drawing reranker conclusions.

Re-run the
benchmark after any retrieval change to guard against regressions.

## Configuration

All knobs live in `retrieval_config.py` and are read from the environment
(see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_QUERY_REWRITING` | `true` | Rewrite questions into retrieval-optimized queries |
| `ENABLE_HYBRID_RETRIEVAL` | `true` | Fuse dense + BM25 (false → original dense-only) |
| `DENSE_WEIGHT` | `1.0` | Dense weight in weighted RRF |
| `BM25_WEIGHT` | `1.0` | BM25 weight in weighted RRF |
| `RRF_K` | `60` | RRF damping constant |
| `RETRIEVAL_CANDIDATE_POOL` | `30` | Candidates pulled from each retriever before fusion |
| `ENABLE_RERANKER` | `false` | Cross-encoder rerank of retrieved candidates (optional) |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` | Cross-encoder model for reranking |
| `RERANK_CANDIDATES` | `20` | Candidates retrieved before reranking (Top-N) |
| `RERANK_TOP_K` | `5` | Chunks kept after reranking (Top-K context) |
| `RETRIEVAL_DEBUG` | `false` | Emit retrieval diagnostics to the log |
