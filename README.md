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
│           Model Bus (Phase 2 — next)            │
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
| **SFS v2** | Done | Multi-format indexing (50+ extensions), tree-sitter code chunking, 3-signal hybrid search, GPU acceleration. 136 tests. |
| **Model Bus** | Next | Unified AI runtime with pluggable providers (Ollama, OpenAI, Anthropic) via Unix socket |
| **SystemAgentd** | Planned | Agent orchestration — log analysis, crash diagnosis, resource prediction |
| **Agentic Shell** | Planned | Natural language shell with session memory |
| **Desktop** | Planned | Context-driven workspaces |
| **Arch ISO** | Planned | Bootable YAAOS distribution |

## Project Structure

```
yaaos/
├── src/
│   └── yaaos-sfs/          # Semantic File System (v0.2.0)
│       ├── src/yaaos_sfs/   # Core: daemon, search, extractors, chunkers, providers
│       └── tests/           # 136 tests
├── .planning/
│   └── docs/                # Architecture, roadmap, tech stack, SFS v2 plan
├── PROGRESS.md              # What's done, what's next
└── README.md                # You are here
```

## Setup

- Requires Linux or WSL
- Uses [uv](https://docs.astral.sh/uv/) for Python package management

```bash
cd src/yaaos-sfs
uv sync --group dev          # Install with dev deps
uv run yaaos-sfs             # Start the daemon
uv run yaaos-find "query"    # Search your files
```

## Docs

- [Architecture](.planning/docs/architecture.md) — 6-layer system design + how SFS powers every layer
- [Roadmap](.planning/docs/roadmap.md) — Full 6-phase plan with milestones
- [Tech Stack](.planning/docs/tech_stack.md) — Every technology choice with rationale
- [SFS v2 Plan](.planning/docs/sfs_v2_plan.md) — Detailed implementation plan (completed)
- [SFS Architecture](src/yaaos-sfs/ARCHITECTURE.md) — Module-level SFS internals
