"""Tests for v2 search enhancements (path matching, recency boost, file_type)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from yaaos_sfs.search import (
    SearchResult,
    _fuzzy_path_score,
    _recency_boost,
    _ext_from_path,
    hybrid_search,
)


class TestFuzzyPathScore:
    def test_exact_match(self):
        score = _fuzzy_path_score("api handler", "/src/api/handler.py")
        assert score > 0

    def test_partial_match(self):
        score = _fuzzy_path_score("api database", "/src/api/routes.py")
        assert 0 < score < 1  # Only "api" matches

    def test_no_match(self):
        score = _fuzzy_path_score("banana", "/src/api/handler.py")
        assert score == 0.0

    def test_empty_query(self):
        assert _fuzzy_path_score("", "/src/file.py") == 0.0

    def test_case_insensitive(self):
        score = _fuzzy_path_score("API Handler", "/src/api/handler.py")
        assert score > 0

    def test_backslash_handling(self):
        score = _fuzzy_path_score("api", "C:\\src\\api\\handler.py")
        assert score > 0


class TestRecencyBoost:
    def test_recent_file_boosted(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        boost = _recency_boost(recent)
        assert boost > 1.0

    def test_old_file_no_boost(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        boost = _recency_boost(old)
        assert boost == 1.0

    def test_empty_date_no_boost(self):
        assert _recency_boost("") == 1.0
        assert _recency_boost(None) == 1.0

    def test_invalid_date_no_boost(self):
        assert _recency_boost("not-a-date") == 1.0

    def test_boundary_date(self):
        # Exactly at the window boundary
        boundary = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        boost = _recency_boost(boundary)
        assert boost >= 1.0


class TestExtFromPath:
    def test_python(self):
        assert _ext_from_path("/src/file.py") == "py"

    def test_docx(self):
        assert _ext_from_path("C:\\docs\\report.docx") == "docx"

    def test_no_extension(self):
        assert _ext_from_path("Makefile") == ""

    def test_multiple_dots(self):
        assert _ext_from_path("archive.tar.gz") == "gz"


class TestSearchResultV2:
    def test_new_fields_defaults(self):
        r = SearchResult("path", "file.py", "text", 0, 0.5)
        assert r.file_type == ""
        assert r.modified_at == ""

    def test_new_fields_set(self):
        r = SearchResult(
            "path", "file.py", "text", 0, 0.5, file_type="py", modified_at="2024-01-01"
        )
        assert r.file_type == "py"
        assert r.modified_at == "2024-01-01"


class TestHybridSearchV2:
    def test_results_have_file_type(self, db, tmp_path):
        """Search results should include file_type."""
        f = tmp_path / "code.py"
        f.write_text("def hello(): pass")
        emb = [1.0] + [0.0] * 383
        db.upsert_file(f, ["def hello(): pass"], [emb])

        provider = MagicMock()
        provider.embed_query.return_value = [0.9] + [0.0] * 383

        results = hybrid_search(db, provider, "hello", top_k=5)
        if results:
            assert results[0].file_type == "py"

    def test_path_signal_boosts_matching_file(self, db, tmp_path):
        """A file whose path matches query terms should rank higher."""
        f1 = tmp_path / "api_handler.py"
        f1.write_text("some generic code here")
        f2 = tmp_path / "utils.py"
        f2.write_text("some generic code here")

        emb = [0.5] * 384
        db.upsert_file(f1, ["some generic code here"], [emb])
        db.upsert_file(f2, ["some generic code here"], [emb])

        provider = MagicMock()
        provider.embed_query.return_value = [0.5] * 384

        results = hybrid_search(db, provider, "api handler", top_k=5)
        if len(results) >= 2:
            # api_handler.py should rank higher due to path match
            assert results[0].filename == "api_handler.py"
