"""Text extractors — plain text, code files, and PDF.

These are always available (no optional dependencies beyond pymupdf for PDF).
"""

from __future__ import annotations

from pathlib import Path


def extract_plaintext(path: Path) -> str | None:
    """Read a file as UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def extract_pdf(path: Path) -> str | None:
    """Extract text from PDF using PyMuPDF."""
    try:
        import pymupdf

        doc = pymupdf.open(str(path))
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n".join(pages)
    except Exception:
        return None


# Extensions handled by plain text reader
_TEXT_EXTENSIONS = [
    # Code
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cxx", ".java", ".rb", ".php", ".swift",
    ".kt", ".kts", ".scala", ".cs", ".fs", ".lua", ".pl", ".pm",
    ".r", ".R", ".jl", ".zig", ".nim", ".dart", ".ex", ".exs",
    ".erl", ".hrl", ".hs", ".ml", ".mli", ".clj", ".cljs",
    # Shell
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    # Markup & prose
    ".md", ".txt", ".rst", ".org", ".adoc", ".tex", ".latex",
    # Web
    ".html", ".htm", ".xml", ".xhtml", ".svg",
    ".css", ".scss", ".sass", ".less",
    # Config & data
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".env.example", ".properties",
    # Data (small)
    ".csv", ".tsv",
    # Other
    ".sql", ".graphql", ".gql", ".proto",
    ".dockerfile", ".makefile", ".cmake",
    ".gitignore", ".gitattributes", ".editorconfig",
]


def register_extractors() -> None:
    """Register all text-based extractors."""
    from . import register

    register(_TEXT_EXTENSIONS, extract_plaintext)
    register([".pdf"], extract_pdf)
