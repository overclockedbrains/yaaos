#!/usr/bin/env python3
"""
generate_corpus.py — Generate a synthetic file corpus for SFS stress testing.

Creates a realistic directory tree with a configurable total size, including:
  - "Real" content (src/, docs/) → should be indexed
  - "Noise" dirs (node_modules/, .git/, dist/, assets/) → should be SKIPPED by v2

Usage:
    python generate_corpus.py --size-gb 1 --output C:\\Temp\\sfs_corpus_1gb
    python generate_corpus.py --size-gb 30 --output C:\\Temp\\sfs_corpus_30gb
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Size budget: what fraction of total size goes into each category
# These ratios mirror the real-world breakdown from the SFS v2 plan.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "node_modules": 0.50,  # 50% — dependency junk
    "git_objects": 0.10,  # 10% — .git/objects/...
    "dist": 0.07,  # 7%  — build artifacts
    "assets": 0.03,  # 3%  — binary images/fonts
    "src": 0.20,  # 20% — actual source code ← indexed
    "docs": 0.10,  # 10% — markdown/txt docs  ← indexed
}

# File templates for "real" content (src + docs)
PYTHON_TEMPLATES = [
    '''\
"""Module: {name}"""
from __future__ import annotations
import os, sys, json
from pathlib import Path
from typing import Optional, List, Dict

class {cls}:
    """A class that handles {topic} operations."""

    def __init__(self, config: Dict):
        self.config = config
        self.initialized = False

    def setup(self) -> bool:
        """Initialize the {topic} subsystem."""
        try:
            self._load_config()
            self.initialized = True
            return True
        except Exception as e:
            print(f"Setup failed: {{e}}")
            return False

    def _load_config(self):
        path = Path(self.config.get("path", "/tmp/default"))
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)

    def process(self, items: List[str]) -> List[Dict]:
        """Process a batch of {topic} items."""
        results = []
        for item in items:
            results.append({{"item": item, "status": "ok", "timestamp": 0}})
        return results

    def close(self):
        self.initialized = False


def main():
    cfg = {{"path": "/tmp/{name}", "debug": False}}
    obj = {cls}(cfg)
    obj.setup()
    result = obj.process(["a", "b", "c"])
    print(json.dumps(result, indent=2))
    obj.close()


if __name__ == "__main__":
    main()
''',
    '''\
"""Utilities for {name}."""
from pathlib import Path
import hashlib, time, logging

log = logging.getLogger("{name}")

SUPPORTED_TYPES = [".txt", ".md", ".py", ".json", ".yaml"]

def compute_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()

def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_TYPES

def retry(fn, attempts=3, delay=0.5):
    """Retry a function up to N times."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            log.warning(f"Attempt {{i+1}} failed: {{e}}")
            time.sleep(delay)
    raise RuntimeError(f"Failed after {{attempts}} attempts")
