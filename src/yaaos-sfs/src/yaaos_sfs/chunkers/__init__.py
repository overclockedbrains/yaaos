"""Chunker registry — maps file types to chunking strategies.

Each chunker takes (text, config_dict) and returns list[str] of chunks.
Falls back to fixed-size word-based chunking if no specialized chunker exists.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger("yaaos-sfs")

# Type alias for chunker functions
Chunker = Callable[[str, dict], list[str]]

# Global registry: extension -> chunker function
_REGISTRY: dict[str, Chunker] = {}


def register(extensions: list[str], func: Chunker) -> None:
    """Register a chunker for one or more extensions."""
    for ext in extensions:
        _REGISTRY[ext.lower()] = func


def get_chunker(path: Path) -> Chunker | None:
    """Get the specialized chunker for a file, or None to use default."""
    return _REGISTRY.get(path.suffix.lower())


def chunk_text(
    text: str,
    path: Path | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[str]:
    """Chunk text using the best available strategy for the file type.

    If a specialized chunker is registered for the file extension, uses that.
    Otherwise falls back to fixed-size word-based chunking.
    """
    if not text or not text.strip():
        return []

    config = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}

    # Try specialized chunker
    if path is not None:
        chunker = get_chunker(path)
        if chunker is not None:
            try:
                chunks = chunker(text, config)
                if chunks:
                    return chunks
            except Exception as e:
                log.warning(f"Specialized chunker failed for {path.name}, falling back: {e}")

    # Default: fixed-size word-based chunking
    return _fixed_size_chunks(text, chunk_size, chunk_overlap)


def _fixed_size_chunks(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    if len(words) <= chunk_size:
        stripped = text.strip()
        return [stripped] if stripped else []

    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        if end >= len(words):
            break
        start = end - overlap

    return chunks


def _register_all() -> None:
    """Register all built-in chunkers."""
    try:
        from .code import register_chunkers as register_code

        register_code()
    except Exception as e:
        log.debug(f"Code chunkers not available: {e}")

    try:
        from .document import register_chunkers as register_docs

        register_docs()
    except Exception as e:
        log.debug(f"Document chunkers not available: {e}")

    try:
        from .structured import register_chunkers as register_structured

        register_structured()
    except Exception as e:
        log.debug(f"Structured chunkers not available: {e}")


# Auto-register on import
_register_all()
