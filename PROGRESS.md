# YAAOS Progress

## Done

- **SFS MVP (Phase 1)** — Semantic File System with working daemon + CLI search
  - SQLite + sqlite-vec DB with vector + FTS5 hybrid search (RRF fusion)
  - File watcher daemon (watchdog) auto-indexes ~/semantic/
  - `yaaos-find` CLI with rich output, --type filter, --status
  - Local embedding (all-MiniLM-L6-v2) + OpenAI provider plugin
  - PDF extraction via PyMuPDF
  - 10 file types supported (txt, md, py, js, ts, json, yaml, sh, rs, pdf)

- **Planning & Architecture** — Full system design documented
  - 6-layer architecture (Base OS → Desktop Environment)
  - Tech stack decisions (Arch Linux, Python→Rust, sqlite-vec, Ollama)
  - 6-phase roadmap with milestones

## In Progress

- **SFS v2 (Phase 1.5)** — Production-ready upgrade | Planning complete, implementation pending
  - **Phase A: Core Infrastructure** — next up
    - A1. File filtering (skip node_modules/.git/build, .gitignore support)
    - A2. Stat-based change detection (replace SHA-256 with stat+xxHash, 60-100x faster)
    - A3. Batch embedding + debouncing (32-chunk batches, 200ms debounce, parallel I/O)
  - **Phase B: Multi-Format Support**
    - B1. Extractor registry (plugin system)
    - B2. Document extractors (DOCX, PPTX, XLSX, EPUB, RTF)
    - B3. Media metadata extractors (EXIF, audio tags, video metadata)
  - **Phase C: Smart Chunking**
    - C1. Chunker registry
    - C2. Tree-sitter AST-aware code chunking
    - C3. Section-aware document chunking
  - **Phase D: Search & UX**
    - D1. Path matching + recency boost in search
    - D2. Voyage/Ollama provider plugins
    - D3. Richer CLI output + per-type status
    - D4. Tests

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
| Architecture | `.planning/docs/architecture.md` |
| Roadmap | `.planning/docs/roadmap.md` |
| Planning index | `.planning/INDEX.md` |
