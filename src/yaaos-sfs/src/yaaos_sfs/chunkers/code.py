"""Code chunker — tree-sitter AST-aware chunking with fallback.

Extracts functions, classes, and methods as individual chunks.
Each chunk is prefixed with file path and language context.
Falls back to fixed-size chunking if tree-sitter is not available or parsing fails.
"""

from __future__ import annotations

import logging

log = logging.getLogger("yaaos-sfs")

# Map extensions to tree-sitter language names
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".cs": "c_sharp",
}

# Node types that represent top-level symbols we want to extract
_SYMBOL_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition",
                    "arrow_function", "export_statement"},
    "typescript": {"function_declaration", "class_declaration", "method_definition",
                    "arrow_function", "export_statement", "interface_declaration",
                    "type_alias_declaration"},
    "rust": {"function_item", "impl_item", "struct_item", "enum_item", "trait_item",
             "mod_item"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier", "namespace_definition"},
    "java": {"class_declaration", "method_declaration", "interface_declaration",
             "constructor_declaration"},
    "ruby": {"method", "class", "module"},
    "c_sharp": {"class_declaration", "method_declaration", "interface_declaration",
                "struct_declaration"},
}

# Minimum chunk size (merge small symbols with neighbors)
_MIN_TOKENS = 64
# Maximum chunk size before sub-chunking
_MAX_TOKENS = 1024


def _get_parser(language: str):
    """Get a tree-sitter parser for the given language. Returns (parser, language_obj) or None."""
    try:
        import tree_sitter_languages
        parser = tree_sitter_languages.get_parser(language)
        ts_language = tree_sitter_languages.get_language(language)
        return parser, ts_language
    except Exception:
        pass

    # Try the newer tree-sitter API with individual language packages
    try:
        import tree_sitter
        lang_module = __import__(f"tree_sitter_{language.replace('-', '_')}")
        lang = tree_sitter.Language(lang_module.language())
        parser = tree_sitter.Parser(lang)
        return parser, lang
    except Exception:
        return None


def _extract_symbols(node, symbol_types: set[str], source_bytes: bytes) -> list[str]:
    """Recursively extract symbol text from AST nodes."""
    symbols = []

    if node.type in symbol_types:
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        symbols.append(text)
    else:
        for child in node.children:
            symbols.extend(_extract_symbols(child, symbol_types, source_bytes))

    return symbols


def _get_signature(text: str, language: str) -> str:
    """Extract a brief signature/header from a code block for context prefix."""
    lines = text.strip().split("\n")
    # Take first line (usually the definition/signature)
    sig = lines[0].strip() if lines else ""
    # Trim decorators for Python
    if language == "python" and sig.startswith("@"):
        for line in lines[1:]:
            if not line.strip().startswith("@"):
                sig = line.strip()
                break
    return sig


def chunk_code(text: str, config: dict, file_path: str = "", language: str = "") -> list[str]:
    """Chunk code using tree-sitter AST parsing.

    Each function/class becomes a chunk prefixed with metadata.
    Large symbols are sub-chunked. Small symbols are merged.
    Falls back to None (caller uses default) if tree-sitter fails.
    """
    if not language:
        return []

    parser_result = _get_parser(language)
    if parser_result is None:
        return []  # No tree-sitter available, caller will use fallback

    parser, _ = parser_result
    source_bytes = text.encode("utf-8")

    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    symbol_types = _SYMBOL_TYPES.get(language, set())
    if not symbol_types:
        return []

    symbols = _extract_symbols(tree.root_node, symbol_types, source_bytes)

    if not symbols:
        return []  # No symbols found, use fallback

    chunks = []
    chunk_size = config.get("chunk_size", 512)
    chunk_overlap = config.get("chunk_overlap", 50)
    prefix = f"# File: {file_path}\n# Language: {language}\n" if file_path else ""

    merge_buffer = []
    merge_tokens = 0

    for symbol_text in symbols:
        token_count = len(symbol_text.split())

        # Small symbol: accumulate for merging
        if token_count < _MIN_TOKENS:
            merge_buffer.append(symbol_text)
            merge_tokens += token_count

            if merge_tokens >= _MIN_TOKENS:
                merged = "\n\n".join(merge_buffer)
                chunks.append(f"{prefix}{merged}")
                merge_buffer.clear()
                merge_tokens = 0
            continue

        # Flush any pending small symbols first
        if merge_buffer:
            merged = "\n\n".join(merge_buffer)
            chunks.append(f"{prefix}{merged}")
            merge_buffer.clear()
            merge_tokens = 0

        # Large symbol: sub-chunk with signature prefix
        if token_count > _MAX_TOKENS:
            sig = _get_signature(symbol_text, language)
            sig_prefix = f"{prefix}# Symbol: {sig}\n"
            words = symbol_text.split()
            start = 0
            while start < len(words):
                end = min(start + chunk_size, len(words))
                sub_chunk = " ".join(words[start:end])
                chunks.append(f"{sig_prefix}{sub_chunk}")
                if end >= len(words):
                    break
                start = end - chunk_overlap
        else:
            # Normal-sized symbol
            sig = _get_signature(symbol_text, language)
            chunks.append(f"{prefix}# Symbol: {sig}\n{symbol_text}")

    # Flush remaining small symbols
    if merge_buffer:
        merged = "\n\n".join(merge_buffer)
        chunks.append(f"{prefix}{merged}")

    return chunks


def _make_code_chunker(extension: str):
    """Create a chunker function bound to a specific language."""
    language = _LANG_MAP.get(extension, "")

    def chunker(text: str, config: dict) -> list[str]:
        return chunk_code(text, config, language=language)

    return chunker


def register_chunkers() -> None:
    """Register code chunkers for all supported languages."""
    from . import register

    for ext, language in _LANG_MAP.items():
        register([ext], _make_code_chunker(ext))
