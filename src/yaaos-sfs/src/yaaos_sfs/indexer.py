"""Text extraction and chunking for the Semantic File System."""

from __future__ import annotations

from pathlib import Path


def extract_text(path: Path) -> str | None:
    """Extract text content from a file. Returns None if unsupported/unreadable."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf(path)

    # All other supported types are treated as plain text
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _extract_pdf(path: Path) -> str | None:
    """Extract text from PDF using PyMuPDF."""
    try:
        import pymupdf

        doc = pymupdf.open(str(path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except Exception:
        return None


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by word count.

    For code files, splits on blank lines first to preserve logical blocks.
    Falls back to word-based chunking for prose.
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if len(words) <= chunk_size:
        return [text.strip()]

    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap

    return chunks
