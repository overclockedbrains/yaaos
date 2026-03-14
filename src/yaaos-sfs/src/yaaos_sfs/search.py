"""Hybrid search engine combining vector similarity, keyword matching, and path matching (RRF fusion)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .db import Database
from .providers import EmbeddingProvider


@dataclass
class SearchResult:
    file_path: str
    filename: str
    chunk_text: str
    chunk_index: int
    score: float
    file_type: str = ""
    modified_at: str = ""

    def snippet(self, max_len: int = 200) -> str:
        text = self.chunk_text.strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rsplit(" ", 1)[0] + "..."


def _fuzzy_path_score(query: str, file_path: str) -> float:
    """Score how well query terms match the file path. Returns 0.0-1.0."""
    query_terms = query.lower().split()
    path_lower = file_path.lower().replace("\\", "/")

    if not query_terms:
        return 0.0

    matches = sum(1 for term in query_terms if term in path_lower)
    return matches / len(query_terms)


def _recency_boost(modified_at: str, days_window: int = 7) -> float:
    """Boost score for recently modified files. Returns multiplier >= 1.0."""
    if not modified_at:
        return 1.0
    try:
        mod_time = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = (now - mod_time).total_seconds() / 86400

        if age_days <= days_window:
            return 1.0 + 0.1 * (1.0 - age_days / days_window)
    except Exception:
        pass
    return 1.0


def _ext_from_path(path: str) -> str:
    """Extract extension from path string."""
    if "." in path:
        return path.rsplit(".", 1)[-1].lower()
    return ""


def hybrid_search(
    db: Database,
    provider: EmbeddingProvider,
    query: str,
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[SearchResult]:
    """Run hybrid search: vector + FTS5 keyword + path matching, merged with RRF."""

    query_embedding = provider.embed_query(query)

    # Get results from both search methods
    vec_results = db.search_vector(query_embedding, top_k=top_k * 2)
    fts_results = db.search_fts(query, top_k=top_k * 2)

    # Build RRF scores from vector + keyword signals
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

    # Third signal: path matching
    path_scored = [
        (cid, _fuzzy_path_score(query, data.get("path", "")))
        for cid, data in result_data.items()
    ]
    path_scored = [(cid, s) for cid, s in path_scored if s > 0]
    path_scored.sort(key=lambda x: x[1], reverse=True)
    for rank, (chunk_id, _) in enumerate(path_scored):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (rrf_k + rank + 1)

    # Apply recency boost
    for chunk_id in scores:
        data = result_data[chunk_id]
        scores[chunk_id] *= _recency_boost(data.get("modified_at", ""))

    # Sort by fused score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    results = []
    for chunk_id, score in ranked:
        r = result_data[chunk_id]
        path = r.get("path", "")
        results.append(
            SearchResult(
                file_path=path,
                filename=r.get("filename", ""),
                chunk_text=r.get("chunk_text", ""),
                chunk_index=r.get("chunk_index", 0),
                score=score,
                file_type=_ext_from_path(path),
                modified_at=r.get("modified_at", ""),
            )
        )

    return results
