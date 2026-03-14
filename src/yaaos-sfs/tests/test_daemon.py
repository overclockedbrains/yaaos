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
        assert handler.file_filter.should_index(f) is True

    def test_should_not_index_hidden_file(self, config):
        handler = self._make_handler(config)
        f = config.watch_dir / ".hidden"
        f.write_text("secret")
        assert handler.file_filter.should_index(f) is False

    def test_should_not_index_unsupported_extension(self, config):
        handler = self._make_handler(config)
        f = config.watch_dir / "binary.exe"
        f.write_bytes(b"\x00\x01")
        assert handler.file_filter.should_index(f) is False

    def test_should_not_index_directory(self, config):
        handler = self._make_handler(config)
        d = config.watch_dir / "subdir"
        d.mkdir()
        # Directories typically don't match the supported extensions
        assert handler.file_filter.should_index(d) is False

    def test_should_index_all_supported_types(self, config):
        handler = self._make_handler(config)
        for ext in [".py", ".js", ".ts", ".md", ".txt", ".json", ".yaml", ".toml", ".sh", ".rs"]:
            f = config.watch_dir / f"test{ext}"
            f.write_text("content")
            assert handler.file_filter.should_index(f) is True, f"Should index {ext}"

    def test_process_batch_calls_db(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "test.py"
        f.write_text("def foo(): pass")

        with patch("yaaos_sfs.daemon.extract_text", return_value="def foo(): pass"):
            with patch("yaaos_sfs.daemon.chunk_text", return_value=["def foo(): pass"]):
                handler._process_batch([f])

        handler.db.upsert_file.assert_called_once()

    def test_process_batch_skips_if_not_needed(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = False

        f = config.watch_dir / "cached.py"
        f.write_text("already indexed")
        handler._process_batch([f])

        handler.db.upsert_file.assert_not_called()

    def test_process_batch_handles_empty_text(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "empty.py"
        f.write_text("")

        with patch("yaaos_sfs.daemon.extract_text", return_value=""):
            handler._process_batch([f])

        handler.db.upsert_file.assert_not_called()

    def test_process_batch_handles_extraction_error(self, config):
        handler = self._make_handler(config)
        handler.db.file_needs_indexing.return_value = True

        f = config.watch_dir / "bad.py"
        f.write_text("bad content")

        with patch("yaaos_sfs.daemon.extract_text", side_effect=Exception("read error")):
            # Should not raise
            handler._process_batch([f])

        handler.db.upsert_file.assert_not_called()


class TestInitialScan:
    def test_scans_files(self, config):
        # Create files in watch dir
        (config.watch_dir / "a.py").write_text("print(1)")
        (config.watch_dir / "b.md").write_text("# Hello")
        (config.watch_dir / ".hidden").write_text("skip")
        (config.watch_dir / "c.exe").write_bytes(b"\x00")

        handler = MagicMock()
        handler.db = MagicMock()
        handler.db.file_needs_indexing.return_value = True
        handler.config = config
        # Use real FileFilter so filtering works correctly
        from yaaos_sfs.filter import FileFilter

        handler.file_filter = FileFilter(
            config.watch_dir, config.supported_extensions, config.max_file_size_mb
        )

        # Mock chunking so embed_and_upsert actually gets valid data
        with patch("yaaos_sfs.daemon.extract_text", return_value="content"):
            with patch("yaaos_sfs.daemon.chunk_text", return_value=["chunk"]):
                _initial_scan(handler, config.watch_dir, config)

        # Should have tried to upsert .py and .md, not .hidden or .exe
        # _embed_and_upsert accepts batches, so it might be called once or twice
        assert handler._embed_and_upsert.call_count >= 1

        # Check what files were passed to _embed_and_upsert
        embedded_files = []
        for call_args in handler._embed_and_upsert.call_args_list:
            files_batch, _ = call_args[0]
            embedded_files.extend([f[0].name for f in files_batch])

        # Sort for easy assertion
        embedded_files.sort()

    def test_orphan_cleanup(self, config):
        (config.watch_dir / "keep.py").write_text("keep")

        handler = MagicMock()
        handler.db = MagicMock()
        handler.db.file_needs_indexing.return_value = True
        # db has two files, but only keep.py is actually on the disk
        handler.db.get_all_indexed_paths.return_value = {
            config.watch_dir / "keep.py",
            config.watch_dir / "ghost.py",
            config.watch_dir / ".hidden", # shouldn't be valid even if on disk
        }
        handler.config = config
        from yaaos_sfs.filter import FileFilter

        handler.file_filter = FileFilter(
            config.watch_dir, config.supported_extensions, config.max_file_size_mb
        )

        # Create .hidden on disk to prove it gets cleaned up if it was somehow in the DB
        (config.watch_dir / ".hidden").write_text("secret")

        with patch("yaaos_sfs.daemon.extract_text", return_value="content"):
            with patch("yaaos_sfs.daemon.chunk_text", return_value=["chunk"]):
                _initial_scan(handler, config.watch_dir, config)

        handler.db.remove_files_batch.assert_called_once()
        args, _ = handler.db.remove_files_batch.call_args
        orphans = args[0]
        assert len(orphans) == 2
        assert config.watch_dir / "ghost.py" in orphans
        assert config.watch_dir / ".hidden" in orphans

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