''',
]

JS_TEMPLATES = [
    """\
// {name}.js — {topic} module
'use strict';

const path = require('path');
const fs = require('fs');

class {cls} {{
  constructor(options = {{}}) {{
    this.options = options;
    this.cache = new Map();
  }}

  async init() {{
    const configPath = path.join(process.env.HOME || '/tmp', '.config', '{name}');
    if (fs.existsSync(configPath)) {{
      const raw = fs.readFileSync(configPath, 'utf-8');
      Object.assign(this.options, JSON.parse(raw));
    }}
    return this;
  }}

  process(items) {{
    return items.map(item => ({{
      input: item,
      output: item.toString().toLowerCase(),
      cached: this.cache.has(item),
    }}));
  }}

  clear() {{
    this.cache.clear();
  }}
}}

module.exports = {{ {cls} }};
""",
]

MD_TEMPLATES = [
    """\
# {topic} Documentation

## Overview

This document describes the **{topic}** component of the YAAOS system.
{topic} is responsible for managing {name} operations at scale.

## Architecture

The system follows a layered design:

```
Input Layer
    ↓
Processing Layer ({cls})
    ↓
Output Layer
```

## Configuration

```toml
[{name}]
enabled = true
workers = 4
timeout_ms = 5000
```

## Usage

```python
from yaaos.{name} import {cls}

obj = {cls}(config)
obj.setup()
results = obj.process(items)
```

## Performance Notes

- Batch size: 32 (optimal for most hardware)
- Memory usage: ~350 MB at rest
- Throughput: ~500 items/second on Ryzen 7 4800H

## Changelog

- 2026-03 Initial implementation
- 2026-02 Architecture review
- 2026-01 Planning phase
""",
]

JSON_TEMPLATES = [
    """\
{{
  "name": "{name}",
  "version": "1.0.0",
  "description": "Configuration for {topic}",
  "settings": {{
    "enabled": true,
    "workers": 4,
    "batch_size": 32,
    "timeout_ms": 5000,
    "retry_count": 3,
    "log_level": "INFO"
  }},
  "paths": {{
    "data": "/tmp/{name}/data",
    "cache": "/tmp/{name}/cache",
    "logs": "/tmp/{name}/logs"
  }},
  "providers": ["local", "openai"],
  "features": {{
    "semantic_search": true,
    "batch_embedding": true,
    "stat_change_detection": true
  }}
}}
""",
]

TOPICS = [
    "indexing",
    "search",
    "embedding",
    "filtering",
    "chunking",
    "caching",
    "monitoring",
    "scheduling",
    "routing",
    "parsing",
    "validation",
    "serialization",
    "streaming",
    "batching",
    "compression",
]

CLASSES = [
    "Manager",
    "Handler",
    "Processor",
    "Worker",
    "Engine",
    "Controller",
    "Dispatcher",
    "Resolver",
    "Executor",
    "Builder",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

rng = random.Random(42)  # deterministic corpus


def rand_text(target_bytes: int) -> str:
    """Generate realistic-looking lorem-ipsum-like text of roughly target_bytes."""
    words = [
        "semantic",
        "file",
        "system",
        "index",
        "search",
        "embedding",
        "vector",
        "chunk",
        "token",
        "model",
        "provider",
        "config",
        "daemon",
        "watcher",
        "event",
        "batch",
        "async",
        "thread",
        "queue",
        "buffer",
        "stream",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "with",
        "for",
        "from",
        "into",
        "through",
        "across",
        "between",
        "within",
        "data",
        "file",
        "path",
        "hash",
        "stat",
        "size",
        "time",
        "byte",
    ]
    parts = []
    generated = 0
    while generated < target_bytes:
        sentence_len = rng.randint(8, 20)
        sentence = " ".join(rng.choice(words) for _ in range(sentence_len))
        sentence = sentence.capitalize() + ". "
        parts.append(sentence)
        generated += len(sentence)
    return "".join(parts)


def rand_binary(size_bytes: int) -> bytes:
    """Generate random binary data (simulates images/compiled output)."""
    # Use os.urandom for speed; in large chunks
    chunk = 65536
    result = bytearray()
    while len(result) < size_bytes:
        result.extend(os.urandom(min(chunk, size_bytes - len(result))))
    return bytes(result)


def rand_name() -> str:
    return rng.choice(TOPICS) + "_" + rng.choice(CLASSES).lower()


def fill_file(path: Path, size_bytes: int, binary: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            remaining = size_bytes
            while remaining > 0:
                chunk = min(65536, remaining)
                f.write(os.urandom(chunk))
                remaining -= chunk
    else:
        content = rand_text(size_bytes)
        path.write_text(content, encoding="utf-8")


def fill_template(template: str, size_bytes: int) -> str:
    name = rand_name()
    topic = rng.choice(TOPICS)
    cls = name.split("_")[0].title() + rng.choice(CLASSES)
    rendered = template.format(name=name, topic=topic, cls=cls)
    # Pad to roughly target size if needed
    while len(rendered.encode()) < size_bytes:
        rendered += f"\n# Additional note: {rand_text(512)}"
    return rendered


# ---------------------------------------------------------------------------
# Category generators
# ---------------------------------------------------------------------------


def generate_node_modules(base: Path, budget_bytes: int, progress: list):
    """Simulate a fat node_modules with fake packages."""
    packages = [
        "lodash",
        "react",
        "react-dom",
        "typescript",
        "webpack",
        "babel-core",
        "eslint",
        "prettier",
        "jest",
        "axios",
        "express",
        "next",
        "vite",
        "rollup",
        "esbuild",
        "tailwindcss",
        "postcss",
        "autoprefixer",
        "dotenv",
        "chalk",
        "commander",
        "minimist",
        "yargs",
        "glob",
    ]
    nm = base / "node_modules"
    written = 0
    pkg_idx = 0
    while written < budget_bytes:
        pkg = packages[pkg_idx % len(packages)] + f"_{pkg_idx // len(packages)}"
        pkg_idx += 1
        pkg_dir = nm / pkg
        # Each package: index.js, package.json, README.md, and some nested files
        for fname, size in [
            ("index.js", rng.randint(5_000, 80_000)),
            ("package.json", rng.randint(500, 3_000)),
            ("README.md", rng.randint(1_000, 10_000)),
            ("lib/main.js", rng.randint(10_000, 200_000)),
            ("dist/bundle.js", rng.randint(50_000, 500_000)),
        ]:
            if written >= budget_bytes:
                break
            fpath = pkg_dir / fname
            actual = min(size, budget_bytes - written)
            fill_file(fpath, actual, binary=False)
            written += actual
        progress[0] += written - progress[0] if written > progress[0] else 0
        if written % (50 * 1024 * 1024) < 100_000:
            print(
                f"  [node_modules] {written / 1024**3:.2f} GB / {budget_bytes / 1024**3:.2f} GB",
                end="\r",
            )
    print()


def generate_git_objects(base: Path, budget_bytes: int):
    """Simulate .git/objects with binary-ish pack files."""
    git_dir = base / ".git" / "objects" / "pack"
    git_dir.mkdir(parents=True, exist_ok=True)
    # A few large pack files
    pack_size = budget_bytes // 3
    for i in range(3):
        pack_file = git_dir / f"pack-{i:04d}.pack"
        fill_file(pack_file, pack_size, binary=True)
        print(f"  [.git] pack-{i:04d}.pack written ({pack_size / 1024**2:.0f} MB)")
    # refs
    refs_dir = base / ".git" / "refs" / "heads"
    refs_dir.mkdir(parents=True, exist_ok=True)
    (base / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (refs_dir / "main").write_text("abc123def456" * 3 + "\n")


def generate_dist(base: Path, budget_bytes: int):
    """Simulate build output (minified JS, compiled binaries)."""
    dist = base / "dist"
    written = 0
    i = 0
    while written < budget_bytes:
        size = min(rng.randint(100_000, 2_000_000), budget_bytes - written)
        fpath = dist / f"chunk.{i:04d}.bundle.min.js"
        fill_file(fpath, size, binary=False)
        written += size
        i += 1
    print(f"  [dist] {written / 1024**2:.0f} MB written")


def generate_assets(base: Path, budget_bytes: int):
    """Simulate binary assets (images, fonts)."""
    assets = base / "assets"
    written = 0
    i = 0
    while written < budget_bytes:
        size = min(rng.randint(50_000, 5_000_000), budget_bytes - written)
        ext = rng.choice([".jpg", ".png", ".gif", ".ttf", ".woff2"])
        fpath = assets / f"asset_{i:05d}{ext}"
        fill_file(fpath, size, binary=True)
        written += size
        i += 1
    print(f"  [assets] {written / 1024**2:.0f} MB written ({i} files)")


def generate_src(base: Path, budget_bytes: int):
    """Generate realistic source code files."""
    src = base / "src"
    modules = [
        "core",
        "indexer",
        "search",
        "daemon",
        "config",
        "db",
        "providers",
        "chunkers",
        "extractors",
        "utils",
        "api",
        "cli",
    ]
    templates_by_ext = {
        ".py": PYTHON_TEMPLATES,
        ".js": JS_TEMPLATES,
        ".md": MD_TEMPLATES,
        ".json": JSON_TEMPLATES,
    }
    written = 0
    file_idx = 0
    while written < budget_bytes:
        module = rng.choice(modules)
        ext = rng.choice(list(templates_by_ext.keys()))
        size = rng.randint(2_000, 50_000)
        size = min(size, budget_bytes - written)
        templates = templates_by_ext[ext]
        template = rng.choice(templates)
        content = fill_template(template, size)
        subdir = src / module / ("sub" + str(rng.randint(0, 3)))
        fpath = subdir / f"file_{file_idx:04d}{ext}"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content[: size * 2], encoding="utf-8")  # approx size
        written += size
        file_idx += 1
    print(f"  [src] {written / 1024**2:.0f} MB written ({file_idx} files)")


def generate_docs(base: Path, budget_bytes: int):
    """Generate documentation files (markdown, txt)."""
    docs = base / "docs"
    written = 0
    file_idx = 0
    while written < budget_bytes:
        size = rng.randint(5_000, 50_000)
        size = min(size, budget_bytes - written)
        ext = rng.choice([".md", ".txt", ".rst"])
        topic = rng.choice(TOPICS)
        fpath = docs / f"{topic}_{file_idx:04d}{ext}"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        if ext == ".md":
            template = rng.choice(MD_TEMPLATES)
            content = fill_template(template, size)
        else:
            content = rand_text(size)
        fpath.write_text(content, encoding="utf-8")
        written += size
        file_idx += 1
    print(f"  [docs] {written / 1024**2:.0f} MB written ({file_idx} files)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate a synthetic file corpus for SFS stress testing"
    )
    parser.add_argument(
        "--size-gb", type=float, default=1.0, help="Total corpus size in GB (default: 1.0)"
    )
    parser.add_argument("--output", type=str, required=True, help="Output directory for the corpus")
    parser.add_argument(
        "--overwrite", action="store_true", help="Delete and recreate output dir if it exists"
    )
    args = parser.parse_args()

    output = Path(args.output)
    total_bytes = int(args.size_gb * 1024**3)

    if output.exists() and not args.overwrite:
        print(f"ERROR: {output} already exists. Use --overwrite to replace it.")
        sys.exit(1)
    if output.exists() and args.overwrite:
        import shutil

        print(f"Removing existing corpus at {output}...")
        shutil.rmtree(output)

    output.mkdir(parents=True, exist_ok=True)
    print(f"\n{'=' * 60}")
    print("  YAAOS SFS Stress Test — Corpus Generator")
    print(f"{'=' * 60}")
    print(f"  Target size : {args.size_gb:.1f} GB ({total_bytes:,} bytes)")
    print(f"  Output dir  : {output}")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    progress = [0]

    for category, fraction in CATEGORIES.items():
        budget = int(total_bytes * fraction)
        print(f"[{category}] generating {budget / 1024**2:.0f} MB...")
        if category == "node_modules":
            generate_node_modules(output, budget, progress)
        elif category == "git_objects":
            generate_git_objects(output, budget)
        elif category == "dist":
            generate_dist(output, budget)
        elif category == "assets":
            generate_assets(output, budget)
        elif category == "src":
            generate_src(output, budget)
        elif category == "docs":
            generate_docs(output, budget)

    elapsed = time.time() - t0

    # Count files
    total_files = sum(1 for _ in output.rglob("*") if Path(_).is_file())
    actual_bytes = sum(f.stat().st_size for f in output.rglob("*") if f.is_file())

    print(f"\n{'=' * 60}")
    print("  Corpus Generation Complete")
    print(f"{'=' * 60}")
    print(f"  Total files  : {total_files:,}")
    print(f"  Actual size  : {actual_bytes / 1024**3:.2f} GB")
    print(f"  Time taken   : {elapsed:.1f}s")
    print(f"  Output dir   : {output}")

    # Per-directory sizes
    print("\n  Directory breakdown:")
    for d in sorted(output.iterdir()):
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            count = sum(1 for f in d.rglob("*") if f.is_file())
            print(f"    {d.name:<20} {size / 1024**2:8.0f} MB  ({count:,} files)")
    print(f"{'=' * 60}")
    print(f"\nCorpus ready. Run: python stress_test.py --corpus {output}")


if __name__ == "__main__":
    main()
