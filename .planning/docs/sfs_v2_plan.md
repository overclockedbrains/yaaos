# SFS v2: Production-Ready Semantic File System

**Status:** Approved
**Last Updated:** 2026-03-14
**Replaces:** Phase 1 MVP (completed)

---

## Context

**Why this change**: The SFS MVP works for small demo folders, but can't handle real-world use. A 22GB developer folder with node_modules, libs, build artifacts, and images would crash the current system — it tries to index everything, uses SHA-256 on every file, and has no filtering.

**What we're building**: An SFS that handles real-world folders at scale — for developers AND general users (researchers, writers, academics). YAAOS is for everyone.

**Hardware constraints**: Ryzen 7 4800H (8 cores), 16GB RAM, GTX 1650 Ti (4GB VRAM). All model/architecture choices must work within this.

**Research completed**: 14 research agents analyzed VSCode/ripgrep indexing, Sourcegraph/Zoekt trigram search, Facebook Watchman architecture, Cursor/Copilot semantic indexing, tree-sitter chunking, CLIP/Whisper multimodal, embedding model benchmarks, file watching at scale, and document extraction libraries.

---

## Research Findings (Key Takeaways)

### How Production Tools Handle Scale

| Tool | Strategy | Key Insight |
|------|----------|-------------|
| **VSCode** | ripgrep for search, NO full index of file contents. Watches files via OS APIs. Skips .gitignore'd files. | You don't need to index everything — smart filtering removes 95% of files |
| **Sourcegraph/Zoekt** | Trigram index, 3-5x source size. Shards by repo. | Index-to-source ratio matters — our vector index will be ~1-2% of source size (much smaller than trigram) |
| **Facebook Watchman** | In-memory file tree, cookie-based synchronization, ~1KB/watched file. Handles millions of files. | Stat-based change detection is the industry standard, NOT content hashing |
| **Cursor/Copilot** | AST-aware chunking via tree-sitter, embedding locally, hybrid vector+keyword search | Tree-sitter chunking gives 15-25% better retrieval than fixed-size |

### Embedding Models for This Hardware

| Model | Dims | Size | Speed (GTX 1650 Ti) | Quality | Verdict |
|-------|------|------|---------------------|---------|---------|
| all-MiniLM-L6-v2 | 384 | ~80MB | ~500 chunks/sec | Good | **Keep as default** — fast, low VRAM |
| nomic-embed-text-v1.5 | 768 | ~270MB | ~200 chunks/sec | Better (code+text aware) | **Future upgrade** — still fits in 4GB VRAM |
| voyage-code-3 | 1024 | Cloud only | API-limited | Best for code | **Cloud provider option** — for testing |
| text-embedding-3-small | 1536 | Cloud only | API-limited | Great general | **Cloud provider option** — already supported |

### What a 22GB Dev Folder Actually Contains

```
~15 GB  node_modules / .venv / vendor (dependencies) → SKIP
~3 GB   .git objects                                  → SKIP
~2 GB   build artifacts (dist, out, target, bin)      → SKIP
~1 GB   images, fonts, binary assets                  → METADATA ONLY
~500 MB actual source code                            → INDEX (Tier 1)
~200 MB docs (PDF, DOCX, MD)                          → INDEX (Tier 2)
~300 MB config, data files                            → INDEX (Tier 1)
```

**Result**: We only need to index ~1GB of actual content out of 22GB. With proper filtering, initial indexing takes 3-5 minutes, not hours.

---

## Architecture

### Three-Tier File Processing

```
File arrives
    │
    ├─ Filter Pipeline (should we index this?)
    │   ├─ Directory skip list (.git, node_modules, __pycache__...)
    │   ├─ .gitignore + .sfsignore matching (pathspec library)
    │   ├─ Generated file patterns (*.min.js, *.lock, *.map)
    │   ├─ Size limit (>100MB configurable, skip)
    │   └─ Binary detection for unknown extensions (first 8KB NUL check)
    │
    ├─ Tier 1: Text-native (direct read)
    │   Code: .py .js .ts .rs .go .c .java .rb .php
    │   Markup: .md .txt .rst .html .xml
    │   Config: .json .yaml .toml .ini .env
    │   Data: .csv .tsv
    │
    ├─ Tier 2: Rich documents (extract text → embed)
    │   .pdf → PyMuPDF (already in MVP)
    │   .docx → python-docx (paragraphs, headings, tables)
    │   .pptx → python-pptx (slide text, speaker notes)
    │   .xlsx → openpyxl (sheet names, headers, values)
    │   .epub → ebooklib (chapters)
    │   .rtf → striprtf
    │
    └─ Tier 3: Media metadata (extract tags → embed as text)
        Images: .png .jpg .gif .svg → Pillow EXIF (camera, date, GPS, description)
        Audio: .mp3 .wav .m4a .flac → mutagen (title, artist, album, genre)
        Video: .mp4 .mkv .avi → mutagen (title, duration, codec)
```

