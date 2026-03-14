# SFS Stress Test

Benchmarks the YAAOS Semantic File System against a realistic synthetic corpus
to measure indexing performance, DB size, and change-detection overhead.

## What It Measures

| Metric | Why it matters |
|--------|---------------|
| Total files in corpus | Shows the scale the daemon has to walk |
| Files that pass filtering | MVP indexes ~everything; v2 targets ~5% |
| Initial indexing time | Baseline to beat with v2's batching + filtering |
| DB size after indexing | Should land in the 150–250 MB range |
| SHA-256 re-check time | Current MVP runs SHA-256 on every stat change |
| Stat-only re-check time | v2 approach (mtime_ns + size) — expected 60–100× faster |

---

## Prerequisites

The SFS package must be installed (or the virtualenv active):

```powershell
cd c:\projects\yaaos\src\yaaos-sfs
uv sync          # or: pip install -e .
```

---

## Quick Sanity Run (1 GB)

Generates a 1 GB corpus and runs the full benchmark. Takes ~5–10 minutes total.

```powershell
# 1. Generate corpus
python tests\stress\generate_corpus.py --size-gb 1 --output C:\Temp\sfs_corpus_1gb

# 2. Run benchmark
python tests\stress\stress_test.py --corpus C:\Temp\sfs_corpus_1gb
```

---

## Full Stress Test (30 GB)

Generates the production-scale corpus and runs the benchmark. Corpus generation
takes ~10–15 minutes; indexing takes as long as the MVP takes (this is what we're measuring).

```powershell
# 1. Generate corpus (one-time, ~10 min)
python tests\stress\generate_corpus.py --size-gb 30 --output C:\Temp\sfs_corpus_30gb

# 2. Run benchmark (re-run as many times as needed, it reuses the corpus)
python tests\stress\stress_test.py --corpus C:\Temp\sfs_corpus_30gb
```

---

## Options

### `generate_corpus.py`

| Flag | Default | Description |
|------|---------|-------------|
| `--size-gb` | `1.0` | Total corpus size in GB |
| `--output` | *(required)* | Where to write the corpus |
| `--overwrite` | off | Delete existing dir and regenerate |

### `stress_test.py`

| Flag | Default | Description |
|------|---------|-------------|
| `--corpus` | *(required)* | Path to corpus (from generate_corpus.py) |
| `--config` | `~/.config/yaaos/config.toml` | SFS config file |
| `--limit` | off | Stop indexing after N files (for quick tests) |
| `--db` | `%TEMP%\sfs_stress_test.db` | Temp DB path (never touches your real DB) |
| `--scan-only` | off | Only count/categorise files, skip indexing |

**Examples:**

```powershell
# Only scan files, no indexing (fastest)
python tests\stress\stress_test.py --corpus C:\Temp\sfs_corpus_30gb --scan-only

# Index only 200 files as a quick sanity check
python tests\stress\stress_test.py --corpus C:\Temp\sfs_corpus_30gb --limit 200

# Use a custom config (e.g., OpenAI provider)
python tests\stress\stress_test.py --corpus C:\Temp\sfs_corpus_1gb --config myconfig.toml
```

---

## Corpus Structure

The generated corpus mirrors a real developer machine:

```
corpus/
  src/          ~20% of total  ← INDEXED    (Python, JS, MD, JSON)
  docs/         ~10% of total  ← INDEXED    (Markdown, TXT)
  node_modules/ ~50% of total  ← SKIPPED*  (fake npm packages)
  .git/         ~10% of total  ← SKIPPED*  (binary pack files)
  dist/         ~7%  of total  ← SKIPPED*  (minified JS bundles)
  assets/       ~3%  of total  ← SKIPPED*  (random binary blobs)
```

\* MVP does NOT skip these — it tries to index everything. SFS v2 (Phase A1)
adds a filter pipeline that eliminates ~95% of files before any I/O.

---

## Results

Results are printed to the console as a table and also saved to:

```
%TEMP%\sfs_stress_results.json
```

## Baseline Results (1 GB Corpus, MVP)

We ran the stress test against the **1 GB synthetic corpus** on the current SFS MVP to establish a baseline before v2. The results clearly highlight the bottlenecks:

| Metric | MVP (measured on 1GB) | SFS v2 (goal) |
|--------|--------------|---------------|
| **Files scanned** | 18,346 | (Same) |
| **Files indexable** | 17,029 (MVP tries to index `node_modules`!) | ~5% of scanned files |
| **Initial index speed** | **~0.07 files/s** (≈65 hours extrapolated) | 3–5 minutes entire corpus |
| **DB size** | **~122 MB for only 50 files** (unsustainable) | < 250 MB entire corpus |
| **Change Detection** | SHA-256 | Stat-first |
| **Re-check (50 files)** | 0.015s (SHA-256) vs 0.00007s (Stat) | **~200x faster** with stat |

These baseline numbers validate the need for SFS v2 Phase A:
1. **Filtering:** We must implement early filtering (A1) because the MVP attempts to index 17,029 out of 18,346 files, completely missing noise directories.
2. **Change Detection:** Stat-only change detection (A2) is over 200x faster than reading every file for a SHA-256 hash.
3. **Indexing Throughput:** Processing files sequentially via the daemon is far too slow (0.07 files/s). We urgently need batching and thread pools (A3).
