from hybrid_retriever import HybridRetriever, reciprocal_rank_fusion
from retrieval_config import RetrievalConfig


def _dense(id_, rank, distance):
    return {"id": id_, "content": id_, "metadata": {}, "distance": distance, "rank": rank}


def _bm25(id_, rank, score):
    return {"id": id_, "content": id_, "metadata": {}, "bm25_score": score, "rank": rank}


def test_rrf_rewards_agreement():
    # 'b' is mid-rank in both lists; 'a' is top of dense only, 'x' top of bm25 only.
    dense = [_dense("a", 1, 0.1), _dense("b", 2, 0.2), _dense("c", 3, 0.3)]
    bm25 = [_bm25("x", 1, 9.0), _bm25("b", 2, 5.0), _bm25("y", 3, 1.0)]
    fused = reciprocal_rank_fusion(dense, bm25, rrf_k=60)
    # 'b' appears in both -> should outrank items that appear in only one list.
    top_id = fused[0]["id"]
    assert top_id == "b"
    b = next(d for d in fused if d["id"] == "b")
    assert b["retrieval_sources"] == ["dense", "bm25"]
    assert b["dense_rank"] == 2 and b["bm25_rank"] == 2


def test_rrf_score_formula():
    dense = [_dense("a", 1, 0.1)]
    bm25 = [_bm25("a", 1, 9.0)]
    fused = reciprocal_rank_fusion(dense, bm25, dense_weight=1.0, bm25_weight=1.0, rrf_k=60)
    expected = 1.0 / 61 + 1.0 / 61
    assert abs(fused[0]["rrf_score"] - expected) < 1e-12


def test_weights_shift_ranking():
    dense = [_dense("d_only", 1, 0.1)]
    bm25 = [_bm25("b_only", 1, 9.0)]
    # Heavily favor bm25 -> its exclusive hit should win.
    fused = reciprocal_rank_fusion(dense, bm25, dense_weight=0.1, bm25_weight=5.0, rrf_k=60)
    assert fused[0]["id"] == "b_only"


def test_fused_ranks_are_sequential():
    dense = [_dense("a", 1, 0.1), _dense("b", 2, 0.2)]
    bm25 = [_bm25("b", 1, 5.0), _bm25("c", 2, 1.0)]
    fused = reciprocal_rank_fusion(dense, bm25)
    assert [d["rank"] for d in fused] == list(range(1, len(fused) + 1))


class _FakeDense:
    def __init__(self, hits):
        self._hits = hits

    def retrieve(self, query, top_k=6, where=None):
        return self._hits[:top_k]


class _FakeBM25:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, top_k=10, where=None):
        return self._hits[:top_k]


def test_hybrid_disabled_delegates_to_dense():
    dense_hits = [_dense("a", 1, 0.1), _dense("b", 2, 0.2)]
    retriever = HybridRetriever(
        _FakeDense(dense_hits),
        _FakeBM25([_bm25("z", 1, 9.0)]),
        config=RetrievalConfig(enable_hybrid=False),
    )
    out = retriever.retrieve("q", top_k=2)
    # dense-only: bm25's 'z' must not appear
    assert [d["id"] for d in out] == ["a", "b"]


def test_hybrid_enabled_fuses_both():
    retriever = HybridRetriever(
        _FakeDense([_dense("a", 1, 0.1), _dense("b", 2, 0.2)]),
        _FakeBM25([_bm25("b", 1, 9.0), _bm25("c", 2, 1.0)]),
        config=RetrievalConfig(enable_hybrid=True, candidate_pool=30),
    )
    out = retriever.retrieve("q", top_k=3)
    ids = [d["id"] for d in out]
    assert "b" in ids and "a" in ids and "c" in ids
    # 'b' agreed by both retrievers -> ranked first
    assert ids[0] == "b"