### Provider Architecture (Easily Swappable)

Cloud models are easily swappable via config — one line to switch providers for testing with high-end models.

Current MVP already has `EmbeddingProvider` ABC + `LocalEmbeddingProvider` + `OpenAIEmbeddingProvider`. We enhance this:

```python
# config.toml — swap provider with one line
[embedding]
provider = "local"           # or "openai", "anthropic", "voyage", "ollama"
model = "all-MiniLM-L6-v2"  # or "text-embedding-3-small", "voyage-code-3"

[providers.openai]
api_key_env = "OPENAI_API_KEY"

[providers.voyage]
api_key_env = "VOYAGE_API_KEY"

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"

[providers.ollama]
base_url = "http://localhost:11434"
```

New providers to add:
- `VoyageEmbeddingProvider` — best code embeddings (voyage-code-3)
- `OllamaEmbeddingProvider` — local models via Ollama API (bridges to Model Bus in Phase 2)
- Provider interface stays the same: `embed(texts) → vectors`, `embed_query(query) → vector`, `dims → int`

### Smart Chunking (Multi-Strategy)

```
File type detected
    │
    ├─ Code (.py, .js, .ts, .rs, etc.)
    │   └─ tree-sitter → extract functions/classes/methods
    │      Each symbol = 1 chunk, prefixed with file path + language
    │      Large symbols (>1024 tokens): sub-chunk with signature prefix
    │      Small symbols (<64 tokens): merge with neighbors
    │
    ├─ Documents (.md, .docx, .pdf, .txt)
    │   └─ Section-aware: split on headings (##), page breaks, slides
    │      No structure? Fall back to 512-token fixed chunks with 50 overlap
    │
    ├─ Structured (.json, .yaml, .csv, .xlsx)
    │   └─ Extract key-value pairs, headers, sheet names as text
    │
    └─ Media (images, audio, video)
        └─ Single metadata chunk per file (filename + extracted tags)
```

**Fallback**: If tree-sitter parsing fails for any reason, fall back to current fixed-size chunking. No crashes.

### Change Detection (Stat-First)

Replace current SHA-256-every-file with stat-first approach:

```
stat() → compare (mtime, size) stored in DB
  ├─ Both match → SKIP (zero I/O, ~1μs/file)
  ├─ Size differs → CHANGED (re-index, no hash needed)
  └─ mtime differs, size same → xxHash128 to confirm
      ├─ Hash matches → touch-only, skip
      └─ Hash differs → re-index
```

xxHash: 30-50 GB/s vs SHA-256 at 0.5 GB/s = **60-100x faster**.

### Daemon Improvements

