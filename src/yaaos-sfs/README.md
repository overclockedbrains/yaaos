# YAAOS Semantic File System (SFS) v2

The **YAAOS Semantic File System (SFS)** is a background daemon and CLI toolkit that lets you search your local files **by meaning**, not just by filename or exact keywords. SFS v2 handles real-world workspaces (22GB+ dev folders) with multi-format support, smart chunking, and swappable embedding providers.

For the full architecture breakdown, read the [Architecture Guide](ARCHITECTURE.md).

## What's New in v2

- **65+ file types** -- code, documents (PDF, DOCX, XLSX, EPUB), and media metadata (EXIF, ID3)
- **Smart chunking** -- tree-sitter AST chunking for code, heading-aware chunking for prose, key-value extraction for JSON/YAML/CSV
- **3-signal hybrid search** -- vector similarity + keyword matching + path matching, fused with RRF
- **Stat-first change detection** -- xxHash128 instead of SHA-256 (60-100x faster)
- **4-layer file filtering** -- .gitignore + .sfsignore + hardcoded noise dirs + size limits
- **Swappable providers** -- local (default), OpenAI, Voyage AI, Ollama
- **Batch embedding + debouncing** -- efficient handling of bulk file changes

## Prerequisites

1. **WSL (Windows)** -- SFS runs best inside Linux/WSL. All commands below assume a Linux shell.
2. **`uv`** -- Fast Python package manager written in Rust.
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Python 3.12+** -- `uv` manages this automatically.

## Installation

```bash
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-sfs

# Core install
uv sync

# Optional: document extraction (DOCX, PPTX, XLSX, EPUB, RTF)
uv sync --extra docs

# Optional: media metadata (image EXIF, audio ID3, video tags)
uv sync --extra media

# Optional: tree-sitter AST chunking for code
uv sync --extra code

# Everything
uv sync --extra all
```

## Configuration

SFS reads from `~/.config/yaaos/config.toml`:

```toml
[sfs]
watch_dir = "~/projects"           # Directory to watch
db_path = "~/.local/share/yaaos/sfs.db"
max_file_size_mb = 5.0             # Skip files larger than this
batch_size = 50                    # Chunks per embedding batch
debounce_ms = 1500                 # Event debounce window
rescan_interval_min = 10           # Periodic re-scan interval
query_port = 9749                  # TCP query server port

[embedding]
provider = "local"                 # "local", "openai", "voyage", "ollama"
model = "all-MiniLM-L6-v2"

[providers.openai]
api_key_env = "OPENAI_API_KEY"

[providers.voyage]
api_key_env = "VOYAGE_API_KEY"

[providers.ollama]
base_url = "http://localhost:11434"
model = "nomic-embed-text"
```

Custom ignore patterns: create `.sfsignore` in your watch directory (same syntax as `.gitignore`). See [.sfsignore.default](.sfsignore.default) for the shipped defaults.

## Running the Daemon

```bash
uv run yaaos-sfs
```

This will:
1. Start the TCP query server on `localhost:9749`
2. Run an initial scan with 4-layer filtering
3. Index new/changed files with batch embedding + tqdm progress bar
4. Watch for file changes with debouncing
5. Periodically re-scan every 10 minutes

Press `Ctrl+C` to shut down gracefully.

## Searching

```bash
# Semantic search (instant via daemon)
uv run yaaos-find "How does the file filter work?"

# Filter by file type (comma-separated)
uv run yaaos-find "quarterly report" --type pdf,docx

# More results
uv run yaaos-find "database helpers" --top 20

# Check index status and per-type breakdown
uv run yaaos-find --status
```

When the daemon is running, queries are **instant** (~50ms). If the daemon is down, the CLI falls back to loading the model locally (~2s first query).

## Tests & Linting

```bash
# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Fast unit tests (no model loading)
uv run pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/stress_test.py -x

# Full integration tests (loads embedding model)
uv run pytest tests/ -v --ignore=tests/stress_test.py
```

## Provider Options

| Provider | Install | Best For |
|----------|---------|----------|
| **local** (default) | included | General use, offline, no API keys |
| **openai** | `uv sync --extra openai` | High-quality general embeddings |
| **voyage** | `uv sync --extra voyage` | Best-in-class code search |
| **ollama** | Ollama server running | Local models, any Ollama model |
