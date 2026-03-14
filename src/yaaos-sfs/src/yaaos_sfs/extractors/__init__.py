"""Extractor registry — maps file extensions to text extraction functions.

Each extractor takes a Path and returns extracted text (str) or None on failure.
Extractors gracefully degrade: if an optional library is missing, that extractor
is simply not registered and the file is skipped with a warning.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger("yaaos-sfs")

# Type alias for extractor functions
Extractor = Callable[[Path], str | None]

# Global registry: extension -> extractor function
_REGISTRY: dict[str, Extractor] = {}


def register(extensions: list[str], func: Extractor) -> None:
    """Register an extractor for one or more extensions."""
    for ext in extensions:
        _REGISTRY[ext.lower()] = func


def get_extractor(path: Path) -> Extractor | None:
    """Get the extractor function for a file, or None if unsupported."""
    return _REGISTRY.get(path.suffix.lower())


def extract_text(path: Path) -> str | None:
    """Extract text from a file using the registered extractor.

    Returns None if no extractor is registered or extraction fails.
    """
    extractor = get_extractor(path)
    if extractor is None:
        return None
    try:
        return extractor(path)
    except Exception as e:
        log.warning(f"Extraction failed for {path.name}: {e}")
        return None


def get_supported_extensions() -> set[str]:
    """Return all extensions that have a registered extractor."""
    return set(_REGISTRY.keys())


def _register_all() -> None:
    """Register all built-in extractors. Called at module load time."""
    # Text extractors (always available)
    from .text import register_extractors as register_text
    register_text()

    # Document extractors (optional deps)
    try:
        from .documents import register_extractors as register_docs
        register_docs()
    except Exception as e:
        log.debug(f"Document extractors not available: {e}")

    # Media metadata extractors (optional deps)
    try:
        from .media import register_extractors as register_media
        register_media()
    except Exception as e:
        log.debug(f"Media extractors not available: {e}")


# Auto-register on import
_register_all()