- **Batch embedding**: Collect chunks, embed 32 at a time via `model.encode(batch)` (~3x faster)
- **Debouncing**: Events within 200ms window are batched together
- **Parallel I/O**: `ThreadPoolExecutor(max_workers=4)` for file reading + text extraction
- **Progress bar**: `tqdm` for initial scan

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/yaaos-sfs/src/yaaos_sfs/config.py` | Add ignore patterns list, max_file_size, batch_size, debounce_ms, provider registry |
| `src/yaaos-sfs/src/yaaos_sfs/indexer.py` | Refactor: move extraction to extractors/, move chunking to chunkers/, keep as thin dispatch |
| `src/yaaos-sfs/src/yaaos_sfs/daemon.py` | Add debouncing (200ms), batch embedding (32 chunks), ThreadPoolExecutor(4), tqdm progress |
| `src/yaaos-sfs/src/yaaos_sfs/db.py` | Stat-based change detection (mtime_ns+size), xxHash128 instead of SHA-256, file_type column |
| `src/yaaos-sfs/src/yaaos_sfs/search.py` | Add file path matching signal to RRF, recency boost, file_type in SearchResult |
| `src/yaaos-sfs/src/yaaos_sfs/cli.py` | Richer --status (per-type breakdown), --type accepts comma-separated (pdf,docx) |
| `src/yaaos-sfs/pyproject.toml` | Add deps: pathspec, xxhash, tqdm, python-docx, python-pptx, openpyxl, Pillow, mutagen, ebooklib, striprtf |
| `src/yaaos-sfs/src/yaaos_sfs/providers/__init__.py` | Add provider registry function: `get_provider(config) → EmbeddingProvider` |

## New Files

| File | Purpose |
|------|---------|
| `src/yaaos-sfs/src/yaaos_sfs/filter.py` | 4-layer filter pipeline: dir skip → gitignore → generated → binary/size |
| `src/yaaos-sfs/src/yaaos_sfs/extractors/__init__.py` | Registry: `get_extractor(path) → Callable` maps extensions to extractors |
| `src/yaaos-sfs/src/yaaos_sfs/extractors/text.py` | Refactored from indexer.py — plain text + code file reading |
| `src/yaaos-sfs/src/yaaos_sfs/extractors/documents.py` | PDF (existing), DOCX, PPTX, XLSX, EPUB, RTF extraction |
| `src/yaaos-sfs/src/yaaos_sfs/extractors/media.py` | EXIF (Pillow), audio tags (mutagen), video metadata |
| `src/yaaos-sfs/src/yaaos_sfs/chunkers/__init__.py` | Registry: `get_chunker(path, text) → list[str]` dispatches by file type |
| `src/yaaos-sfs/src/yaaos_sfs/chunkers/code.py` | tree-sitter AST chunking with fallback to fixed-size |
| `src/yaaos-sfs/src/yaaos_sfs/chunkers/document.py` | Heading/section-aware chunking for prose |
| `src/yaaos-sfs/src/yaaos_sfs/chunkers/structured.py` | JSON/YAML/CSV key-value extraction |
| `src/yaaos-sfs/src/yaaos_sfs/providers/voyage_provider.py` | Voyage AI embeddings (voyage-code-3) |
| `src/yaaos-sfs/src/yaaos_sfs/providers/ollama_provider.py` | Ollama local embeddings (bridges to Model Bus later) |
| `src/yaaos-sfs/.sfsignore.default` | Default ignore patterns shipped with SFS |

---

## Implementation Order

### Phase A: Core Infrastructure (makes everything else possible)

**A1. File filtering** — `filter.py`
- Hardcoded dir skip list (22 patterns: .git, node_modules, __pycache__, .venv, dist, build, etc.)
- `.gitignore` + `.sfsignore` parsing via `pathspec` library
- Generated file skip (*.min.js, *.map, *.lock)
- Binary detection for unknown extensions (first 8KB NUL byte check)
- Max file size config (default 100MB)
- Integrate into `daemon.py` `_should_index()` and `_initial_scan()`

**A2. Stat-based change detection** — `db.py`
- Add `mtime_ns`, `size_bytes` columns to `files` table
- Replace `_hash_file()` SHA-256 with xxHash128 (`xxhash.xxh128()`)
- New `file_needs_indexing()`: stat-first, hash only if mtime changed + size same
- Migration: auto-rebuild stat cache on first run with new schema

**A3. Batch embedding + debouncing** — `daemon.py`
- Debounce: collect events for 200ms before processing
- Batch: accumulate chunks, embed 32 at a time
- ThreadPoolExecutor(4) for parallel file I/O (read + extract)
- tqdm progress bar for initial scan
- Graceful error handling: one bad file doesn't stop the batch

### Phase B: Multi-Format Support (YAAOS is for everyone)

**B1. Extractor registry** — `extractors/__init__.py`
- Extension → extractor function mapping
- Each extractor: `(Path) → str | None`
- Graceful degradation: if library missing, log warning and skip

**B2. Document extractors** — `extractors/documents.py`
- DOCX: python-docx → paragraphs + headings + tables
- PPTX: python-pptx → slide text + speaker notes
- XLSX: openpyxl → sheet names + headers + cell values (first 1000 rows)
- EPUB: ebooklib → chapters as text
- RTF: striprtf → plain text
- All deps optional: `[project.optional-dependencies] docs = [...]`

**B3. Media metadata extractors** — `extractors/media.py`
- Images: Pillow → EXIF (camera model, date taken, GPS coords, description)
- Audio: mutagen → ID3 tags (title, artist, album, genre, year)
- Video: mutagen → metadata (title, duration, codec info)
- Output: formatted text block → goes through same embed pipeline
- All deps optional: `[project.optional-dependencies] media = [...]`

### Phase C: Smart Chunking (quality improvement)

**C1. Chunker registry** — `chunkers/__init__.py`
- Extension → chunker function mapping
- Each chunker: `(text, config) → list[str]`
- Default fallback: current fixed-size chunking from `indexer.py`

**C2. Code chunker** — `chunkers/code.py`
- tree-sitter parsing for Python, JS/TS, Rust, Go, C/C++, Java
- Extract: functions, classes, methods as individual chunks
- Prefix each chunk: `# File: path\n# Language: python\n# Symbol: def foo()\n`
- Large symbols (>1024 tokens): sub-chunk with signature prefix
- Small symbols (<64 tokens): merge with neighbors
- **Fallback**: if tree-sitter fails → fixed-size chunking (no crash)
- tree-sitter as optional dep: `[project.optional-dependencies] code = [...]`

