# YAAOS Planning

## Docs (Approved)

Finalized documents driving development.

- [architecture.md](docs/architecture.md) — System architecture: 6 layers from Base OS to Desktop Environment
- [tech_stack.md](docs/tech_stack.md) — Every technology choice with rationale (Arch, Python/Rust, sqlite-vec, Ollama, etc.)
- [mvp_scope.md](docs/mvp_scope.md) — MVP scope: Semantic File System (project structure, DB schema, algorithms, success criteria)
- [roadmap.md](docs/roadmap.md) — Full 6-phase roadmap with milestones and dependencies
- [sfs_v2_plan.md](docs/sfs_v2_plan.md) — SFS v2 plan: production-ready with filtering, multi-format, smart chunking, scale
- [phase2_model_bus_plan.md](docs/phase2_model_bus_plan.md) — Phase 2: Model Bus — unified AI runtime, pluggable providers, resource management, streaming
- [phase3_systemagentd_plan.md](docs/phase3_systemagentd_plan.md) — Phase 3: SystemAgentd — agent orchestration, OTP supervision, Tool Registry
- [dev_commands.md](docs/dev_commands.md) — Dev commands reference for all YAAOS components

## Drafts (Brainstorming)

Early brainstorming documents. Kept for historical context.

- [01_first_draft_ai_linux_distro.md](drafts/01_first_draft_ai_linux_distro.md) — Original vision: Semantic FS, Agents as Daemons, Agentic Shell, DevX
- [02_target_audiences_and_use_cases.md](drafts/02_target_audiences_and_use_cases.md) — Target audiences (devs, data scientists, sysadmins) and 5 concrete use cases

## Project Status

**Current Phase:** Phase 4 — Agentic Shell (next)
**Phase 1 (SFS MVP):** Complete
**Phase 1.5 (SFS v2):** Complete (2026-03-15) — v0.2.0, 136 tests passing
**Phase 2 (Model Bus):** Complete (2026-03-15) — v0.1.0, 173 tests passing
**Phase 3 (SystemAgentd):** Complete (2026-03-16) — v0.1.0
**Code Locations:** `src/yaaos-sfs/`, `src/yaaos-modelbus/`, `src/yaaos-agentd/`

### Quick Reference

```
.planning/
├── INDEX.md              ← you are here
├── docs/
│   ├── architecture.md   ← system design
│   ├── tech_stack.md     ← technology choices
│   ├── mvp_scope.md      ← Phase 1 MVP scope
│   ├── roadmap.md        ← the full 6-phase plan
│   ├── sfs_v2_plan.md    ← SFS v2 production plan
│   ├── phase2_model_bus_plan.md ← Phase 2 Model Bus plan
│   ├── phase3_systemagentd_plan.md ← Phase 3 SystemAgentd plan
│   └── dev_commands.md   ← dev commands reference
├── phases/
│   └── 03-systemagentd/  ← Phase 3 research docs
└── drafts/
    ├── 01_*.md           ← original brainstorm
    └── 02_*.md           ← audiences & use cases
```
