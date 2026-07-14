import importlib


def test_defaults(monkeypatch):
    for var in [
        "ENABLE_QUERY_REWRITING", "ENABLE_HYBRID_RETRIEVAL",
        "DENSE_WEIGHT", "BM25_WEIGHT", "RRF_K", "RETRIEVAL_CANDIDATE_POOL",
        "RETRIEVAL_DEBUG",
    ]:
        monkeypatch.delenv(var, raising=False)
    import retrieval_config
    importlib.reload(retrieval_config)
    cfg = retrieval_config.load_retrieval_config()
    assert cfg.enable_query_rewriting is True
    assert cfg.enable_hybrid is True
    assert cfg.dense_weight == 1.0
    assert cfg.rrf_k == 60
    assert cfg.candidate_pool == 30
    assert cfg.debug is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ENABLE_HYBRID_RETRIEVAL", "false")
    monkeypatch.setenv("BM25_WEIGHT", "2.5")
    monkeypatch.setenv("RRF_K", "40")
    monkeypatch.setenv("RETRIEVAL_DEBUG", "1")
    import retrieval_config
    importlib.reload(retrieval_config)
    cfg = retrieval_config.load_retrieval_config()
    assert cfg.enable_hybrid is False
    assert cfg.bm25_weight == 2.5
    assert cfg.rrf_k == 40
    assert cfg.debug is True


def test_bad_values_fall_back_to_default(monkeypatch):
    monkeypatch.setenv("RRF_K", "not-an-int")
    monkeypatch.setenv("BM25_WEIGHT", "")
    import retrieval_config
    importlib.reload(retrieval_config)
    cfg = retrieval_config.load_retrieval_config()
    assert cfg.rrf_k == 60
    assert cfg.bm25_weight == 1.0
