# YAAOS Planning

## Docs (Approved)

Finalized documents driving development.

- [architecture.md](docs/architecture.md) — System architecture: 6 layers from Base OS to Desktop Environment
- [tech_stack.md](docs/tech_stack.md) — Every technology choice with rationale (Arch, Python/Rust, sqlite-vec, Ollama, etc.)
- [mvp_scope.md](docs/mvp_scope.md) — MVP scope: Semantic File System (project structure, DB schema, algorithms, success criteria)
- [roadmap.md](docs/roadmap.md) — Full 6-phase roadmap with milestones and dependencies
- [sfs_v2_plan.md](docs/sfs_v2_plan.md) — SFS v2 plan: production-ready with filtering, multi-format, smart chunking, scale

## Drafts (Brainstorming)

Early brainstorming documents. Kept for historical context.

- [01_first_draft_ai_linux_distro.md](drafts/01_first_draft_ai_linux_distro.md) — Original vision: Semantic FS, Agents as Daemons, Agentic Shell, DevX
- [02_target_audiences_and_use_cases.md](drafts/02_target_audiences_and_use_cases.md) — Target audiences (devs, data scientists, sysadmins) and 5 concrete use cases

## Project Status

**Current Phase:** Phase 1.5 — SFS v2 (Production-Ready)
**Phase 1 (MVP):** Complete (8/9 milestones, tests remaining)
**Phase 1.5 (v2):** Planning complete, implementation pending
**Code Location:** `src/yaaos-sfs/`

### Quick Reference

```
.planning/
├── INDEX.md              ← you are here
├── docs/
│   ├── architecture.md   ← system design
│   ├── tech_stack.md     ← technology choices
│   ├── mvp_scope.md      ← Phase 1 MVP scope
│   ├── roadmap.md        ← the full 6-phase plan
│   └── sfs_v2_plan.md    ← SFS v2 production plan
└── drafts/
    ├── 01_*.md           ← original brainstorm
    └── 02_*.md           ← audiences & use cases
```
