#!/usr/bin/env python3
"""
stress_test.py — Benchmark the YAAOS SFS against a synthetic file corpus.

Measures:
  1. Total files in corpus (raw rglob)
  2. Files that pass _should_index() filtering
  3. Initial indexing time + throughput
  4. Database size after indexing
  5. SHA-256 re-check time (current MVP) vs stat-only re-check (v2 approach)
  6. Per-category breakdown

Usage:
    python stress_test.py --corpus C:\\Temp\\sfs_corpus_1gb [--config path/to/config.toml]
    python stress_test.py --corpus C:\\Temp\\sfs_corpus_30gb --limit 500

No changes are made to your real SFS index. Uses a temp DB at C:\\Temp\\sfs_stress_test.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Bootstrap — ensure yaaos_sfs is importable
# ---------------------------------------------------------------------------
_here = Path(__file__).resolve()
# Walk up to find the src/yaaos-sfs/src directory
for _parent in _here.parents:
    _candidate = _parent / "src" / "yaaos-sfs" / "src"
    if _candidate.exists():
        sys.path.insert(0, str(_candidate))
        break
    # Also check if we're already inside the package tree
    _candidate2 = _parent.parent / "src"
    if (_candidate2 / "yaaos_sfs").exists():
        sys.path.insert(0, str(_candidate2))
        break

# Direct relative import fallback: two levels up from tests/stress/ → src/
_src = _here.parent.parent.parent / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


try:
    from yaaos_sfs.config import Config
    from yaaos_sfs.db import Database
    from yaaos_sfs.indexer import extract_text, chunk_text
    from yaaos_sfs.filter import FileFilter
    from yaaos_sfs.providers.local import LocalEmbeddingProvider

    SFS_AVAILABLE = True
except ImportError as e:
    print(f"WARNING: Could not import yaaos_sfs: {e}")
    print("Running in SCAN-ONLY mode (no embedding/indexing).")
    SFS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fmt_size(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def fmt_time(t: float) -> str:
    if t >= 60:
        m, s = divmod(int(t), 60)
        return f"{m}m {s}s"
    return f"{t:.2f}s"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def stat_file(path: Path) -> tuple[int, int]:
    """Return (mtime_ns, size_bytes) — all we need for stat-first detection."""
    s = path.stat()
    return (int(s.st_mtime_ns), s.st_size)


class Result(NamedTuple):
    label: str
    value: str
    note: str = ""


# ---------------------------------------------------------------------------
# Phase 1: Corpus scan
# ---------------------------------------------------------------------------


def scan_corpus(corpus: Path, config: "Config" | None) -> dict:
    print("\n[Phase 1] Scanning corpus directory (V2 Logic)...")
    t0 = time.perf_counter()

    total_files = 0
    total_size = 0
    indexable_files = []
    skipped_by_ext = 0
    skipped_hidden = 0
    per_dir: dict[str, dict] = {}

    if config and SFS_AVAILABLE:
        file_filter = FileFilter(corpus, config.supported_extensions, config.max_file_size_mb)
    else:
        file_filter = None

    for root, dirs, filenames in os.walk(corpus):
        if file_filter:
            dirs[:] = [d for d in dirs if file_filter.is_dir_allowed(Path(os.path.join(root, d)))]

        for f in filenames:
            path = Path(root) / f
            total_files += 1
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total_size += size

            # Track per top-level dir
            try:
                rel = path.relative_to(corpus)
                top = rel.parts[0] if len(rel.parts) > 1 else "."
            except ValueError:
                top = "."
            if top not in per_dir:
                per_dir[top] = {"files": 0, "size": 0, "indexable": 0}
            per_dir[top]["files"] += 1
            per_dir[top]["size"] += size

            if file_filter:
                if file_filter.should_index(path, file_size=size):
                    indexable_files.append(path)
                    per_dir[top]["indexable"] += 1
            else:
                indexable_files.append(path)
                per_dir[top]["indexable"] += 1

    elapsed = time.perf_counter() - t0

    print(f"  rglob scan complete: {total_files:,} files in {fmt_time(elapsed)}")
    print(f"  Total size: {fmt_size(total_size)}")
    print(f"  Indexable (pass _should_index): {len(indexable_files):,}")

    return {
        "total_files": total_files,
        "total_size": total_size,
        "indexable_files": indexable_files,
        "indexable_count": len(indexable_files),
        "skipped_by_ext": skipped_by_ext,
        "skipped_hidden": skipped_hidden,
        "scan_time": elapsed,
        "per_dir": per_dir,
    }


# ---------------------------------------------------------------------------
# Phase 2: Indexing benchmark
# ---------------------------------------------------------------------------


def run_indexing(
    indexable_files: list[Path],
    db: "Database",
    provider: "LocalEmbeddingProvider",
    config: "Config",
    limit: int | None = None,
) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    indexed = 0
    failed = 0
    skipped = 0
    total_chunks = 0
    t0 = time.perf_counter()

    files_to_index = []
    for path in indexable_files:
        if db.file_needs_indexing(path):
            files_to_index.append(path)
        else:
            skipped += 1

    if limit:
        files_to_index = files_to_index[:limit]

    total = len(files_to_index)
    print(f"\n[Phase 2] Indexing {total:,} files (V2 Batched Logic)...")

    def process_file(path: Path):
        try:
            text = extract_text(path)
            if not text or not text.strip():
                return "failed", None, path
            chunks = chunk_text(text, config.chunk_size, config.chunk_overlap)
            if not chunks:
                return "failed", None, path
            return "ok", chunks, path
        except Exception:
            return "failed", None, path

    current_batch_files = []
    current_batch_chunks = []

    def flush_batch():
        nonlocal indexed, total_chunks
        if not current_batch_chunks:
            return
        try:
            embs = provider.embed(current_batch_chunks)
            offset = 0
            for path, ccks in current_batch_files:
                n = len(ccks)
                db.upsert_file(path, ccks, embs[offset : offset + n])
                offset += n
                indexed += 1
                total_chunks += n
        except Exception as e:
            print(f"Batch embed failed: {e}")
        finally:
            current_batch_files.clear()
            current_batch_chunks.clear()

    workers = min(32, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_file, p) for p in files_to_index]

        with tqdm(total=total, desc="Indexing") as pbar:
            for fut in as_completed(futures):
                pbar.update(1)
                try:
                    status, chunks, path = fut.result()
                except Exception:
                    status, chunks, path = "failed", None, None

                if status == "failed":
                    failed += 1
                elif status == "ok" and chunks:
                    current_batch_files.append((path, chunks))
                    current_batch_chunks.extend(chunks)
                    if len(current_batch_chunks) >= config.batch_size:
                        flush_batch()

            flush_batch()

    elapsed = time.perf_counter() - t0
    print(f"\n  Indexing done in {fmt_time(elapsed)}")
    print(f"  Indexed: {indexed:,} | Failed: {failed} | Skipped (cached): {skipped}")
    print(f"  Total chunks: {total_chunks:,}")

    return {
        "indexed": indexed,
        "failed": failed,
        "skipped": skipped,
        "total_chunks": total_chunks,
        "elapsed": elapsed,
        "files_per_sec": indexed / elapsed if elapsed > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Phase 3: Change detection benchmark
# ---------------------------------------------------------------------------


def benchmark_change_detection(indexed_files: list[Path], db: "Database") -> dict:
    """Compare SHA-256 vs stat-only re-check performance."""
    files = [f for f in indexed_files if f.exists()][:500]  # Sample up to 500 files
    if not files:
        return {"sha256_time": 0, "stat_time": 0, "files": 0}

    n = len(files)
    print(f"\n[Phase 3] Change detection benchmark ({n} files)...")

    # SHA-256 (current MVP approach)
    t0 = time.perf_counter()
    for path in files:
        sha256_file(path)
    sha256_elapsed = time.perf_counter() - t0

    # Stat-only (v2 approach — just mtime_ns + size)
    t0 = time.perf_counter()
    for path in files:
        stat_file(path)
    stat_elapsed = time.perf_counter() - t0

    sha256_rate = n / sha256_elapsed if sha256_elapsed > 0 else 0
    stat_rate = n / stat_elapsed if stat_elapsed > 0 else 0
    speedup = sha256_elapsed / stat_elapsed if stat_elapsed > 0 else 0

    print(f"  SHA-256   : {fmt_time(sha256_elapsed)} ({sha256_rate:.0f} files/s)")
    print(f"  Stat-only : {fmt_time(stat_elapsed)} ({stat_rate:.0f} files/s)")
    print(f"  Speedup   : {speedup:.0f}x faster with stat-only")

    return {
        "files_sampled": n,
        "sha256_time": sha256_elapsed,
        "stat_time": stat_elapsed,
        "sha256_rate": sha256_rate,
        "stat_rate": stat_rate,
        "speedup": speedup,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_report(scan: dict, index: dict | None, change: dict | None, db_path: Path, corpus: Path):
    db_size = db_path.stat().st_size if db_path.exists() else 0

    print(f"\n{'=' * 68}")
    print("  YAAOS SFS Stress Test Results")
    print(f"{'=' * 68}")
    print(f"  Corpus: {corpus}")
    print(f"{'=' * 68}")

    rows = [
        ("Corpus total files", f"{scan['total_files']:,}", ""),
        ("Corpus total size", fmt_size(scan["total_size"]), ""),
        ("Corpus scan time", fmt_time(scan["scan_time"]), "rglob only"),
        ("Files to index (MVP)", f"{scan['indexable_count']:,}", "passed _should_index()"),
        ("Skipped (ext mismatch)", f"{scan['skipped_by_ext']:,}", ""),
    ]

    if index:
        filtering_pct = (1 - scan["indexable_count"] / max(scan["total_files"], 1)) * 100
        rows += [
            ("Files actually indexed", f"{index['indexed']:,}", ""),
            ("Chunks stored", f"{index['total_chunks']:,}", ""),
            ("Index time", fmt_time(index["elapsed"]), "wall clock"),
            ("Index throughput", f"{index['files_per_sec']:.1f} files/s", ""),
            ("DB size", fmt_size(db_size), "sqlite-vec"),
            ("Filtering would remove", f"{filtering_pct:.0f}% of files", "v2 improvement"),
        ]

    if change:
        rows += [
            (
                "SHA-256 re-check (500)",
                fmt_time(change["sha256_time"]),
                f"{change['sha256_rate']:.0f} files/s (current MVP)",
            ),
            (
                "Stat-only re-check (500)",
                fmt_time(change["stat_time"]),
                f"{change['stat_rate']:.0f} files/s (v2 approach)",
            ),
            ("Change detection speedup", f"{change['speedup']:.0f}x faster", "with stat-first"),
        ]

    label_w = max(len(r[0]) for r in rows) + 2
    for label, value, note in rows:
        note_str = f"  ← {note}" if note else ""
        print(f"  {label:<{label_w}} {value:<20}{note_str}")

    print("\n  Per-directory breakdown:")
    print(f"  {'Dir':<22} {'Files':>8}  {'Size':>10}  {'Indexable':>10}")
    print(f"  {'-' * 22} {'-' * 8}  {'-' * 10}  {'-' * 10}")
    for d, stats in sorted(scan["per_dir"].items()):
        indexable_note = f"{stats['indexable']:,}" if stats["indexable"] else "0 (skipped)"
        print(
            f"  {d:<22} {stats['files']:>8,}  {fmt_size(stats['size']):>10}  {indexable_note:>10}"
        )

    print(f"{'=' * 68}")

    # Save JSON results
    results = {
        "corpus": str(corpus),
        "scan": {k: v for k, v in scan.items() if k != "indexable_files" and k != "per_dir"},
        "per_dir": scan["per_dir"],
        "indexing": index,
        "change_detection": change,
        "db_size_bytes": db_size,
    }
    out = Path(os.environ.get("TEMP", "/tmp")) / "sfs_stress_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\n  Full results saved to: {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="YAAOS SFS stress test — benchmark indexing against a synthetic corpus"
    )
    parser.add_argument(
        "--corpus",
        type=str,
        required=True,
        help="Path to the corpus directory (from generate_corpus.py)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to SFS config.toml (optional; uses defaults if not specified)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit indexing to N files (for quick sanity runs)"
    )
    parser.add_argument(
        "--db", type=str, default=None, help="Temp DB path (default: %%TEMP%%\\sfs_stress_test.db)"
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan + count files, skip indexing (fastest check)",
    )
    args = parser.parse_args()

    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"ERROR: corpus directory not found: {corpus}")
        sys.exit(1)

    temp_dir = Path(os.environ.get("TEMP", "/tmp"))
    db_path = Path(args.db) if args.db else temp_dir / "sfs_stress_test.db"
    # Always use a fresh DB for the stress test
    if db_path.exists():
        db_path.unlink()
        print(f"Removed stale stress DB: {db_path}")

    print(f"\n{'=' * 68}")
    print("  YAAOS SFS Stress Test")
    print(f"{'=' * 68}")
    print(f"  Corpus   : {corpus}")
    print(f"  DB       : {db_path}")
    print(f"  SFS      : {'available' if SFS_AVAILABLE else 'SCAN-ONLY (import failed)'}")
    if args.limit:
        print(f"  Limit    : {args.limit} files")
    print(f"{'=' * 68}")

    # Load config / supported extensions
    if SFS_AVAILABLE:
        config_path = Path(args.config) if args.config else None
        config = Config.load(config_path)
        # Override watch_dir to our corpus and db_path to temp
        config.watch_dir = corpus
        config.db_path = db_path
    else:
        # Minimal fallback for scan-only mode
        pass

    # Phase 1: Scan
    scan = scan_corpus(corpus, config if SFS_AVAILABLE else None)

    if args.scan_only or not SFS_AVAILABLE:
        print_report(scan, None, None, db_path, corpus)
        return

    # Phase 2: Index
    print("\nLoading embedding model (all-MiniLM-L6-v2)...")
    t_model = time.perf_counter()
    provider = LocalEmbeddingProvider(config.embedding_model)
    print(f"  Model loaded in {fmt_time(time.perf_counter() - t_model)}")

    db = Database(db_path, embedding_dims=provider.dims)
    index_results = run_indexing(scan["indexable_files"], db, provider, config, limit=args.limit)

    # Phase 3: Change detection benchmark
    change_results = benchmark_change_detection(scan["indexable_files"], db)

    db.close()

    # Report
    print_report(scan, index_results, change_results, db_path, corpus)


if __name__ == "__main__":
    main()
