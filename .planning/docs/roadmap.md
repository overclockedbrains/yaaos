# YAAOS Development Roadmap

**Project:** YAAOS (Your Agentic AI Operating System)
**Status:** Active Development
**Start Date:** 2026-03-13
**Last Updated:** 2026-03-15

---

## Phase Overview

```
Phase 1 ──▶ Phase 1.5 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──▶ Phase 5 ──▶ Phase 6
  SFS         SFS v2       Model       System      Agentic      Desktop     Arch
 (MVP)      (Production)    Bus        Agentd       Shell         DE        ISO
  ✓            ✓
```

---

## Phase 1: Semantic File System (MVP) — DONE

**Goal:** A working semantic search tool — drop files in a folder, search by meaning.

**Platform:** WSL / Linux
**Stack:** Python 3.11+, uv, sentence-transformers, sqlite-vec

| Milestone | Deliverable | Status |
|-----------|-------------|--------|
| 1.1 | Project setup (uv, structure, config) | Done |
| 1.2 | DB layer (SQLite + sqlite-vec schema) | Done |
| 1.3 | Indexer (text extraction + chunking) | Done |
| 1.4 | Embedding provider (local all-MiniLM-L6-v2 + abstraction) | Done |
| 1.5 | File watcher daemon (watchdog) | Done |
| 1.6 | Search engine (hybrid: vector + FTS5 + RRF fusion) | Done |
| 1.7 | CLI tool (`yaaos-find`) | Done |
| 1.8 | OpenAI provider plugin (config-based swap) | Done |
| 1.9 | Tests & polish | Done |

**Dependencies:** None (standalone)

---

## Phase 1.5: SFS v2 (Production-Ready) — DONE

**Goal:** Handle real-world 22GB+ folders at scale with multi-format support, smart chunking, and GPU acceleration.

**Completed:** 2026-03-15 | **Version:** 0.2.0 | **Tests:** 136 passing

| Milestone | Deliverable | Status |
|-----------|-------------|--------|
| A1 | 4-layer file filtering (.gitignore, .sfsignore, extension whitelist, size limit) | Done |
| A2 | Stat-based change detection (mtime_ns + size_bytes, xxHash128 fallback) | Done |
| A3 | Batch embedding with debouncing + parallel I/O | Done |
| B1 | Extractor registry with graceful degradation | Done |
| B2 | Document extractors (PDF, DOCX, PPTX, XLSX, EPUB, RTF) | Done |
| B3 | Media metadata extractors (EXIF, audio tags, video metadata) | Done |
| C1 | Chunker registry with extension → chunker mapping | Done |
| C2 | Tree-sitter AST-aware code chunking | Done |
| C3 | Section-aware Markdown/RST chunking | Done |
| D1 | 3-signal RRF search (vector + FTS5 + path matching) with recency boost | Done |
| D2 | Voyage + Ollama provider plugins | Done |
| D3 | Daemon query server + periodic re-scan + GPU auto-detection | Done |
| D4 | 136 tests, ruff-clean | Done |

### SFS's Role in YAAOS

SFS is not just a search tool — it is the **semantic memory layer** that every higher layer depends on:

| Layer | How It Uses SFS |
|-------|----------------|
| **Model Bus** | SFS provides context for all AI calls — OS-level RAG. Any component needing relevant files queries SFS. |
| **SystemAgentd** | Agents discover related files, dependencies, and docs without explicit paths. Agents gain situational awareness. |
| **Agentic Shell** | Replaces `find`/`grep`/`locate` with intent-based search. `"open everything related to login"` just works. |
| **Desktop Environment** | Powers context workspaces — the desktop auto-organizes by surfacing semantically related files to what you're working on. |

**Dependencies:** None (standalone, consumed by all layers above)

---

## Phase 2: Model Bus (Unified AI Runtime) ← NEXT

**Goal:** A single API that all YAAOS components use for AI inference, with pluggable providers.

| Milestone | Deliverable |
|-----------|-------------|
| 2.1 | Model Bus daemon (Unix socket, JSON-RPC) |
| 2.2 | Ollama provider (embedding + generation) |
| 2.3 | OpenAI provider |
| 2.4 | Anthropic provider |
| 2.5 | Provider routing + config (`providers.toml`) |
| 2.6 | Resource-aware model loading (VRAM/RAM checks) |
| 2.7 | Migrate SFS to use Model Bus instead of direct embedding |

