"""Dependency-free BM25 (Okapi) lexical index over the same chunk corpus as
the dense Chroma store.

Why BM25 here: the legal corpus is dominated by exact statutory tokens —
धारा numbers, Act names, rare domain terms (फार्मेसी, खोप) — where dense
cosine similarity is weakest. Lexical matching recovers the chunks dense
retrieval misses, which is the main lever on grounded-answer recall.

The index is built directly from the Chroma collection so it stays in sync
with whatever has been embedded; there is no second copy of the data to
maintain.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Devanagari runs (U+0900–U+097F) OR latin/digit runs. Punctuation, the danda
# (।), and whitespace act as separators and are dropped. Latin is lowercased so
# romanized Nepali matches case-insensitively.
_TOKEN_RE = re.compile(r"[ऀ-ॿ]+|[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    """Tokenize mixed Devanagari / romanized-Nepali / English legal text."""
    return _TOKEN_RE.findall((text or "").lower())


def _matches_where(metadata: Dict[str, Any], where: Optional[dict]) -> bool:
    """Replicate the small subset of Chroma `where` filters this project uses
    so BM25 candidates respect the same arena routing as the dense retriever.

    Supports ``{"source_file": "x"}`` and ``{"$or": [ {...}, ... ]}``.
    """
    if not where:
        return True
    if "$or" in where:
        return any(_matches_where(metadata, clause) for clause in where["$or"])
    if "$and" in where:
        return all(_matches_where(metadata, clause) for clause in where["$and"])
    for key, value in where.items():
        if metadata.get(key) != value:
            return False
    return True


class BM25Index:
    """In-memory BM25 Okapi index.

    Small corpus (hundreds–thousands of short chunks) so a straightforward
    scan-and-score implementation is more than fast enough and keeps the
    ranking fully transparent and unit-testable.
    """

    def __init__(
        self,
        ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError("ids, documents and metadatas must be the same length")

        self.k1 = k1
        self.b = b
        self.ids = ids
        self.documents = documents
        self.metadatas = metadatas

        self._doc_tokens: List[List[str]] = [tokenize(doc) for doc in documents]
        self._doc_len: List[int] = [len(toks) for toks in self._doc_tokens]
        self.corpus_size = len(documents)
        self.avgdl = (sum(self._doc_len) / self.corpus_size) if self.corpus_size else 0.0

        # term frequency per doc + document frequency across corpus
        self._doc_freqs: List[Counter] = [Counter(toks) for toks in self._doc_tokens]
        df: Counter = Counter()
        for freqs in self._doc_freqs:
            df.update(freqs.keys())
        self._df = df

        # Okapi BM25 idf with the standard +1 to keep it non-negative.
        self._idf: Dict[str, float] = {
            term: math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

        logger.info(
            "BM25Index built: %d docs, %d unique terms, avgdl=%.1f",
            self.corpus_size,
            len(df),
            self.avgdl,
        )

    @classmethod
    def from_vector_store(cls, vector_store, **kwargs) -> "BM25Index":
        """Build the index from a `rag_pipeline.VectorStore` (or any object
        exposing a Chroma `.collection`)."""
        collection = vector_store.collection
        got = collection.get(include=["documents", "metadatas"])
        return cls(
            ids=got["ids"],
            documents=got["documents"],
            metadatas=got["metadatas"],
            **kwargs,
        )

    def _score(self, query_tokens: List[str], doc_index: int) -> float:
        freqs = self._doc_freqs[doc_index]
        dl = self._doc_len[doc_index]
        denom_norm = self.k1 * (1 - self.b + self.b * (dl / self.avgdl if self.avgdl else 0))
        score = 0.0
        for term in query_tokens:
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf.get(term, 0.0)
            score += idf * (tf * (self.k1 + 1)) / (tf + denom_norm)
        return score

    def search(
        self,
        query: str,
        top_k: int = 10,
        where: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Return the top_k BM25 matches (optionally filtered by `where`).

        Each hit: ``{id, content, metadata, bm25_score, rank}``. Only
        positively-scoring documents are returned (a zero score means no query
        term matched, i.e. not a lexical hit).
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored = []
        for i in range(self.corpus_size):
            if where and not _matches_where(self.metadatas[i], where):
                continue
            s = self._score(query_tokens, i)
            if s > 0:
                scored.append((s, i))

        scored.sort(key=lambda x: x[0], reverse=True)

        hits: List[Dict[str, Any]] = []
        for rank, (score, i) in enumerate(scored[:top_k], start=1):
            hits.append(
                {
                    "id": self.ids[i],
                    "content": self.documents[i],
                    "metadata": self.metadatas[i],
                    "bm25_score": score,
                    "rank": rank,
                }
            )
        return hits
