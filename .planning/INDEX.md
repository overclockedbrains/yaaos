# YAAOS Planning

## Docs (Approved)

Finalized documents driving development.

- [architecture.md](docs/architecture.md) — System architecture: 6 layers from Base OS to Desktop Environment
- [tech_stack.md](docs/tech_stack.md) — Every technology choice with rationale (Arch, Python/Rust, sqlite-vec, Ollama, etc.)
- [mvp_scope.md](docs/mvp_scope.md) — MVP scope: Semantic File System (project structure, DB schema, algorithms, success criteria)
- [roadmap.md](docs/roadmap.md) — Full 6-phase roadmap with milestones and dependencies

## Drafts (Brainstorming)

Early brainstorming documents. Kept for historical context.

- [01_first_draft_ai_linux_distro.md](drafts/01_first_draft_ai_linux_distro.md) — Original vision: Semantic FS, Agents as Daemons, Agentic Shell, DevX
- [02_target_audiences_and_use_cases.md](drafts/02_target_audiences_and_use_cases.md) — Target audiences (devs, data scientists, sysadmins) and 5 concrete use cases

## Project Status

**Current Phase:** Phase 1 — Semantic File System (MVP)
**Phase 1 Progress:** 8/9 milestones complete (tests & polish remaining)
**Code Location:** `src/yaaos-sfs/`

### Quick Reference

```
.planning/
├── INDEX.md              ← you are here
├── docs/
│   ├── architecture.md   ← system design
│   ├── tech_stack.md     ← technology choices
│   ├── mvp_scope.md      ← what we're building now
│   └── roadmap.md        ← the full 6-phase plan
└── drafts/
    ├── 01_*.md           ← original brainstorm
    └── 02_*.md           ← audiences & use cases
```
