# YAAOS Semantic File System (v2)

The **YAAOS Semantic File System (SFS)** is a powerful background daemon and CLI toolkit designed to semantically index and search your local files. Instead of searching by filename or exact keywords, SFS lets you search your workspace "by meaning" using advanced local or cloud-based embedding models.

SFS v2 features an ultra-fast watcher, chunking logic, an embedded light vector database (`sqlite-vec`), and extremely aggressive 4-layer file filtering to ensure maximum efficiency.

👉 **Confused about what runs where?** Read the [Architecture & Flow Guide](ARCHITECTURE.md) for a crystal-clear breakdown.

## 📋 Prerequisites

To run this project optimally, the following stack is required:

1. **Windows Subsystem for Linux (WSL)**
   SFS runs best inside a Linux environment like WSL on Windows. All terminal commands must be run from an active WSL session (e.g., Ubuntu).
2. **`uv` - Python Package Manager**
   We use `uv`, an extremely fast Python package manager written in Rust.
   *Install `uv` (WSL/Linux):* `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. **Python 3.12+**
   `uv` will automatically manage Python versions for you.

## 🚀 Installation & Setup

1. **Open your WSL terminal** and navigate to the SFS directory:
   ```bash
   cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-sfs
   ```

2. **Sync Dependencies**:
   `uv` will create the virtual environment (`.venv`) and install all lockfile dependencies incredibly fast.
   ```bash
   uv sync
   ```

## ⚙️ Configuration

SFS behaviour is driven by configuration variables. Some common features to tune:

- **Directory to Watch:** The root of the repository you are indexing.
- **Embedding Provider:** `local` (SentenceTransformers, runs on CPU/GPU locally) or `openai` (Requires `OPENAI_API_KEY`).
- **File Limits:** Set `max_file_size_mb` (default is usually 5MB).

You can pass environment variables or define them where `config.py` specifies. By default, it aggressively ignores noise like `node_modules`, `.git`, `.venv`, and respects your local `.gitignore`.

## 🧪 Running Tests & Linting

Before pushing code or after making modifications, ensure you run the comprehensive test suite to keep the CI green!

**1. Fast Format & Lint:**
```bash
# Check code style and format
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Auto-fix fixable issues
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/
```

**2. Fast Tests (Logic & Mocks - Extremely Fast):**
```bash
uv run pytest tests/ -v --ignore=tests/test_providers.py --ignore=tests/test_integration.py --ignore=tests/stress -x
```

**3. Full Integration Tests (Heavy - Loads Models):**
```bash
uv run pytest tests/ -v --ignore=tests/stress
```

## 🏃‍♂️ Upping It (Running the Daemon)

To start the SFS background daemon that will actively watch your files, chunk changes, get embeddings, and upsert them to SQLite:

```bash
uv run yaaos-sfs
```

**What happens?**
1. **Query Server:** A TCP query server starts on `localhost:9749`, ready to serve instant searches from the CLI.
2. **Initial Scan:** SFS heavily filters your tree, finds allowed files, and compares them with the SQLite DB. Missing/outdated files are batched and embedded.
3. **Watchdog Active:** The daemon continuously listens to file creations, saves, and deletions. Uses a debouncer (e.g. 1500ms) so wildly hitting `Ctrl+S` doesn't saturate the indexer.
4. **Periodic Re-scan:** Every 10 minutes (configurable via `rescan_interval_min`), the daemon re-scans the directory to catch files the OS watcher may have missed (e.g. bulk copies, network drive syncs, or OS event buffer overflows).
5. *Press `Ctrl+C` to elegantly spin down the daemon and close the DB.*

## 🔍 Searching (Instant via Daemon)

SFS provides a command-line utility to query your indexed semantic filesystem:

```bash
# Instant search — routes through the running daemon (no model loading)
uv run yaaos-find "How does the file filter pipeline work?"

# Check daemon and index status
uv run yaaos-find --status
```

When the daemon is running, queries are **instant** — the CLI sends your query over TCP to the daemon, which already has the embedding model in memory. No model loading overhead.

If the daemon is not running, the CLI gracefully falls back to loading the model directly (slower first query, same results).
