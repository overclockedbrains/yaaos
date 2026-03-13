# YAAOS Development Roadmap

**Project:** YAAOS (Your Agentic AI Operating System)
**Status:** Active Development
**Start Date:** 2026-03-13

---

## Phase Overview

```
Phase 1 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──▶ Phase 5 ──▶ Phase 6
  SFS        Model       System      Agentic      Desktop     Arch
 (MVP)        Bus        Agentd       Shell         DE        ISO
```

---

## Phase 1: Semantic File System (MVP) ← CURRENT

**Goal:** A working semantic search tool — drop files in a folder, search by meaning.

**Platform:** WSL / Linux
**Stack:** Python 3.11+, uv, pyfuse3, sentence-transformers, sqlite-vec

| Milestone | Deliverable | Status |
|-----------|-------------|--------|
| 1.1 | Project setup (uv, structure, config) | Pending |
| 1.2 | DB layer (SQLite + sqlite-vec schema) | Pending |
| 1.3 | Indexer (text extraction + chunking) | Pending |
| 1.4 | Embedding provider (local all-MiniLM-L6-v2 + abstraction) | Pending |
| 1.5 | File watcher daemon (inotify / watchdog) | Pending |
| 1.6 | Search engine (hybrid: vector + FTS5 + RRF fusion) | Pending |
| 1.7 | CLI tool (`yaaos-find`) | Pending |
| 1.8 | OpenAI provider plugin (config-based swap) | Pending |
| 1.9 | Tests & polish | Pending |

**Success Criteria:**
- `yaaos-sfs` daemon watches ~/semantic/ and auto-indexes files
- `yaaos-find "natural language query"` returns ranked results < 200ms
- Supports 10+ file types (txt, md, py, js, ts, json, yaml, sh, rs, c, pdf)
- Provider swap via config (local ↔ OpenAI)

**Dependencies:** None (standalone)

---

## Phase 2: Model Bus (Unified AI Runtime)

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
