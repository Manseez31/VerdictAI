"""Reranker unit tests. The cross-encoder model is mocked so these run fast
and offline (no ~1.1GB model download)."""

from reranker import CrossEncoderReranker


class _FakeModel:
    def __init__(self, scores):
        self.scores = scores

    def predict(self, pairs):
        assert len(pairs) == len(self.scores)
        # each pair is [query, passage]
        assert all(len(p) == 2 for p in pairs)
        return self.scores


def _make(scores):
    """Build a reranker without loading the real model."""
    r = object.__new__(CrossEncoderReranker)
    r.model_name = "fake"
    r.model = _FakeModel(scores)
    return r


def test_rerank_orders_by_score_desc_and_truncates():
    docs = [
        {"id": "a", "content": "x"},
        {"id": "b", "content": "y"},
        {"id": "c", "content": "z"},
    ]
    out = _make([0.1, 0.9, 0.5]).rerank("q", docs, top_k=2)
    assert [d["id"] for d in out] == ["b", "c"]
    assert out[0]["rerank_score"] == 0.9
    assert [d["rank"] for d in out] == [1, 2]


def test_rerank_can_reorder_retrieval_ranking():
    # Retriever thought 'a' was best; cross-encoder disagrees.
    docs = [
        {"id": "a", "content": "x", "rank": 1},
        {"id": "b", "content": "y", "rank": 2},
    ]
    out = _make([0.2, 0.8]).rerank("q", docs, top_k=2)
    assert out[0]["id"] == "b"


def test_rerank_adds_source_marker():
    docs = [{"id": "a", "content": "x", "retrieval_sources": ["dense", "bm25"]}]
    out = _make([0.7]).rerank("q", docs, top_k=1)
    assert out[0]["retrieval_sources"] == ["dense", "bm25", "rerank"]


def test_rerank_empty_returns_empty():
    assert _make([]).rerank("q", [], top_k=5) == []


def test_reranker_config_defaults(monkeypatch):
    import importlib
    for var in ["ENABLE_RERANKER", "RERANKER_MODEL", "RERANK_CANDIDATES", "RERANK_TOP_K"]:
        monkeypatch.delenv(var, raising=False)
    import retrieval_config
    importlib.reload(retrieval_config)
    cfg = retrieval_config.load_retrieval_config()
    assert cfg.enable_reranker is False               # optional, off by default
    assert cfg.reranker_model == "BAAI/bge-reranker-base"
    assert cfg.rerank_candidates == 20
    assert cfg.rerank_top_k == 5