**Success Criteria:**
- Any component can request `embed()` or `generate()` via socket
- `yaaos config set default.generation openai` switches providers instantly
- Model Bus prevents OOM by checking available resources

**Dependencies:** Phase 1 (SFS is the first consumer)

---

## Phase 3: SystemAgentd (Agent Orchestration)

**Goal:** A supervisor daemon that manages AI agents as systemd services.

| Milestone | Deliverable |
|-----------|-------------|
| 3.1 | SystemAgentd supervisor daemon |
| 3.2 | Agent service template (`agent@.service`) |
| 3.3 | Log-Agent (real-time journald analysis) |
| 3.4 | Crash-Agent (core dump analysis, socket-activated) |
| 3.5 | Resource-Agent (CPU/RAM prediction, cgroup tuning) |
| 3.6 | Net-Agent (network anomaly detection) |
| 3.7 | Agent Bus API (query status, start/stop agents) |
| 3.8 | Migrate SFS daemon to run as a managed agent |

**Success Criteria:**
- Agents run as systemd services with cgroup isolation
- Crash-Agent analyzes a core dump and suggests a fix
- Log-Agent surfaces anomalies from journalctl in real-time
- `systemagentctl status` shows all running agents

**Dependencies:** Phase 2 (agents use Model Bus for inference)

---

## Phase 4: Agentic Shell (aish)

**Goal:** An intent-driven shell that understands natural language commands.

| Milestone | Deliverable |
|-----------|-------------|
| 4.1 | Shell prototype (bash/nushell wrapper + LLM intent layer) |
| 4.2 | Intent parser (NL → command plan via Model Bus) |
| 4.3 | Audit display (show generated commands before execution) |
| 4.4 | Session memory (infinite context, recall past commands) |
| 4.5 | Semantic pipes (`cat log | llm "find database errors"`) |
| 4.6 | Integration with SFS (`yaaos-find` built into shell) |
| 4.7 | Integration with SystemAgentd (agent status in shell) |

**Success Criteria:**
- `compress python files and send to staging` generates and executes correct commands
- `re-run yesterday's docker command but on port 8080` works via session memory
- Standard shell commands still work normally (fallback to bash)

**Dependencies:** Phase 2 (Model Bus), Phase 3 (agent integration)

---

## Phase 5: Desktop Environment

**Goal:** Context-driven dynamic workspaces managed by AI.

| Milestone | Deliverable |
|-----------|-------------|
| 5.1 | Choose DE base (Wayland compositor: sway/river/custom) |
| 5.2 | Context workspace manager (Coding, Research, Gaming) |
| 5.3 | Automatic resource suspension (freeze inactive contexts) |
| 5.4 | Natural language window management |
| 5.5 | Ambient context awareness (window + file + browser) |
| 5.6 | Notification system for agent alerts |

**Success Criteria:**
- Switching from "Gaming" to "Coding" context frees RAM from suspended apps
- "Put terminal left, browser right, browser wider" works
- Agent notifications appear as desktop notifications

**Dependencies:** Phase 3 (agents), Phase 4 (shell integration)

---

## Phase 6: Arch Linux Distribution

**Goal:** A bootable, installable YAAOS ISO built with archiso.

| Milestone | Deliverable |
|-----------|-------------|
| 6.1 | archiso profile (package list, custom configs) |
| 6.2 | Custom pacman repository for YAAOS packages |
| 6.3 | Calamares installer integration |
| 6.4 | GPU auto-detection (Vulkan default, CUDA/ROCm optional) |
| 6.5 | First boot experience (guided setup, model download) |
| 6.6 | Live USB demo mode |
| 6.7 | Documentation + website |

**Success Criteria:**
- Boot from USB → install → working YAAOS with all components
- GPU detected and configured automatically
- First boot wizard downloads chosen LLM model via Ollama
- All phases (SFS, agents, shell, DE) work out of the box

**Dependencies:** All previous phases

---

## Cross-Cutting Concerns

| Concern | Approach |
|---------|----------|
| **Privacy** | Local-first. Cloud providers are opt-in. No telemetry. |
| **Performance** | Embedding < 500ms/file. Search < 200ms. Agent CPU < 10%. |
| **Security** | No arbitrary code execution without user confirmation. Sandboxed agents. |
| **Testing** | Unit tests per component. Integration tests per phase. |
| **Language migration** | Python MVP → Rust production (per component, not all at once). |
