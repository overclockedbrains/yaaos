# YAAOS Progress

## Done

- **Planning & Architecture** — Full system design documented
  - 6-layer architecture (Base OS → Desktop Environment)
  - Tech stack decisions (Arch Linux, Python→Rust, sqlite-vec, Ollama)
  - 6-phase roadmap with milestones

- **SFS MVP (Phase 1)** — Semantic File System with working daemon + CLI search
  - SQLite + sqlite-vec DB with vector + FTS5 hybrid search (RRF fusion)
  - File watcher daemon (watchdog) auto-indexes ~/semantic/
  - `yaaos-find` CLI with rich output, --type filter, --status
  - Local embedding (all-MiniLM-L6-v2) + OpenAI provider plugin
  - PDF extraction via PyMuPDF

- **SFS v2 (Phase 1.5)** — Production-ready upgrade | Completed 2026-03-15
  - **Phase A: Core Infrastructure** — done
    - 4-layer file filtering (hardcoded ignores, .gitignore + .sfsignore, extension whitelist, size limit)
    - Generated/minified file + lock file filtering (.min.js, .bundle.js, package-lock.json, etc.)
    - Stat-based change detection (mtime_ns + size_bytes, xxHash128 fallback) — 60-100x faster than SHA-256
    - Batch embedding with debouncing, parallel I/O
  - **Phase B: Multi-Format Support** — done
    - Extractor registry with graceful degradation (optional deps)
    - Document extractors: PDF, DOCX, PPTX, XLSX, EPUB, RTF
    - Media metadata extractors: EXIF (images), audio tags, video metadata
    - 3-tier processing: text-native → rich documents → media metadata
  - **Phase C: Smart Chunking** — done
    - Chunker registry with extension → chunker mapping
    - Tree-sitter AST-aware code chunking (functions/classes as chunks)
    - Section-aware Markdown/RST chunking, fixed-size fallback
  - **Phase D: Search & UX** — done
    - 3-signal RRF hybrid search (vector + FTS5 keyword + path matching) with recency boost
    - Voyage, Ollama, OpenAI provider plugins
    - Daemon query server on localhost for instant CLI searches
    - Periodic re-scan with deleted file cleanup from vector DB
    - GPU auto-detection (CUDA/MPS/CPU) with adaptive batch sizing
    - 136 tests passing, ruff-clean
  - **Version:** 0.2.0 | **Tests:** 136 passing

## In Progress

- **Phase 2: Model Bus** — Planned, ready for implementation
  - Plan: `.planning/docs/phase2_model_bus_plan.md`
  - 5 sub-phases: A (Core Infrastructure) → B (Providers) → C (Cloud + CLI) → D (Resources) → E (SFS Migration)
  - asyncio Unix socket daemon, JSON-RPC 2.0, NDJSON streaming
  - Pluggable providers: Ollama, OpenAI, Anthropic, Voyage, local sentence-transformers
  - Resource-aware model loading (VRAM/RAM checks, idle eviction)
  - Target: 145+ tests

## Future

- **Phase 2: Model Bus** — Unified AI runtime, pluggable providers via Unix socket
- **Phase 3: SystemAgentd** — Agent orchestration as systemd services
- **Phase 4: Agentic Shell (aish)** — Intent-driven shell with NL commands
- **Phase 5: Desktop Environment** — AI-managed context workspaces
- **Phase 6: Arch Linux ISO** — Bootable YAAOS distribution

## Key Files

| What | Where |
|------|-------|
| SFS source | `src/yaaos-sfs/src/yaaos_sfs/` |
| SFS v2 plan | `.planning/docs/sfs_v2_plan.md` |
| Model Bus plan | `.planning/docs/phase2_model_bus_plan.md` |
| Architecture | `.planning/docs/architecture.md` |
| Roadmap | `.planning/docs/roadmap.md` |
| Planning index | `.planning/INDEX.md` |

## Setup

- Always run in linux env or wsl.
- Always use uv for python package management.
