"""Document chunker — heading/section-aware chunking for prose.

Splits on markdown headings (##), page breaks, and paragraph boundaries.
Falls back to fixed-size chunking if no structure is detected.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_PAGE_BREAK_RE = re.compile(r"\n{3,}|---\s*\n|===\s*\n|\f")


def chunk_markdown(text: str, config: dict) -> list[str]:
    """Chunk markdown by headings, keeping heading as chunk prefix."""
    chunk_size = config.get("chunk_size", 512)
    chunk_overlap = config.get("chunk_overlap", 50)

    # Find all headings and their positions
    headings = list(_HEADING_RE.finditer(text))

    if not headings:
        # No markdown headings, try paragraph-based
        return chunk_prose(text, config)

    sections = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section_text = text[start:end].strip()
        if section_text:
            sections.append(section_text)

    # Also capture any text before the first heading
    if headings and headings[0].start() > 0:
        preamble = text[: headings[0].start()].strip()
        if preamble:
            sections.insert(0, preamble)

    # Sub-chunk large sections
    chunks = []
    for section in sections:
        words = section.split()
        if len(words) <= chunk_size:
            chunks.append(section)
        else:
            # Extract heading prefix for context
            heading_match = _HEADING_RE.match(section)
            prefix = heading_match.group(0) + "\n" if heading_match else ""

            start = 0
            while start < len(words):
                end = min(start + chunk_size, len(words))
                chunk = " ".join(words[start:end])
                if start > 0 and prefix:
                    chunk = prefix + chunk
                chunks.append(chunk.strip())
                if end >= len(words):
                    break
                start = end - chunk_overlap

    return chunks if chunks else [text.strip()]


def chunk_prose(text: str, config: dict) -> list[str]:
    """Chunk prose by paragraph boundaries, merging small paragraphs."""
    chunk_size = config.get("chunk_size", 512)
    chunk_overlap = config.get("chunk_overlap", 50)

    # Split on double newlines (paragraphs)
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    if len(paragraphs) == 1:
        words = paragraphs[0].split()
        if len(words) <= chunk_size:
            return [paragraphs[0]]
        # Single long paragraph — fall back to word chunking
        return _word_chunks(paragraphs[0], chunk_size, chunk_overlap)

    # Merge small paragraphs into chunks
    chunks = []
    current_parts = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())

        if current_words + para_words > chunk_size and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Overlap: keep last paragraph
            if chunk_overlap > 0 and current_parts:
                last = current_parts[-1]
                current_parts = [last]
                current_words = len(last.split())
            else:
                current_parts = []
                current_words = 0

        current_parts.append(para)
        current_words += para_words

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def chunk_document(text: str, config: dict) -> list[str]:
    """General document chunker — detects structure and dispatches."""
    # Check for markdown headings
    if _HEADING_RE.search(text):
        return chunk_markdown(text, config)

    # Check for page breaks (PDFs, multi-page docs)
    if _PAGE_BREAK_RE.search(text):
        pages = _PAGE_BREAK_RE.split(text)
        pages = [p.strip() for p in pages if p.strip()]
        if len(pages) > 1:
            chunks = []
            for page in pages:
                chunks.extend(chunk_prose(page, config))
            return chunks

    # Default: prose chunking
    return chunk_prose(text, config)


def _word_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Simple word-based chunking."""
    words = text.split()
    if len(words) <= chunk_size:
        return [text.strip()] if text.strip() else []

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        if end >= len(words):
            break
        start = end - overlap

    return chunks


def register_chunkers() -> None:
    """Register document chunkers."""
    from . import register

    # Markdown and text prose
    register([".md", ".txt", ".rst", ".org", ".adoc"], chunk_markdown)
    # Rich documents extracted as text
    register([".pdf", ".docx", ".pptx", ".epub", ".rtf"], chunk_document)
    # HTML/XML
    register([".html", ".htm", ".xml", ".xhtml"], chunk_prose)
