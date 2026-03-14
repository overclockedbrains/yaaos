"""Tests for the chunker registry and built-in chunkers."""

from __future__ import annotations

from pathlib import Path

from yaaos_sfs.chunkers import chunk_text, _fixed_size_chunks
from yaaos_sfs.chunkers.document import chunk_markdown, chunk_prose, chunk_document
from yaaos_sfs.chunkers.structured import chunk_json, chunk_csv


class TestChunkerRegistry:
    def test_default_chunking_no_path(self):
        """Without a path, should use fixed-size chunking."""
        text = "Hello world, short text."
        chunks = chunk_text(text)
        assert chunks == ["Hello world, short text."]

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_long_text_chunks(self):
        words = [f"word{i}" for i in range(100)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=30, chunk_overlap=5)
        assert len(chunks) > 1

    def test_path_dispatches_to_chunker(self):
        """Markdown path should use markdown chunker."""
        text = "# Heading 1\n\nContent under heading 1.\n\n## Heading 2\n\nContent under heading 2."
        chunks = chunk_text(text, path=Path("notes.md"), chunk_size=512)
        assert len(chunks) >= 1
        # Should preserve heading structure
        assert any("Heading" in c for c in chunks)


class TestFixedSizeChunks:
    def test_short_text(self):
        chunks = _fixed_size_chunks("short", 512, 50)
        assert chunks == ["short"]

    def test_overlap(self):
        words = [f"w{i}" for i in range(50)]
        text = " ".join(words)
        chunks = _fixed_size_chunks(text, 20, 5)
        assert len(chunks) >= 2
        # Check overlap
        w1 = chunks[0].split()
        w2 = chunks[1].split()
        overlap = set(w1[-5:]) & set(w2[:5])
        assert len(overlap) > 0


class TestMarkdownChunker:
    def test_splits_on_headings(self):
        text = "# Intro\n\nIntro content.\n\n## Methods\n\nMethod content.\n\n## Results\n\nResult content."
        chunks = chunk_markdown(text, {"chunk_size": 512})
        assert len(chunks) >= 3

    def test_preserves_heading_in_chunk(self):
        text = "# Title\n\nParagraph one.\n\n## Section\n\nParagraph two."
        chunks = chunk_markdown(text, {"chunk_size": 512})
        assert any("# Title" in c for c in chunks)
        assert any("## Section" in c for c in chunks)

    def test_preamble_before_headings(self):
        text = "Preamble text here.\n\n# First Heading\n\nContent."
        chunks = chunk_markdown(text, {"chunk_size": 512})
        assert any("Preamble" in c for c in chunks)

    def test_no_headings_falls_back_to_prose(self):
        text = "Just a plain paragraph.\n\nAnother paragraph.\n\nThird paragraph."
        chunks = chunk_markdown(text, {"chunk_size": 512})
        assert len(chunks) >= 1

    def test_large_section_subchunked(self):
        large = "# Big Section\n\n" + " ".join(f"word{i}" for i in range(1000))
        chunks = chunk_markdown(large, {"chunk_size": 200, "chunk_overlap": 20})
        assert len(chunks) > 1


class TestProseChunker:
    def test_merges_small_paragraphs(self):
        text = "Short one.\n\nShort two.\n\nShort three."
        chunks = chunk_prose(text, {"chunk_size": 512})
        assert len(chunks) == 1  # All small, merged

    def test_splits_large_paragraphs(self):
        text = " ".join(f"word{i}" for i in range(600))
        chunks = chunk_prose(text, {"chunk_size": 200, "chunk_overlap": 20})
        assert len(chunks) > 1

    def test_empty_text(self):
        assert chunk_prose("", {"chunk_size": 512}) == []
        assert chunk_prose("  \n\n  ", {"chunk_size": 512}) == []


class TestDocumentChunker:
    def test_detects_markdown(self):
        text = "# Heading\n\nContent."
        chunks = chunk_document(text, {"chunk_size": 512})
        assert any("Heading" in c for c in chunks)

    def test_detects_page_breaks(self):
        text = "Page 1 content.\n\n\n\nPage 2 content.\n\n\n\nPage 3 content."
        chunks = chunk_document(text, {"chunk_size": 512})
        assert len(chunks) >= 1


class TestJsonChunker:
    def test_simple_object(self):
        text = '{"name": "Alice", "age": 30, "city": "NYC"}'
        chunks = chunk_json(text, {"chunk_size": 512})
        assert len(chunks) >= 1
        assert "name: Alice" in chunks[0]

    def test_nested_object(self):
        text = '{"db": {"host": "localhost", "port": 5432}, "debug": true}'
        chunks = chunk_json(text, {"chunk_size": 512})
        assert len(chunks) >= 1
        assert "db.host: localhost" in chunks[0]

    def test_invalid_json_returns_empty(self):
        assert chunk_json("not json {{{", {"chunk_size": 512}) == []

    def test_large_array_summarized(self):
        import json

        data = list(range(100))
        text = json.dumps(data)
        chunks = chunk_json(text, {"chunk_size": 512})
        assert len(chunks) >= 1
        assert "100 items" in chunks[0]


class TestCsvChunker:
    def test_preserves_header(self):
        text = "Name,Age,City\nAlice,30,NYC\nBob,25,London"
        chunks = chunk_csv(text, {"chunk_size": 512})
        assert len(chunks) >= 1
        assert "Headers:" in chunks[0]
        assert "Alice" in chunks[0]

    def test_large_csv_splits(self):
        lines = ["col1,col2,col3"]
        for i in range(200):
            lines.append(f"val{i}_a,val{i}_b,val{i}_c")
        text = "\n".join(lines)
        chunks = chunk_csv(text, {"chunk_size": 50, "chunk_overlap": 0})
        assert len(chunks) > 1
        # Each chunk should have the header
        for chunk in chunks:
            assert "Headers:" in chunk

    def test_empty_csv(self):
        assert chunk_csv("", {"chunk_size": 512}) == []
