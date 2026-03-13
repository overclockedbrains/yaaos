# YAAOS MVP Scope: Semantic File System

**Draft Status:** First Draft
**Project:** YAAOS (Your Agentic AI Operating System)

---

## 1. MVP Goal

Build a **working Semantic File System** that can be demoed on any Linux machine (or WSL).

**One-liner:** Drop files into a folder, search them by meaning.

**Demo scenario:**
1. User has a `~/semantic/` directory with mixed files (notes, code, PDFs)
2. User saves a new file → it's automatically embedded in the background
3. User runs `yaaos-find "the bug fix for the routing issue"` → gets ranked results with snippets
4. User runs `yaaos-find "python scripts that parse JSON"` → finds code files by what they do, not their names

---

## 2. What's IN the MVP

### 2.1 Semantic File System Daemon (`yaaos-sfs`)

A background daemon that:
- **Watches** a configured directory (default: `~/semantic/`) via inotify
- **Extracts text** from supported file types on create/modify
- **Chunks** large files (512 tokens, 50-token overlap)
- **Generates embeddings** using all-MiniLM-L6-v2 (local, CPU)
- **Stores** metadata + vectors in a SQLite database with sqlite-vec

Supported file types for MVP:
| Type | Extension | Extraction Method |
|------|-----------|-------------------|
| Plain text | .txt | Direct read |
| Markdown | .md | Direct read |
| Python | .py | Direct read |
| JavaScript/TypeScript | .js, .ts | Direct read |
| JSON | .json | Direct read |
| YAML/TOML | .yaml, .toml | Direct read |
| Shell scripts | .sh, .bash | Direct read |
| Rust | .rs | Direct read |
| C/C++ | .c, .h, .cpp | Direct read |
| PDF | .pdf | PyMuPDF or pdfplumber |

### 2.2 CLI Search Tool (`yaaos-find`)

A command-line tool that:
- Takes a natural language query as input
- Embeds the query using the same model
- Runs hybrid search (vector similarity + keyword FTS5)
- Returns ranked results with:
  - File path
  - Relevance score
  - Matching snippet (the relevant chunk)
  - File metadata (size, modified date)

```bash
# Basic semantic search
$ yaaos-find "notes about the API redesign"

# Search with filters
$ yaaos-find "python database helpers" --type py
$ yaaos-find "meeting notes from March" --after 2026-03-01

# Show more context
$ yaaos-find "routing bug fix" --snippets --top 5

# Index status
$ yaaos-find --status
Indexed: 1,247 files | 8,392 chunks | DB size: 42 MB
Last indexed: 2 seconds ago
```

### 2.3 Provider Abstraction (Simple)

A thin abstraction layer for the embedding model so cloud providers can be swapped in:

```python
# Default: local
provider = LocalEmbeddingProvider(model="all-MiniLM-L6-v2")

# Swap to OpenAI for testing
provider = OpenAIEmbeddingProvider(model="text-embedding-3-small")

# Configured via ~/.config/yaaos/config.toml
```

---

## 3. What's NOT in the MVP

| Feature | Why Not Yet |
|---------|-----------|
| FUSE virtual mount | Adds complexity. inotify on a real dir is simpler for MVP. |
| SystemAgentd | Phase 3. SFS daemon runs standalone for now. |
| Agentic Shell (aish) | Phase 4. CLI tool is sufficient for demo. |
| Desktop Environment | Phase 5. Way future. |
| LLM-powered query rewriting | Nice-to-have. Raw embedding search is the baseline. |
| Dynamic auto-organization | Phase 2 feature. MVP is search-only. |
| Multi-directory watching | MVP watches one directory. Configurable later. |
| Real-time file deduplication | Future feature. |
| Image/audio embedding | Text-only for MVP. |

---

## 4. Project Structure

```
yaaos/
├── .planning/
│   └── drafts/            # Planning documents (you are here)
├── src/
│   └── yaaos/
│       ├── __init__.py
│       ├── cli.py          # yaaos-find CLI entry point
│       ├── daemon.py       # SFS daemon (inotify + indexing)
│       ├── indexer.py      # Text extraction + chunking
│       ├── embeddings.py   # Embedding provider abstraction
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── local.py    # sentence-transformers (default)
│       │   └── openai.py   # OpenAI embeddings (optional)
│       ├── search.py       # Hybrid search engine
│       ├── db.py           # SQLite + sqlite-vec database layer
│       └── config.py       # Configuration loading (TOML)
├── tests/
│   ├── test_indexer.py
│   ├── test_search.py
│   └── test_embeddings.py
├── pyproject.toml          # Project config (Poetry/uv)
├── README.md
└── LICENSE
```

