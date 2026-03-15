# YAAOS

**Your Agentic AI Operating System** — an AI-native Linux distro where intelligence is built into every layer, from the filesystem to the desktop.

## Vision

Most "AI tools" bolt a chatbot onto existing software. YAAOS builds AI into the OS itself:

- **Files have meaning** — the filesystem understands content, not just filenames
- **Agents run as services** — AI daemons monitor, analyze, and act on your system
- **The shell understands intent** — type what you want, not how to do it
- **The desktop organizes itself** — workspaces adapt to what you're working on

## Architecture

```
┌─────────────────────────────────────────────────┐
│           Desktop Environment (Phase 5)         │
│        Context-driven AI workspaces             │
├─────────────────────────────────────────────────┤
│           Agentic Shell — aish (Phase 4)        │
│        Intent-driven, NL commands               │
├─────────────────────────────────────────────────┤
│           SystemAgentd (Phase 3)                │
│        Agent orchestration as systemd services  │
├─────────────────────────────────────────────────┤
│         Semantic File System — SFS (Done)       │
│        The memory layer — semantic search,      │
│        multi-format indexing, GPU-accelerated   │
├─────────────────────────────────────────────────┤
│           Model Bus (Done)                      │
│        Unified AI runtime, pluggable providers  │
├─────────────────────────────────────────────────┤
│           Base OS — Arch Linux                  │
│        systemd · pacman · Linux Kernel · GPU    │
└─────────────────────────────────────────────────┘
```

Every layer above the Model Bus depends on **SFS** as its semantic memory — it's how agents understand your workspace, how the shell resolves intent to files, and how the desktop knows what to surface.

## Current Status

| Component | Status | Details |
|-----------|--------|---------|
| **SFS v2** | Done | Multi-format indexing (50+ extensions), tree-sitter code chunking, 3-signal hybrid search, GPU acceleration. 154 tests. |
| **Model Bus** | Done | Unified AI inference daemon — 5 providers (Ollama, OpenAI, Anthropic, Voyage, local), JSON-RPC 2.0 over Unix socket, VRAM/RAM resource management, streaming, hot-reload. 173 tests. |
| **SystemAgentd** | Next | Agent orchestration — log analysis, crash diagnosis, resource prediction |
| **Agentic Shell** | Planned | Natural language shell with session memory |
| **Desktop** | Planned | Context-driven workspaces |
| **Arch ISO** | Planned | Bootable YAAOS distribution |

## Project Structure

```
yaaos/
├── src/
│   ├── yaaos-sfs/           # Semantic File System (v0.2.0)
│   │   ├── src/yaaos_sfs/   # Core: daemon, search, extractors, chunkers, providers
│   │   └── tests/           # 154 tests
│   └── yaaos-modelbus/      # Model Bus — unified AI inference daemon (v0.1.0)
│       ├── src/yaaos_modelbus/  # Daemon, server, router, providers, client SDK
│       └── tests/           # 173 tests
├── .planning/
│   └── docs/                # Architecture, roadmap, tech stack, phase plans
├── PROGRESS.md              # What's done, what's next
└── README.md                # You are here
```

## Setup

- Requires Linux or WSL
- Uses [uv](https://docs.astral.sh/uv/) for Python package management

```bash
# Model Bus (AI inference daemon)
cd src/yaaos-modelbus
cp .env.example .env         # Configure socket path, API keys
uv sync                      # Install dependencies
uv run yaaos-modelbusd       # Start the daemon
uv run yaaos-bus health      # Check status

# Semantic File System
cd src/yaaos-sfs
uv sync --group dev          # Install with dev deps
uv run yaaos-sfs             # Start the daemon
uv run yaaos-find "query"    # Search your files
```

## Docs

- [Architecture](.planning/docs/architecture.md) — 6-layer system design + how SFS powers every layer
- [Roadmap](.planning/docs/roadmap.md) — Full 6-phase plan with milestones
- [Tech Stack](.planning/docs/tech_stack.md) — Every technology choice with rationale
- [Phase 2 Plan](.planning/docs/phase2_model_bus_plan.md) — Model Bus implementation plan (completed)
- [SFS v2 Plan](.planning/docs/sfs_v2_plan.md) — Detailed implementation plan (completed)
- [SFS Architecture](src/yaaos-sfs/ARCHITECTURE.md) — Module-level SFS internals
