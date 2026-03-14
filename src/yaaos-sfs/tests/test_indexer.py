"""Tests for text extraction and chunking."""

from __future__ import annotations

from yaaos_sfs.indexer import extract_text, chunk_text


class TestExtractText:
    def test_extract_plain_text(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello, world!")
        assert extract_text(f) == "Hello, world!"

    def test_extract_python_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    return 42\n")
        text = extract_text(f)
        assert "def foo" in text
        assert "return 42" in text

    def test_extract_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = extract_text(f)
        assert result == ""

    def test_extract_binary_file_returns_garbage_not_crash(self, tmp_path):
        """Binary files should not crash extract_text (returns replacement chars)."""
        f = tmp_path / "binary.txt"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        result = extract_text(f)
        assert result is not None  # Should not crash

    def test_extract_nonexistent_file(self, tmp_path):
        f = tmp_path / "nope.txt"
        result = extract_text(f)
        assert result is None

    def test_extract_utf8_with_special_chars(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("Héllo wörld! 日本語テスト 🎉", encoding="utf-8")
        result = extract_text(f)
        assert "Héllo" in result
        assert "日本語" in result


class TestChunkText:
    def test_short_text_single_chunk(self):
        text = "Hello world, this is a short text."
        chunks = chunk_text(text, chunk_size=512)
        assert len(chunks) == 1
        assert chunks[0] == text.strip()

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []
        assert chunk_text(None) == []

    def test_long_text_multiple_chunks(self):
        # Create text with 100 words
        words = [f"word{i}" for i in range(100)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=30, overlap=5)
        assert len(chunks) > 1
        # Each chunk should have roughly 30 words (last may be smaller)
        for chunk in chunks[:-1]:
            assert len(chunk.split()) == 30

    def test_overlap_exists(self):
        words = [f"w{i}" for i in range(50)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        assert len(chunks) >= 2
        # Words from end of chunk 1 should appear at start of chunk 2
        words_c1 = chunks[0].split()
        words_c2 = chunks[1].split()
        overlap = set(words_c1[-5:]) & set(words_c2[:5])
        assert len(overlap) > 0

    def test_chunk_size_respected(self):
        words = [f"word{i}" for i in range(200)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=50, overlap=10)
        for chunk in chunks[:-1]:
            assert len(chunk.split()) == 50
