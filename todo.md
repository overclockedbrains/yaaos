# 🚀 YAAOS - The Ultimate Dev Distro

**Goal:** Make YAAOS the default Linux distro for developers. Zero-friction remote dev, native AI agents, and a semantic file system out of the box.

### 🔌 Frictionless Connectivity
- [ ] **Instant VSCode SSH:** Zero config remote access. Connect instantly and start coding without weird key setups.
- [ ] **Auto Dev Environments (`.ash`):** Just run a `.ash` script (or tell the agent) and it provisions your entire stack automatically (Node, Android, Rust, whatever you need).

### 🤖 True Agentic Integration
- [ ] **Talk to your IDE:** The native OS agent bridges directly to VSCode and Android Studio. "Setup an Android app and open it." -> Done.
- [ ] **SystemAgentd (AI Daemons):** Background agents replacing systemd. They monitor resources, catch crashes, and fix missing dependencies on the fly without breaking your flow.
- [ ] **Agentic Shell (`ash`):** A shell that understands your intent (`compress this project and send to staging`).

### 🧠 Semantic File System (SFS)
- [ ] **Query by Meaning:** Save a file, and it's instantly embedded locally. Search by meaning ("Find that login bug Aman talked about Tuesday"), not just filename.
- [ ] **Auto Organization:** Tell the folder agent "Keep my Downloads sorted by project" and it does it natively.

### 📊 Fingertip Management
- [ ] **Dashboard/CLI:** Monitor all your agent daemons, containers, and system resources instantly from one place.

- [x] Phase 2 (Model Bus) — implemented, verified, manually tested end-to-end (2026-03-15)
- [ ] Phase 3 (SystemAgentd) — next up

### 🎯 North-Star Test (Post Phase 4)

> "Setup a basic Android app repo with latest tech stack, install whatever is needed, build it, run it on an emulator, and verify it launches."
>
> Single prompt. No GUI. Agent uses CLI tools (sdkmanager, gradle, adb, emulator -no-window).
> Verifies launch via `adb shell dumpsys activity`. See [roadmap](/.planning/docs/roadmap.md) for full spec.

### 📝 Personal TODOs

- [ ] Github ci needs upgrade - currently only has sfs
- [ ] Can YAAOS take current screen details and use it? (Answer: yes via D-Bus, /proc, headless browser CDP, adb uiautomator — no vision model needed for most cases)
