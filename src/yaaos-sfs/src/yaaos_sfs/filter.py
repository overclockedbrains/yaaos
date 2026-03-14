"""
filter.py — 4-layer file filtering pipeline for YAAOS SFS (v2).
Implements the following layers to aggressively discard non-indexable files:
1. Hardcoded Ignore Layer: extremely common noise dirs (node_modules, .git, dist)
2. Pathspec (.gitignore) Layer: parses user .gitignore patterns
3. Extension whitelist Layer: from config.supported_extensions
4. Size limit Layer: ignores files > config.max_file_size_mb
"""

from __future__ import annotations

import os
from pathlib import Path
import pathspec

# Layer 1: Global default ignores (never index these, regardless of gitignore)
GLOBAL_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    "vendor",
    "dist",
    "build",
    "out",
    "target",
    "bin",
    "obj",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    ".vs",
}


class FileFilter:
    def __init__(self, watch_dir: Path, supported_extensions: list[str], max_file_size_mb: float):
        self.watch_dir = watch_dir
        self.supported_extensions = set(ext.lower() for ext in supported_extensions)
        self.max_file_size_bytes = int(max_file_size_mb * 1024 * 1024)

        self._gitignore_spec: pathspec.PathSpec | None = None
        self._load_gitignore()

    def _load_gitignore(self) -> None:
        """Load .gitignore if it exists in the root of the watch directory."""
        gitignore_path = self.watch_dir / ".gitignore"
        lines = []
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except IOError:
                pass

        # Always add our global defaults to the pathspec to be extra safe
        lines.extend([f"**/{d}/" for d in GLOBAL_IGNORED_DIRS])

        self._gitignore_spec = pathspec.PathSpec.from_lines("gitignore", lines)

    def is_dir_allowed(self, dir_path: Path) -> bool:
        """
        Fast path for directory traversal (`os.walk` or similar).
        Returns False if the directory should be completely skipped.
        """
        # 1. Hardcoded check first (fastest)
        if dir_path.name in GLOBAL_IGNORED_DIRS or dir_path.name.startswith("."):
            return False

        # 2. .gitignore check
        if self._gitignore_spec:
            try:
                rel_path = dir_path.relative_to(self.watch_dir)
                # Ensure it ends with / so pathspec knows it's a directory
                rel_str = str(rel_path).replace(os.sep, "/")
                if not rel_str.endswith("/"):
                    rel_str += "/"
                if self._gitignore_spec.match_file(rel_str):
                    return False
            except ValueError:
                pass  # Path not relative to watch_dir

        return True

    def should_index(self, path: Path, file_size: int | None = None) -> bool:
        """
        Runs the 4-layer filter on a file path.
        Returns True if the file should be chunked and indexed.
        """
        # 0. Skip hidden files
        if path.name.startswith("."):
            return False

        # 1. Check parent directories against hardcoded ignores (if not already filtered by walker)
        # Note: Depending on walker, this might be redundant, but safe.
        for part in path.parts:
            if part in GLOBAL_IGNORED_DIRS or (part.startswith(".") and part not in (".", "..")):
                return False

        # 2. Pathspec (.gitignore) Layer
        if self._gitignore_spec:
            try:
                rel_path = path.relative_to(self.watch_dir)
                rel_str = str(rel_path).replace(os.sep, "/")
                if self._gitignore_spec.match_file(rel_str):
                    return False
            except ValueError:
                pass  # Not in watch_dir

        # 3. Extension whitelist Layer
        if path.suffix.lower() not in self.supported_extensions:
            return False

        # 4. Size limit Layer
        # Fallback to stat if size wasn't provided (e.g. initial scan might provide it)
        if file_size is None:
            try:
                file_size = path.stat().st_size
            except OSError:
                return False

        if file_size > self.max_file_size_bytes or file_size == 0:
            return False

        return True
