"""Tests for the file watcher daemon components."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yaaos_sfs.daemon import SFSHandler, _initial_scan, _get_provider


class TestSFSHandler:
    def _make_handler(self, config):
        db = MagicMock()
        provider = MagicMock()
        provider.embed.return_value = [[0.1] * 384]
        provider.dims = 384
        return SFSHandler(db, provider, config)

    def test_should_index_supported_file(self, config):
        handler = self._make_handler(config)
        f = config.watch_dir / "test.py"
        f.write_text("print('hi')")
        assert handler._should_index(f) is True

    def test_should_not_index_hidden_file(self, config):
        handler = self._make_handler(config)
        f = config.watch_dir / ".hidden"
        f.write_text("secret")
        assert handler._should_index(f) is False

    def test_should_not_index_unsupported_extension(self, config):
        handler = self._make_handler(config)
        f = config.watch_dir / "binary.exe"
        f.write_bytes(b"\x00\x01")
        assert handler._should_index(f) is False

    def test_should_not_index_directory(self, config):
        handler = self._make_handler(config)
        d = config.watch_dir / "subdir"
        d.mkdir()
        assert handler._should_index(d) is False

    def test_should_index_all_supported_types(self, config):
        handler = self._make_handler(config)
        for ext in [".py", ".js", ".ts", ".md", ".txt", ".json", ".yaml", ".toml", ".sh", ".rs"]:
            f = config.watch_dir / f"test{ext}"
            f.write_text("content")
            assert handler._should_index(f) is True, f"Should index {ext}"

    def test_index_file_calls_db(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "test.py"
        f.write_text("def foo(): pass")

        with patch("yaaos_sfs.daemon.extract_text", return_value="def foo(): pass"):
            with patch("yaaos_sfs.daemon.chunk_text", return_value=["def foo(): pass"]):
                handler._index_file(f)

        handler.db.upsert_file.assert_called_once()

    def test_index_file_skips_if_not_needed(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = False

        f = config.watch_dir / "cached.py"
        f.write_text("already indexed")
        handler._index_file(f)

        handler.db.upsert_file.assert_not_called()

    def test_index_file_handles_empty_text(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "empty.py"
        f.write_text("")

        with patch("yaaos_sfs.daemon.extract_text", return_value=""):
            handler._index_file(f)

        handler.db.upsert_file.assert_not_called()

    def test_index_file_handles_extraction_error(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "bad.py"
        f.write_text("bad content")

        with patch("yaaos_sfs.daemon.extract_text", side_effect=Exception("read error")):
            # Should not raise
            handler._index_file(f)

        handler.db.upsert_file.assert_not_called()


class TestInitialScan:
    def test_scans_files(self, config):
        # Create files in watch dir
        (config.watch_dir / "a.py").write_text("print(1)")
        (config.watch_dir / "b.md").write_text("# Hello")
        (config.watch_dir / ".hidden").write_text("skip")
        (config.watch_dir / "c.exe").write_bytes(b"\x00")

        handler = MagicMock(spec=SFSHandler)
        handler.config = config
        # Make _should_index behave like real handler
        handler._should_index = SFSHandler._should_index.__get__(handler)

        _initial_scan(handler, config.watch_dir)

        # Should have tried to index .py and .md, not .hidden or .exe
        assert handler._index_file.call_count == 2


class TestGetProvider:
    def test_default_local_provider(self, config):
        provider = _get_provider(config)
        from yaaos_sfs.providers.local import LocalEmbeddingProvider

        assert isinstance(provider, LocalEmbeddingProvider)

    def test_openai_without_key_exits(self, config):
        config.embedding_provider = "openai"
        config.openai_api_key = None
        with pytest.raises(SystemExit):
            _get_provider(config)
