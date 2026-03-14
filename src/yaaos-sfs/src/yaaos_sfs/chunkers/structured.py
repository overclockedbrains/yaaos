"""Structured data chunker — JSON, YAML, CSV key-value extraction.

Extracts key-value pairs, headers, and data as readable text chunks.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("yaaos-sfs")


def chunk_json(text: str, config: dict) -> list[str]:
    """Chunk JSON by extracting key-value pairs as readable text."""
    chunk_size = config.get("chunk_size", 512)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []  # Fall back to default chunking

    lines = _flatten_json(data, prefix="")
    if not lines:
        return []

    return _merge_lines(lines, chunk_size)


def _flatten_json(obj, prefix: str = "", depth: int = 0) -> list[str]:
    """Flatten JSON into key: value lines."""
    if depth > 10:  # Prevent infinite recursion
        return [f"{prefix}: {str(obj)[:200]}"]

    lines = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                lines.extend(_flatten_json(value, new_prefix, depth + 1))
            else:
                lines.append(f"{new_prefix}: {value}")
    elif isinstance(obj, list):
        if len(obj) > 50:  # Large arrays: summarize
            lines.append(f"{prefix}: [{len(obj)} items]")
            for i, item in enumerate(obj[:10]):
                lines.extend(_flatten_json(item, f"{prefix}[{i}]", depth + 1))
            lines.append(f"... and {len(obj) - 10} more items")
        else:
            for i, item in enumerate(obj):
                lines.extend(_flatten_json(item, f"{prefix}[{i}]", depth + 1))
    else:
        lines.append(f"{prefix}: {obj}")

    return lines


def chunk_yaml(text: str, config: dict) -> list[str]:
    """Chunk YAML by splitting on top-level keys."""
    chunk_size = config.get("chunk_size", 512)

    # Try to parse YAML
    try:
        import yaml

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            lines = _flatten_json(data, prefix="")
            return _merge_lines(lines, chunk_size)
    except Exception:
        pass

    # Fallback: split on top-level keys (lines starting without indentation)
    sections = re.split(r"\n(?=\S)", text)
    sections = [s.strip() for s in sections if s.strip()]

    if not sections:
        return []

    return _merge_lines(sections, chunk_size)


def chunk_csv(text: str, config: dict) -> list[str]:
    """Chunk CSV/TSV by rows, preserving header context."""
    chunk_size = config.get("chunk_size", 512)

    lines = text.strip().split("\n")
    if not lines:
        return []

    header = lines[0]
    chunks = []
    current_lines = [f"Headers: {header}"]
    current_words = len(header.split())

    for line in lines[1:]:
        line_words = len(line.split())
        if current_words + line_words > chunk_size and len(current_lines) > 1:
            chunks.append("\n".join(current_lines))
            current_lines = [f"Headers: {header}"]
            current_words = len(header.split())

        current_lines.append(line)
        current_words += line_words

    if len(current_lines) > 1:
        chunks.append("\n".join(current_lines))

    return chunks


def _merge_lines(lines: list[str], chunk_size: int) -> list[str]:
    """Merge lines into chunks that fit within the word limit."""
    chunks = []
    current = []
    current_words = 0

    for line in lines:
        words = len(line.split())
        if current_words + words > chunk_size and current:
            chunks.append("\n".join(current))
            current = []
            current_words = 0

        current.append(line)
        current_words += words

    if current:
        chunks.append("\n".join(current))

    return chunks


def register_chunkers() -> None:
    """Register structured data chunkers."""
    from . import register

    register([".json"], chunk_json)
    register([".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"], chunk_yaml)
    register([".csv", ".tsv"], chunk_csv)
