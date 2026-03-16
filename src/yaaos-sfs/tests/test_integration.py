"""Integration tests — end-to-end indexing and search.

These tests use a real embedding model (all-MiniLM-L6-v2) for realistic
semantic search validation. The module is skipped automatically if the
model cannot be loaded (e.g., no network on first run, missing
sentence-transformers).
"""

from __future__ import annotations

import pytest

from yaaos_sfs.config import Config
from yaaos_sfs.db import Database
from yaaos_sfs.extractors import extract_text
from yaaos_sfs.chunkers import chunk_text
from yaaos_sfs.search import hybrid_search

_load_err = None
_provider = None
try:
    from yaaos_sfs.providers.local import LocalEmbeddingProvider

    _provider = LocalEmbeddingProvider("all-MiniLM-L6-v2")
except Exception as exc:
    _load_err = exc

pytestmark = pytest.mark.skipif(
    _provider is None,
    reason=f"Embedding model unavailable: {_load_err}",
)


@pytest.fixture(scope="module")
def provider():
    """Shared provider — loaded once at module import time."""
    return _provider


@pytest.fixture
def integration_env(tmp_path, provider):
    """Set up a complete environment: config, db, files."""
    watch_dir = tmp_path / "semantic"
    watch_dir.mkdir()

    config = Config(
        watch_dir=watch_dir,
        db_path=tmp_path / "sfs.db",
        embedding_dims=provider.dims,
    )

    db = Database(config.db_path, embedding_dims=provider.dims)

    # Create test files
    (watch_dir / "api_notes.md").write_text(
        "# API Redesign Notes\n\n"
        "We need to refactor the authentication module.\n"
        "The current JWT implementation has security issues.\n"
        "Consider switching to OAuth2 with PKCE flow.\n"
    )
    (watch_dir / "database.py").write_text(
        "import sqlite3\n\n"
        "def connect_database(path: str):\n"
        '    """Connect to SQLite database."""\n'
        "    conn = sqlite3.connect(path)\n"
        "    conn.row_factory = sqlite3.Row\n"
        "    return conn\n\n"
        "def query_users(conn):\n"
        '    return conn.execute("SELECT * FROM users").fetchall()\n'
    )
    (watch_dir / "shopping_list.txt").write_text(
        "Groceries:\n- Eggs\n- Milk\n- Bread\n- Butter\n- Cheese\n"
    )
    (watch_dir / "deploy.sh").write_text(
        "#!/bin/bash\n"
        "docker build -t myapp .\n"
        "docker push registry.example.com/myapp\n"
        "kubectl rollout restart deployment/myapp\n"
    )

    yield {"config": config, "db": db, "provider": provider, "watch_dir": watch_dir}

    db.close()


def _index_file(db, provider, path, config):
    """Helper to index a single file."""
    text = extract_text(path)
    if not text or not text.strip():
        return
    chunks = chunk_text(text, chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
    if not chunks:
        return
    embeddings = provider.embed(chunks)
    db.upsert_file(path, chunks, embeddings)


class TestEndToEnd:
    def test_index_and_search_api_notes(self, integration_env):
        """Index files and search for API-related content."""
        env = integration_env
        # Index all files
        for f in env["watch_dir"].iterdir():
            if f.is_file():
                _index_file(env["db"], env["provider"], f, env["config"])

        # Search for API content
        results = hybrid_search(env["db"], env["provider"], "authentication security", top_k=5)
        assert len(results) >= 1
        # The API notes file should be the top result
        assert any("api_notes" in r.filename for r in results[:2])

    def test_search_database_code(self, integration_env):
        """Search should find database-related code."""
        env = integration_env
        for f in env["watch_dir"].iterdir():
            if f.is_file():
                _index_file(env["db"], env["provider"], f, env["config"])

        results = hybrid_search(env["db"], env["provider"], "sqlite database connection", top_k=5)
        assert len(results) >= 1
        assert any("database" in r.filename for r in results[:2])

    def test_search_unrelated_query(self, integration_env):
        """Search for something not in any file should return fewer/weaker results."""
        env = integration_env
        for f in env["watch_dir"].iterdir():
            if f.is_file():
                _index_file(env["db"], env["provider"], f, env["config"])

        results = hybrid_search(env["db"], env["provider"], "quantum physics black holes", top_k=5)
        # Should still return something (vector similarity always finds nearest)
        # but scores should be lower
        if results:
            related = hybrid_search(env["db"], env["provider"], "database query users", top_k=5)
            if related:
                assert related[0].score >= results[0].score

    def test_reindex_after_edit(self, integration_env):
        """Editing a file and re-indexing should update search results."""
        env = integration_env

        f = env["watch_dir"] / "new_feature.py"
        f.write_text("def old_function(): pass")
        _index_file(env["db"], env["provider"], f, env["config"])

        # Verify it's indexed
        stats = env["db"].get_stats()
        initial_files = stats["files"]

        # Edit the file
        f.write_text("def machine_learning_pipeline(): pass")
        _index_file(env["db"], env["provider"], f, env["config"])

        # File count should be same (upsert, not duplicate)
        stats = env["db"].get_stats()
        assert stats["files"] == initial_files

        # New content should be searchable
        results = hybrid_search(env["db"], env["provider"], "machine learning", top_k=5)
        assert any("new_feature" in r.filename for r in results[:3])

    def test_remove_and_search(self, integration_env):
        """Removing a file should remove it from search results."""
        env = integration_env

        f = env["watch_dir"] / "temporary.txt"
        f.write_text("unique temporary content zxcvbnm")
        _index_file(env["db"], env["provider"], f, env["config"])

        # Should find it
        results = hybrid_search(env["db"], env["provider"], "unique temporary zxcvbnm", top_k=5)
        assert any("temporary" in r.filename for r in results)

        # Remove it
        env["db"].remove_file(f)

        # Should NOT find it
        results = hybrid_search(env["db"], env["provider"], "unique temporary zxcvbnm", top_k=5)
        assert not any("temporary" in r.filename for r in results)

    def test_stats_accuracy(self, integration_env):
        """Stats should accurately reflect indexed data."""
        env = integration_env

        # Start fresh by removing all
        for f in env["watch_dir"].iterdir():
            if f.is_file():
                env["db"].remove_file(f)

        # Index specific files
        files_to_index = list(env["watch_dir"].iterdir())
        text_files = [
            f for f in files_to_index if f.is_file() and f.suffix in [".md", ".py", ".txt", ".sh"]
        ]

        for f in text_files:
            _index_file(env["db"], env["provider"], f, env["config"])

        stats = env["db"].get_stats()
        assert stats["files"] == len(text_files)
        assert stats["chunks"] >= len(text_files)  # At least 1 chunk per file