---

## 5. Database Schema

```sql
-- File metadata
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT,
    size_bytes INTEGER,
    modified_at TIMESTAMP,
    indexed_at TIMESTAMP,
    content_hash TEXT,          -- SHA-256 for change detection
    chunk_count INTEGER DEFAULT 0
);

-- Text chunks with embeddings
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER,       -- Position in file
    chunk_text TEXT,            -- Raw text of this chunk
    embedding BLOB,            -- 384-dim float32 vector (sqlite-vec)
    token_count INTEGER
);

-- Full-text search (keyword matching)
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_text,
    content='chunks',
    content_rowid='id'
);

-- Vector similarity index (sqlite-vec)
CREATE VIRTUAL TABLE chunks_vec USING vec0(
    embedding float[384]
);

-- Search combines:
-- 1. chunks_vec for semantic similarity (cosine distance)
-- 2. chunks_fts for keyword matching (BM25)
-- 3. Reciprocal Rank Fusion (RRF) to merge results
```

---

## 6. Key Algorithms

### Hybrid Search (RRF Fusion)

```
For a query Q:
1. Embed Q → vector
2. Run vector search: top 20 results by cosine similarity
3. Run FTS5 search: top 20 results by BM25 score
4. Merge using Reciprocal Rank Fusion:
   score(doc) = Σ 1/(k + rank_in_list) for each list containing doc
   where k = 60 (standard RRF constant)
5. Return top N by fused score
```

### Chunking Strategy

```
For a file with content C:
1. Split C into sentences (or by newlines for code)
2. Group sentences into chunks of ~512 tokens
3. Each chunk overlaps with previous by ~50 tokens
4. Store each chunk with its position index
```

---

## 7. Success Criteria

The MVP is "done" when:

- [ ] `yaaos-sfs` daemon watches `~/semantic/` and auto-indexes new/modified files
- [ ] Supports at least 10 file types (text, markdown, code, PDF)
- [ ] Indexing a file takes < 500ms (excluding model load)
- [ ] Search returns results in < 200ms for a 1000-file index
- [ ] `yaaos-find "natural language query"` returns relevant ranked results
- [ ] Hybrid search (semantic + keyword) outperforms either alone
- [ ] Provider abstraction allows swapping to OpenAI embeddings via config
- [ ] Works on Linux (native) and Windows (WSL)
- [ ] Has basic tests for indexer, search, and embedding providers

---

## 8. Dependencies

```toml
[project]
name = "yaaos"
version = "0.1.0"
requires-python = ">=3.11"

[project.dependencies]
sentence-transformers = ">=3.0"    # Local embedding model
sqlite-vec = ">=0.1"              # Vector search in SQLite
inotify-simple = ">=1.3"          # File watching (Linux)
watchdog = ">=4.0"                # File watching (cross-platform fallback)
click = ">=8.0"                   # CLI framework
rich = ">=13.0"                   # Pretty terminal output
tomli = ">=2.0"                   # TOML config parsing
pymupdf = ">=1.24"                # PDF text extraction

[project.optional-dependencies]
openai = ["openai>=1.0"]          # Optional cloud provider
anthropic = ["anthropic>=0.30"]   # Optional cloud provider

[project.scripts]
yaaos-find = "yaaos.cli:main"
yaaos-sfs = "yaaos.daemon:main"
```

---

## 9. Development Milestones

| # | Milestone | Deliverable |
|---|-----------|-------------|
| 1 | **DB + Indexer** | SQLite schema, text extraction, chunking logic |
| 2 | **Embedding Provider** | Local provider with all-MiniLM-L6-v2, provider abstraction |
| 3 | **Daemon** | inotify watcher, auto-index on file change |
| 4 | **Search Engine** | Hybrid search (vector + FTS5 + RRF fusion) |
| 5 | **CLI Tool** | `yaaos-find` with pretty output |
| 6 | **Provider Plugin** | OpenAI embedding provider, config-based switching |
| 7 | **Testing & Polish** | Tests, error handling, README, demo script |
