"""Tests for the SQLite + sqlite-vec database layer."""

from __future__ import annotations

import random

from yaaos_sfs.db import _serialize_vector, _deserialize_vector


class TestVectorSerialization:
    def test_roundtrip(self):
        vec = [0.1, 0.2, 0.3, 0.4, 0.5]
        data = _serialize_vector(vec)
        result = _deserialize_vector(data)
        assert len(result) == len(vec)
        for a, b in zip(vec, result):
            assert abs(a - b) < 1e-6

    def test_empty_vector(self):
        vec = []
        data = _serialize_vector(vec)
        assert _deserialize_vector(data) == []

    def test_large_vector(self):
        vec = [random.random() for _ in range(384)]
        data = _serialize_vector(vec)
        result = _deserialize_vector(data)
        assert len(result) == 384


class TestDatabase:
    def test_schema_creation(self, db):
        """DB should create all tables on init."""
        tables = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t["name"] for t in tables}
        assert "files" in table_names
        assert "chunks" in table_names

    def test_file_needs_indexing_new_file(self, db, tmp_path):
        """New file should need indexing."""
        f = tmp_path / "new.txt"
        f.write_text("hello")
        assert db.file_needs_indexing(f) is True

    def test_file_needs_indexing_after_upsert(self, db, tmp_path):
        """File should NOT need indexing after being upserted (unchanged)."""
        f = tmp_path / "indexed.txt"
        f.write_text("hello world")

        fake_embedding = [0.1] * 384
        db.upsert_file(f, ["hello world"], [fake_embedding])
        assert db.file_needs_indexing(f) is False

    def test_file_needs_indexing_after_change(self, db, tmp_path):
        """File should need re-indexing after content changes."""
        f = tmp_path / "changing.txt"
        f.write_text("version 1")

        fake_embedding = [0.1] * 384
        db.upsert_file(f, ["version 1"], [fake_embedding])
        assert db.file_needs_indexing(f) is False

        f.write_text("version 2")
        assert db.file_needs_indexing(f) is True

    def test_upsert_creates_file_record(self, db, tmp_path):
        """Upsert should create a file record in the files table."""
        f = tmp_path / "test.py"
        f.write_text("print('hello')")

        fake_embedding = [0.1] * 384
        db.upsert_file(f, ["print('hello')"], [fake_embedding])

        row = db.conn.execute("SELECT * FROM files WHERE path = ?", (str(f),)).fetchone()
        assert row is not None
        assert row["filename"] == "test.py"
        assert row["extension"] == ".py"
        assert row["chunk_count"] == 1

    def test_upsert_creates_chunks(self, db, tmp_path):
        """Upsert should create chunk records."""
        f = tmp_path / "multi.txt"
        f.write_text("chunk1 chunk2 chunk3")

        chunks = ["chunk1", "chunk2", "chunk3"]
        embeddings = [[0.1] * 384, [0.2] * 384, [0.3] * 384]
        db.upsert_file(f, chunks, embeddings)

        rows = db.conn.execute(
            "SELECT * FROM chunks WHERE file_id = (SELECT id FROM files WHERE path = ?)",
            (str(f),),
        ).fetchall()
        assert len(rows) == 3
        assert rows[0]["chunk_text"] == "chunk1"
        assert rows[1]["chunk_index"] == 1

    def test_upsert_replaces_on_reindex(self, db, tmp_path):
        """Re-upserting should replace old chunks."""
        f = tmp_path / "reindex.txt"
        f.write_text("version 1")

        db.upsert_file(f, ["v1 chunk"], [[0.1] * 384])
        f.write_text("version 2 with more content")
        db.upsert_file(f, ["v2 chunk a", "v2 chunk b"], [[0.2] * 384, [0.3] * 384])

        rows = db.conn.execute(
            "SELECT * FROM chunks WHERE file_id = (SELECT id FROM files WHERE path = ?)",
            (str(f),),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["chunk_text"] == "v2 chunk a"

    def test_remove_file(self, db, tmp_path):
        """Removing a file should delete all its data."""
        f = tmp_path / "remove_me.txt"
        f.write_text("goodbye")

        db.upsert_file(f, ["goodbye"], [[0.1] * 384])
        assert (
            db.conn.execute("SELECT COUNT(*) as n FROM files WHERE path = ?", (str(f),)).fetchone()[
                "n"
            ]
            == 1
        )

        db.remove_file(f)
        assert (
            db.conn.execute("SELECT COUNT(*) as n FROM files WHERE path = ?", (str(f),)).fetchone()[
                "n"
            ]
            == 0
        )

    def test_remove_nonexistent_file(self, db, tmp_path):
        """Removing a file that was never indexed should not crash."""
        f = tmp_path / "never_existed.txt"
        db.remove_file(f)  # Should not raise

    def test_search_vector(self, db, tmp_path):
        """Vector search should return results ranked by similarity."""
        f = tmp_path / "searchable.txt"
        f.write_text("hello world")

        # Insert with a known embedding
        embedding = [1.0] + [0.0] * 383
        db.upsert_file(f, ["hello world"], [embedding])

        # Search with similar embedding
        query = [0.9] + [0.0] * 383
        results = db.search_vector(query, top_k=5)
        assert len(results) >= 1
        assert results[0]["chunk_text"] == "hello world"

    def test_search_fts(self, db, tmp_path):
        """FTS search should find text by keywords."""
        f = tmp_path / "searchable.txt"
        f.write_text("python database helpers")

        db.upsert_file(f, ["python database helpers"], [[0.1] * 384])

        results = db.search_fts("python database", top_k=5)
        assert len(results) >= 1
        assert "python" in results[0]["chunk_text"]

    def test_get_stats(self, db, tmp_path):
        """Stats should reflect indexed data."""
        stats = db.get_stats()
        assert stats["files"] == 0
        assert stats["chunks"] == 0

        f = tmp_path / "stat_test.txt"
        f.write_text("stats test")
        db.upsert_file(f, ["chunk1", "chunk2"], [[0.1] * 384, [0.2] * 384])

        stats = db.get_stats()
        assert stats["files"] == 1
        assert stats["chunks"] == 2
        assert stats["db_size_mb"] >= 0

    def test_multiple_files(self, db, tmp_path):
        """Should handle multiple files independently."""
        for i in range(5):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"content of file {i}")
            db.upsert_file(f, [f"content {i}"], [[float(i) * 0.1] * 384])

        stats = db.get_stats()
        assert stats["files"] == 5
        assert stats["chunks"] == 5
