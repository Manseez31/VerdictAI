from bm25_index import BM25Index, tokenize


def _index():
    return BM25Index(
        ids=["a", "b", "c", "d"],
        documents=[
            "फार्मेसी परिषद्को स्थापना र दर्ता",
            "खोप कार्यक्रमको जिम्मेवारी स्वास्थ्य मन्त्रालय",
            "pharmacy license renewal process",
            "नेपालको संविधानले मौलिक हक सुनिश्चित गर्दछ",
        ],
        metadatas=[
            {"source_file": "pharmacy.pdf", "section_number": "3"},
            {"source_file": "immunization.pdf"},
            {"source_file": "pharmacy.pdf"},
            {"source_file": "constitution.pdf"},
        ],
    )


def test_tokenize_mixed_scripts():
    assert tokenize("फार्मेसी ऐन, २०५७?") == ["फार्मेसी", "ऐन", "२०५७"]
    assert tokenize("Pharmacy LICENSE 2024") == ["pharmacy", "license", "2024"]
    assert tokenize("") == []


def test_search_ranks_exact_term_first():
    idx = _index()
    hits = idx.search("फार्मेसी दर्ता", top_k=3)
    assert hits, "expected at least one lexical hit"
    assert hits[0]["id"] == "a"
    # scores are descending
    scores = [h["bm25_score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_no_match_returns_empty():
    idx = _index()
    assert idx.search("zzz nonexistent term", top_k=5) == []


def test_search_respects_where_filter():
    idx = _index()
    hits = idx.search("दर्ता स्थापना", top_k=5, where={"source_file": "pharmacy.pdf"})
    assert all(h["metadata"]["source_file"] == "pharmacy.pdf" for h in hits)


def test_search_where_or_filter():
    idx = _index()
    where = {"$or": [{"source_file": "pharmacy.pdf"}, {"source_file": "immunization.pdf"}]}
    hits = idx.search("खोप फार्मेसी", top_k=5, where=where)
    assert hits
    assert all(h["metadata"]["source_file"] in {"pharmacy.pdf", "immunization.pdf"} for h in hits)


def test_ranks_are_one_indexed_and_sequential():
    idx = _index()
    hits = idx.search("फार्मेसी दर्ता स्थापना", top_k=5)
    assert [h["rank"] for h in hits] == list(range(1, len(hits) + 1))