**C3. Document chunker** — `chunkers/document.py`
- Markdown: split on `##` headings, keep heading as chunk prefix
- General prose: split on paragraph boundaries, merge small paragraphs
- If no structure detected: fixed-size 512 tokens with 50 overlap

### Phase D: Search & UX

**D1. Search enhancements** — `search.py`
- Add file path as third RRF signal (fuzzy match query against file paths)
- Recency boost: multiply score by `1 + 0.1 * recency_factor` (files modified in last 7 days get boost)
- Add `file_type` and `modified_at` to `SearchResult`
- Support comma-separated type filter: `--type pdf,docx,md`

**D2. Provider additions** — `providers/`
- `voyage_provider.py`: Voyage AI API (voyage-code-3, 1024d)
- `ollama_provider.py`: Local Ollama API (any embedding model)
- Provider factory in `providers/__init__.py`: `get_provider(config) → EmbeddingProvider`

**D3. CLI & status improvements** — `cli.py`
- `--status` shows per-type breakdown: "45 .py, 23 .md, 12 .pdf, 8 .docx, 156 images (metadata)"
- `--type` accepts comma-separated: `yaaos-find "report" --type pdf,docx`
- Show file type icon in results (from extension)

**D4. Tests**
- Unit tests for filter pipeline (skip node_modules, respect .gitignore)
- Unit tests for each extractor (DOCX, PPTX, XLSX, media)
- Unit tests for each chunker (tree-sitter, document, structured)
- Integration test: index a mixed folder, search, verify results

---

## Performance Projections (22GB Mixed Dev Folder)

| Metric | Current MVP | After SFS v2 |
|--------|------------|--------------|
| Files to scan | ALL (~500K+) | ~25K after filtering |
| Files to index | ALL (~500K+) | ~20K text + ~2K docs + ~5K media metadata |
| Initial index time | Would crash/hours | **3-5 minutes** |
| Incremental check | SHA-256 every file (~2 min) | Stat check: **<1 second** |
| Single file re-index | ~200ms | ~200ms (unchanged) |
| DB size | N/A | **~150-250 MB** |
| RAM usage | ~350 MB (model) | ~350 MB (unchanged) |
| VRAM usage | ~300 MB (all-MiniLM) | ~300 MB (unchanged) |
| Search latency | <200ms | <200ms (unchanged) |
| File types supported | 10 (text only) | **40+ (text + docs + media metadata)** |
| Burst handling (git checkout) | Each event separately | **Debounce → single batch** |

## Rust Migration Path

The architecture is designed for incremental Rust migration (future, not this implementation):
- `filter.py` → Rust CLI (like ripgrep) — biggest perf win
- `db.py` → Rust with rusqlite + sqlite-vec — faster I/O
- `daemon.py` → Rust with notify crate — native file watching
- Provider interface stays Python (model loading is Python-ecosystem)
- Each component can be swapped independently via subprocess or FFI

## Future: AI-Powered Media (Not in this implementation)

| Feature | Model | VRAM Need | Fits GTX 1650 Ti? | Timeline |
|---------|-------|-----------|-------------------|----------|
| Image similarity | CLIP ViT-B/32 | ~1.5 GB | YES | Near-future |
| Audio transcription | Whisper-tiny | ~150 MB (CPU) | YES (CPU) | Near-future |
| OCR (scanned PDFs) | Tesseract | ~30 MB (CPU) | YES | Can add in Phase B |
| Image captioning | BLIP-2 / LLaVA | 5-8 GB | NO (cloud only) | Future |
| Video understanding | Keyframes+BLIP+Whisper | Multiple | NO | Distant |

The provider/plugin architecture means these can be added incrementally without changing core code.

---

## Verification

1. **Filtering**: Point SFS at 22GB dev folder → verify it skips node_modules/.git/build but indexes source + docs + media metadata
2. **Document extraction**: Add .docx and .xlsx to watch dir → verify `yaaos-find "content from the document"` returns them
3. **Media metadata**: Add photos with EXIF → `yaaos-find "photo from 2024"` finds by date
4. **Performance**: Initial index of 22GB folder completes in <5 minutes with tqdm progress bar
5. **Stat detection**: `touch file.py` (no content change) → no re-index. Edit file.py → re-indexes
6. **Debouncing**: `git checkout other-branch` (1000+ file changes) → single batch process
7. **Provider swap**: Change `provider = "openai"` in config → restart → search works with cloud embeddings
8. **Type filter**: `yaaos-find "quarterly report" --type pdf,docx` → only PDF/DOCX results
9. **Status**: `yaaos-find --status` → shows per-type breakdown
10. **Robustness**: Corrupt PDF in watch dir → logs error, continues indexing other files
