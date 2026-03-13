"""Hybrid search engine combining vector similarity and keyword matching (RRF fusion)."""

from __future__ import annotations

from dataclasses import dataclass

from .db import Database
from .providers import EmbeddingProvider


@dataclass
class SearchResult:
    file_path: str
    filename: str
    chunk_text: str
    chunk_index: int
    score: float

    def snippet(self, max_len: int = 200) -> str:
        text = self.chunk_text.strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rsplit(" ", 1)[0] + "..."


def hybrid_search(
    db: Database,
    provider: EmbeddingProvider,
    query: str,
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """Run hybrid search: vector similarity + FTS5 keyword, merged with RRF."""

    query_embedding = provider.embed_query(query)

    # Get results from both search methods
    vec_results = db.search_vector(query_embedding, top_k=top_k * 2)
    fts_results = db.search_fts(query, top_k=top_k * 2)

    # Build RRF scores
    # Key: chunk_id -> (score, result_data)
    scores: dict[int, float] = {}
    result_data: dict[int, dict] = {}

    for rank, r in enumerate(vec_results):
        chunk_id = r["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (rrf_k + rank + 1)
        result_data[chunk_id] = r

    for rank, r in enumerate(fts_results):
        chunk_id = r["id"]
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (rrf_k + rank + 1)
        if chunk_id not in result_data:
            result_data[chunk_id] = r

    # Sort by fused score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    results = []
    for chunk_id, score in ranked:
        r = result_data[chunk_id]
        results.append(SearchResult(
            file_path=r["path"],
            filename=r["filename"],
            chunk_text=r["chunk_text"],
            chunk_index=r["chunk_index"],
            score=score,
        ))

    return results
