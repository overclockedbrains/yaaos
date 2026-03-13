# YAAOS Tech Stack Decisions

**Status:** Approved
**Last Updated:** 2026-03-13
**Project:** YAAOS (Your Agentic AI Operating System)

---

## 1. Base Distribution

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **Arch Linux** | **CHOSEN** | Rolling release = latest AI tooling always. AUR covers the long tail. `archiso` is proven (CachyOS, EndeavourOS). Minimal base = no bloat. systemd native. |
| Debian/Ubuntu | Rejected | Package staleness is a dealbreaker for fast-moving AI ecosystem. Better NVIDIA support but not enough to outweigh. |
| NixOS | Inspiration only | Declarative model is inspiring for agent config. But painful CUDA support, steep learning curve, immature derivative distro tooling. |
| Void Linux | Eliminated | No systemd = no foundation for SystemAgentd. Poor GPU/AI tooling. |
| Alpine Linux | Eliminated | musl libc breaks CUDA/PyTorch/ROCm. No systemd. Not designed for desktop. |

---

## 2. Programming Languages

### Prototype Phase (MVP)

| Component | Language | Why |
|-----------|----------|-----|
| Semantic File System | **Python 3.11+** | Fastest iteration. `pyfuse3` for FUSE, `sentence-transformers` for embeddings, `sqlite-vec` bindings available. |
| CLI tools | **Python** | Shared codebase with SFS daemon. Click/Typer for CLI framework. |
| Config/scripts | **TOML + Bash** | Standard Linux config patterns. |

### Production Phase (Future)

| Component | Language | Why |
|-----------|----------|-----|
| FUSE layer | **Rust** (`fuser` crate) | Performance, memory safety, no GC pauses during file I/O. |
| SystemAgentd | **Rust** | System daemon needs low overhead. D-Bus via `zbus` crate. |
| Agentic Shell | **Rust** | Fork/extend Nushell (already Rust). |
| Model Bus | **Rust** | Hot path for inference routing needs minimal latency. |

---

## 3. AI / ML Stack

### Embedding Model

| Model | Size | Speed (CPU) | Dims | Quality (MTEB) | Verdict |
|-------|------|-------------|------|-----------------|---------|
| **all-MiniLM-L6-v2** | 80 MB | ~4ms/sent | 384 | ~57 | **MVP default** — fastest, good enough |
| bge-small-en-v1.5 | 130 MB | ~5ms/sent | 384 | ~61 | Good alternative |
| nomic-embed-text-v1.5 | 550 MB | ~15ms/sent | 768 | ~63 | **Production upgrade** — handles code+text |
| mxbai-embed-large-v1 | 1.3 GB | ~25ms/sent | 1024 | ~65 | Too large for system daemon |

**Decision:** Start with `all-MiniLM-L6-v2` via `sentence-transformers` (CPU). Upgrade to `nomic-embed-text` later for code understanding.

### LLM Runtime

| Runtime | Verdict | Rationale |
|---------|---------|-----------|
| **Ollama** | **CHOSEN** | Best UX, systemd integration, model management, OpenAI-compatible API, embedding API built-in. |
| llama.cpp (direct) | Future optimization | For hot paths where HTTP overhead matters. Ollama uses it internally anyway. |
| vLLM | Eliminated | Designed for data centers, not desktops. Slow startup, heavy GPU requirements. |

### LLM Models (for intent/reasoning tasks)

Target hardware: GTX 1650 Ti (4GB VRAM), 16GB RAM.

| Model | Quantization | VRAM | Use Case |
|-------|-------------|------|----------|
| Phi-3-mini (3.8B) | Q4_K_M | ~2.5 GB | Default for shell intent parsing, log analysis |
| Llama-3.2-3B | Q4_K_M | ~2.2 GB | Alternative, good instruction following |
| Mistral-7B | Q4_K_M | ~4.5 GB | Exceeds VRAM — CPU offload or cloud fallback |

**Decision:** Default to Phi-3-mini Q4 for local inference. Reserve VRAM headroom for embedding model if needed.

---

## 4. Vector Storage

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **sqlite-vec** | **CHOSEN** | SQLite is ubiquitous on Linux. Single-file DB. Metadata + vectors in one place. SQL queries. No extra daemon. Handles 100K+ files easily. |
| ChromaDB | Rejected | Python-only, stability issues, requires separate process. |
| Qdrant | Rejected | Overkill server architecture for single-user desktop. |
| FAISS | Rejected | No built-in persistence, no metadata filtering. Raw library, not a DB. |
| LanceDB | Runner-up | Good for production scale-up. Versioned data. But younger ecosystem. |

---

## 5. File System Layer

| Component | Choice | Rationale |
|-----------|--------|-----------|
| FUSE library | **pyfuse3** (MVP) → **fuser** (Rust, production) | pyfuse3 is async, FUSE3 native, actively maintained. fuser is the Rust standard. |
| File watching | **inotify** via `inotify_simple` (Python) | Linux-native, efficient, no polling. Upgrade to fanotify in Rust phase. |
| Text extraction | **textract** + custom handlers | Handles PDF, DOCX, code files. Extensible. |
| Chunking | Custom: 512 tokens, 50 overlap | Standard RAG chunking strategy. Tunable. |

---

## 6. AI Provider System (Pluggable)

The Model Bus supports swappable providers for easy testing with cloud models.

```toml
# ~/.config/yaaos/providers.toml

[default]
embedding = "local"
generation = "local"

[providers.local]
type = "ollama"
url = "http://localhost:11434"
embedding_model = "all-minilm:l6-v2"
generation_model = "phi3:mini"

[providers.openai]
type = "openai"
api_key_env = "OPENAI_API_KEY"
embedding_model = "text-embedding-3-small"
generation_model = "gpt-4o-mini"

[providers.anthropic]
type = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"
generation_model = "claude-sonnet-4-20250514"

# Switch providers easily:
# yaaos config set default.generation openai
# yaaos config set default.generation local
```

**Key design**: Provider switching is a single config change. All components use the Model Bus API, never talk to providers directly. This makes testing with GPT-4o or Claude trivial without changing any component code.

---

## 7. Inter-Process Communication

| Mechanism | Use Case |
|-----------|----------|
| **Unix domain sockets** | Model Bus, Agent Bus, SFS search API |
| **D-Bus** | systemd integration, agent lifecycle |
| **inotify** | File change detection for SFS |
| **JSON-RPC 2.0** | Wire protocol over Unix sockets |

---

## 8. Development & Build Tools

| Tool | Purpose |
|------|---------|
| **archiso** | Build bootable YAAOS ISO |
| **Calamares** | Graphical installer for live ISO |
| **mkosi** | Fast VM testing during development |
| **Poetry / uv** | Python dependency management |
| **Custom pacman repo** | Distribute YAAOS packages |

---

## 9. Hardware Requirements

### Minimum (CPU-only inference)
- x86_64 CPU with AVX2 (2015+)
- 8 GB RAM
- 20 GB disk (OS + models)
- No GPU required (embedding + small LLM on CPU)

### Recommended (GPU-accelerated)
- Modern x86_64 CPU (8+ cores)
- 16 GB RAM
- NVIDIA GPU with 4+ GB VRAM (GTX 1650+) or AMD with ROCm support
- 50 GB disk (OS + multiple models)
- SSD strongly recommended (embedding index I/O)

### Developer's Machine (reference)
- AMD Ryzen 7 4800H (8 cores)
- 16 GB RAM
- NVIDIA GTX 1650 Ti (4 GB VRAM)
- Target: Phi-3-mini Q4 on GPU, embeddings on CPU
