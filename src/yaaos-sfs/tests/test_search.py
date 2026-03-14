"""Tests for hybrid search (vector + FTS + RRF fusion)."""

from __future__ import annotations

from unittest.mock import MagicMock

from yaaos_sfs.search import hybrid_search, SearchResult


class TestSearchResult:
    def test_snippet_short_text(self):
        r = SearchResult("path", "file.py", "short text", 0, 0.5)
        assert r.snippet() == "short text"

    def test_snippet_truncation(self):
        long_text = "word " * 100
        r = SearchResult("path", "file.py", long_text, 0, 0.5)
        snippet = r.snippet(max_len=50)
        assert len(snippet) <= 55  # 50 + "..."
        assert snippet.endswith("...")


class TestHybridSearch:
    def test_returns_results(self, db, tmp_path):
        """Hybrid search should return ranked results."""
        # Index some files
        f1 = tmp_path / "api.py"
        f1.write_text("def api_handler(): pass")
        f2 = tmp_path / "db.py"
        f2.write_text("def database_query(): pass")

        emb1 = [1.0] + [0.0] * 383
        emb2 = [0.0] + [1.0] + [0.0] * 382
        db.upsert_file(f1, ["def api_handler(): pass"], [emb1])
        db.upsert_file(f2, ["def database_query(): pass"], [emb2])

        # Mock provider
        provider = MagicMock()
        provider.embed_query.return_value = [0.9] + [0.0] * 383
        provider.dims = 384

        results = hybrid_search(db, provider, "api handler", top_k=5)
        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)

    def test_empty_index(self, db):
        """Search on empty index should return empty list."""
        provider = MagicMock()
        provider.embed_query.return_value = [0.1] * 384
        provider.dims = 384

        results = hybrid_search(db, provider, "anything", top_k=5)
        assert results == []

    def test_rrf_fusion_boosts_dual_matches(self, db, tmp_path):
        """A result matching both vector AND keyword should rank higher."""
        f1 = tmp_path / "match_both.py"
        f1.write_text("python database helpers for web apps")
        f2 = tmp_path / "match_vec_only.py"
        f2.write_text("unrelated content xyz")

        # f1 gets an embedding similar to query, f2 gets a different one
        emb1 = [1.0] + [0.0] * 383
        emb2 = [0.0] * 384
        db.upsert_file(f1, ["python database helpers for web apps"], [emb1])
        db.upsert_file(f2, ["unrelated content xyz"], [emb2])

        provider = MagicMock()
        provider.embed_query.return_value = [0.95] + [0.0] * 383

        results = hybrid_search(db, provider, "python database", top_k=5)
        # File matching both signals should be first
        if len(results) >= 2:
            assert results[0].filename == "match_both.py"
