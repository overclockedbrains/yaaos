"""Shared fixtures for SFS tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_dir(tmp_path):
    """A temporary directory for test files."""
    return tmp_path


@pytest.fixture
def sample_files(tmp_dir):
    """Create a set of sample files for testing."""
    files = {}

    # Python file
    py = tmp_dir / "hello.py"
    py.write_text('def greet(name):\n    return f"Hello, {name}!"\n')
    files["py"] = py

    # Markdown file
    md = tmp_dir / "notes.md"
    md.write_text(
        "# Meeting Notes\n\nDiscussed the API redesign.\n\n## Action Items\n\n- Refactor auth module\n- Update docs\n"
    )
    files["md"] = md

    # JSON file
    js = tmp_dir / "config.json"
    js.write_text('{"database": {"host": "localhost", "port": 5432}, "debug": true}\n')
    files["json"] = js

    # Plain text
    txt = tmp_dir / "readme.txt"
    txt.write_text(
        "This is a readme file for the YAAOS project.\nIt explains the semantic file system.\n"
    )
    files["txt"] = txt

    # Hidden file (should be skipped)
    hidden = tmp_dir / ".hidden"
    hidden.write_text("secret stuff")
    files["hidden"] = hidden

    # Unsupported extension
    exe = tmp_dir / "program.exe"
    exe.write_bytes(b"\x00\x01\x02\x03")
    files["exe"] = exe

    return files


@pytest.fixture
def config(tmp_dir):
    """Create a test Config pointing to tmp dirs."""
    from yaaos_sfs.config import Config

    db_path = tmp_dir / "test.db"
    watch_dir = tmp_dir / "watch"
    watch_dir.mkdir()

    return Config(
        watch_dir=watch_dir,
        db_path=db_path,
        embedding_provider="local",
        embedding_model="all-MiniLM-L6-v2",
        embedding_dims=384,
        chunk_size=512,
        chunk_overlap=50,
    )


@pytest.fixture
def db(config):
    """Create a test database."""
    from yaaos_sfs.db import Database

    database = Database(config.db_path, embedding_dims=config.embedding_dims)
    yield database
    database.close()
