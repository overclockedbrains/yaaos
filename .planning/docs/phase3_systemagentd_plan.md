# Phase 3: SystemAgentd — Agent Orchestration Layer

**Status:** Planning
**Created:** 2026-03-15
**Depends On:** Phase 2 (Model Bus) — Complete, Phase 1.5 (SFS v2) — Complete
**Consumed By:** Phase 4 (Agentic Shell), Phase 5 (Desktop Environment)

---

## 1. Problem Definition

YAAOS has a working semantic filesystem (SFS) and a unified AI runtime (Model Bus). But these are passive infrastructure — they wait for requests. There is no component that **actively monitors the system, reasons about what's happening, and takes corrective action**.

SystemAgentd is the daemon that turns YAAOS from "a Linux distro with AI tools" into "an AI-native operating system." It is the orchestration layer that manages long-running AI agents as first-class OS services — each one observing a domain (logs, crashes, resources, network), reasoning via Model Bus, and acting through a pluggable Tool Registry.

### Goals

1. **Supervisor daemon** (`systemagentd`) that manages agent lifecycle with OTP-grade fault tolerance — supervision trees, restart strategies, crash isolation.
2. **Agent service framework** — a base class and systemd template unit so writing a new agent is as easy as implementing `observe() → reason() → act()`.
3. **Tool Registry** — a pluggable, MCP-inspired registry of CLI tools (adb, gradle, docker, git, pacman) that agents can discover and invoke with structured input/output.
4. **Four built-in agents** — Log-Agent, Crash-Agent, Resource-Agent, Net-Agent — proving the framework works on real problems.
5. **Agent Bus API** — Unix socket API + `systemagentctl` CLI for querying status, starting/stopping agents, and inspecting tool invocations.
6. **SFS migration** — re-host the existing SFS daemon as a managed agent under SystemAgentd.

### Non-Goals (Deferred)

- **Inter-agent collaboration / delegation** — Agents operate independently in Phase 3. Multi-agent coordination (agent A asks agent B for help) is Phase 4+ when the Agentic Shell needs it.
- **User-facing natural language interface** — Agents are system services, not chatbots. NL interaction comes via `aish` in Phase 4.
- **GUI dashboard** — Phase 5 (Desktop Environment).
- **Rust rewrite** — Python MVP first, Rust when battle-tested.
- **eBPF-based monitoring** — Net-Agent and Resource-Agent use userspace tools (psutil, /proc, conntrack) in Phase 3. eBPF is a future optimization.
- **Remote/distributed agents** — All agents run on the local machine.
- **Agent marketplace / third-party agents** — First-party agents only in Phase 3.

---

## 2. Architecture: Foundational Principles

### 2.1 Erlang/OTP Supervision Trees (adapted to Python + systemd)

**Source:** [Erlang OTP Supervisor Design Principles](https://www.erlang.org/doc/system/sup_princ.html), [Learn You Some Erlang — Supervisors](https://learnyousomeerlang.com/supervisors)

OTP supervisors are the gold standard for fault-tolerant process management. Key patterns we adopt:

| OTP Concept | SystemAgentd Implementation |
|---|---|
| **Supervisor process** | `systemagentd` daemon — monitors all agents |
| **Worker process** | Each agent (Log-Agent, Crash-Agent, etc.) |
| **one_for_one restart** | Default: if one agent crashes, only that agent restarts. Other agents are unaffected. |
| **rest_for_one restart** | For dependent agents: if agent A crashes, restart A and all agents started after A. |
| **Restart intensity** | `max_restarts` / `max_seconds` — if an agent crashes more than N times in M seconds, stop trying and mark it `degraded`. Prevents restart storms. |
| **Permanent / transient / temporary** | Agent restart policy: `permanent` (always restart), `transient` (restart only on abnormal exit), `temporary` (never restart, one-shot tasks). |
| **"Let it crash"** | Agents do NOT wrap every error in try/except. If an agent hits an unrecoverable state, it crashes. The supervisor restarts it with clean state. This is simpler and more reliable than defensive error handling. |

**Why this matters:** Naive process supervisors just restart on crash. OTP's insight is that restart storms are worse than crashes — the intensity limit (`max_restarts=3, max_seconds=60`) circuit-breaks a repeatedly failing agent before it destabilizes the system.

```
                    systemagentd (supervisor)
                    restart: one_for_one
                    max_restarts: 5 / 60s
                    ┌──────────┬──────────┬──────────┬──────────┐
                    │          │          │          │          │
                Log-Agent  Crash-Agent  Res-Agent  Net-Agent  FS-Agent
                permanent  transient    permanent  permanent  permanent
```

### 2.2 Kubernetes Reconciliation Loop (Level-Triggered)

**Source:** [Level Triggering and Reconciliation in Kubernetes](https://hackernoon.com/level-triggering-and-reconciliation-in-kubernetes-1f17fe30333d)

Every agent operates on a **reconciliation loop**, not an event-driven callback model:

```
while running:
    desired = load_desired_state()    # "what should be true"
    actual  = observe_current_state() # "what is true right now"
    diff    = compute_diff(desired, actual)
    if diff:
        actions = plan_actions(diff)  # reason via Model Bus
        execute(actions)              # act via Tool Registry
    await sleep(reconcile_interval)
```

**Why level-triggered over edge-triggered:**
- **Self-healing:** If an event is missed (network glitch, daemon restart), the next reconcile loop catches the drift anyway. No events are "lost."
- **Idempotent:** Running reconcile twice produces the same result. No duplicate actions.
- **Debuggable:** You can always inspect "desired state" vs "actual state" — the diff tells you exactly why an agent acted.
- **Batch-friendly:** If 100 log entries arrive in 1 second, the agent sees them as one batch at the next reconcile, not 100 individual events.

Each agent defines its own `reconcile_interval` (default: 30s for most agents, 5s for Log-Agent, on-demand for Crash-Agent via socket activation).

### 2.3 Agent Execution Model: Observe → Reason → Act

Inspired by the [ReAct pattern](https://www.promptingguide.ai/techniques/react) (Reason + Act), but adapted for long-running system daemons:

```
┌─────────────────────────────────────────────────────┐
│                    Agent Loop                       │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│  │ OBSERVE  │───▶│  REASON  │───▶│   ACT    │       │
│  │          │    │          │    │          │       │
│  │ Read     │    │ Analyze  │    │ Invoke   │       │
│  │ system   │    │ via LLM  │    │ tools    │       │
│  │ state    │    │ or rules │    │ from     │       │
│  │          │    │          │    │ registry │       │
│  └──────────┘    └──────────┘    └──────────┘       │
│       ▲                               │             │
│       └───────────────────────────────┘             │
│              (reconciliation loop)                  │
└─────────────────────────────────────────────────────┘
```

**Critical design choice: Not every observation requires LLM reasoning.** Agents use a tiered approach:

1. **Rule-based fast path** — Simple heuristics (CPU > 90% for 60s → alert). No LLM call. Sub-millisecond.
2. **Statistical anomaly detection** — Deviation from moving average (log rate spike, memory trend). No LLM call. Milliseconds.
3. **LLM reasoning** — Complex analysis (interpret a stack trace, correlate log patterns, suggest a fix). Via Model Bus. Seconds.

This prevents agents from burning GPU/API credits on routine observations. The LLM is a scalpel, not a sledgehammer.

### 2.4 Tool Registry: MCP-Inspired, Unix-Native

**Sources:** [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25), [Anthropic Sandbox Runtime](https://github.com/anthropic-experimental/sandbox-runtime)

Tools are CLI programs wrapped with structured metadata so agents can discover and invoke them safely:

```toml
# /etc/yaaos/tools.d/docker.toml
[tool]
name = "docker"
description = "Container runtime for building and running applications"
binary = "/usr/bin/docker"
version_cmd = "docker --version"

[tool.capabilities]
actions = ["ps", "run", "stop", "build", "logs", "inspect"]

[tool.schema.ps]
description = "List running containers"
args = ["ps", "--format", "json"]
output_format = "json"

[tool.schema.run]
description = "Run a container"
parameters = [
    { name = "image", type = "string", required = true, description = "Container image to run" },
    { name = "detach", type = "boolean", default = true, description = "Run in background" },
    { name = "ports", type = "array", items = "string", description = "Port mappings (host:container)" },
]
args_template = "run {%if detach%}-d{%endif%} {%for p in ports%}-p {{p}} {%endfor%} {{image}}"
output_format = "text"

[tool.permissions]
requires_root = false
network_access = true
filesystem_write = true

[tool.sandbox]
enabled = true
allowed_paths = ["/var/run/docker.sock", "/tmp"]
network = true
```

**Key design decisions:**

1. **TOML manifests** — Each tool is a `.toml` file in `/etc/yaaos/tools.d/` or `~/.config/yaaos/tools.d/`. Drop a file, tool is discovered. Same pattern as systemd drop-in dirs.
2. **JSON Schema for parameters** — Matches MCP's `inputSchema` convention. Agents know exactly what arguments a tool accepts.
3. **args_template** — Jinja2-style template that converts structured parameters into CLI arguments. This is the bridge between "AI-friendly structured input" and "Unix CLI."
4. **Output format hints** — `json`, `text`, `table`, `exitcode`. Helps agents parse tool output without guessing.
5. **Permission declarations** — Agents check permissions before invoking. Root-requiring tools need explicit opt-in.
6. **Optional sandboxing** — Via bubblewrap (bwrap) for filesystem/network isolation. Based on [Anthropic's sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) patterns. Lightweight: Linux namespaces, no container runtime needed.

**Discovery flow:**
```
Agent wants to "list running containers"
  → queries Tool Registry: find_tools(capability="container.list")
  → Registry returns: docker.ps (with schema)
  → Agent constructs: {"image": "...", "detach": true}
  → Registry validates against JSON Schema
  → Registry executes: docker ps --format json
  → Registry returns structured output to agent
```

### 2.5 Inter-Component Communication

Matching the existing YAAOS pattern (Model Bus):

| Socket | Owner | Purpose |
|--------|-------|---------|
| `/run/yaaos/modelbus.sock` | Model Bus | AI inference (existing) |
| `/run/yaaos/sfs.sock` | SFS | Semantic search (existing) |
| `/run/yaaos/agentbus.sock` | SystemAgentd | Agent management API (new) |

**Protocol:** JSON-RPC 2.0 over NDJSON on Unix sockets — identical to Model Bus. This means the existing `JsonRpcServer` class from `yaaos-modelbus` can be extracted into a shared library or copied as a pattern.

**Why not D-Bus:** D-Bus is powerful but complex (type system, introspection, bus ownership). Our agents need simple request/reply and event streaming. Unix sockets with JSON-RPC are simpler, debuggable with `socat`, and consistent with Model Bus. D-Bus integration is optional for desktop events in Phase 5.

---

## 3. Component Breakdown

### 3.1 SystemAgentd Supervisor Daemon

The core daemon. Responsibilities:

- **Agent lifecycle management** — Start, stop, restart, health-check agents
- **Supervision tree** — OTP-style restart strategies and intensity limits
- **Reconciliation loop** — Periodically verify all agents match desired state
- **Agent Bus API server** — Unix socket API for external queries
- **systemd integration** — Type=notify, watchdog, structured journal logging
- **Configuration** — TOML config for agent definitions, restart policies, resource limits

```python
# Simplified supervisor loop (reconciliation-based)
class Supervisor:
    """OTP-inspired supervisor with reconciliation loop."""

    def __init__(self, config: SupervisorConfig):
        self.desired_agents: dict[str, AgentSpec] = {}  # from config
        self.running_agents: dict[str, AgentHandle] = {}
        self.restart_tracker: dict[str, RestartHistory] = {}

    async def reconcile(self):
        """Level-triggered: compare desired vs actual, correct drift."""
        for name, spec in self.desired_agents.items():
            handle = self.running_agents.get(name)

            if handle is None:
                # Agent should be running but isn't → start it
                await self.start_agent(name, spec)

            elif not handle.is_healthy():
                # Agent exists but unhealthy → check restart policy
                if self.can_restart(name):
                    await self.restart_agent(name, spec)
                else:
                    logger.error("agent.restart_limit_exceeded",
                                 agent=name,
                                 status="degraded")

        # Remove agents no longer in desired state
        for name in list(self.running_agents):
            if name not in self.desired_agents:
                await self.stop_agent(name)
```

### 3.2 Agent Base Class

Every agent implements a common interface:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentSpec:
    """Agent definition from config."""
    name: str
    module: str                        # "yaaos_agentd.agents.log_agent"
    restart_policy: str = "permanent"  # permanent | transient | temporary
    reconcile_interval_sec: float = 30.0
    resource_limits: dict = field(default_factory=dict)  # CPUQuota, MemoryMax
    config: dict = field(default_factory=dict)            # agent-specific config

class BaseAgent(ABC):
    """Base class for all YAAOS agents."""

    def __init__(self, spec: AgentSpec, model_bus, tool_registry, sfs_client):
        self.spec = spec
        self.model_bus = model_bus          # ModelBusClient for AI inference
        self.tool_registry = tool_registry  # ToolRegistry for CLI tools
        self.sfs = sfs_client              # SFS client for semantic search
        self._state: dict[str, Any] = {}   # agent working memory

    @abstractmethod
    async def observe(self) -> dict:
        """Gather current system state relevant to this agent.
        Returns an observation dict that will be passed to reason()."""
        ...

    @abstractmethod
    async def reason(self, observation: dict) -> list[Action]:
        """Analyze the observation and decide what actions to take.
        May call Model Bus for LLM reasoning on complex observations.
        Returns a list of actions (possibly empty)."""
        ...

    @abstractmethod
    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Execute planned actions via Tool Registry.
        Returns results for logging and state update."""
        ...

    async def run_cycle(self):
        """Single observe → reason → act cycle."""
        observation = await self.observe()
        actions = await self.reason(observation)
        if actions:
            results = await self.act(actions)
            await self.report(observation, actions, results)

    async def report(self, observation, actions, results):
        """Log cycle results to journal with structured fields."""
        ...

    # Lifecycle hooks
    async def on_start(self): ...
    async def on_stop(self): ...
    async def on_reload(self, new_config: dict): ...

    # State persistence
    async def save_state(self): ...
    async def load_state(self): ...
```

### 3.3 systemd Integration

**Template unit for agents:**

```ini
# systemd/systemagentd-agent@.service
[Unit]
Description=YAAOS Agent: %i
After=systemagentd.service
BindsTo=systemagentd.service
Documentation=https://github.com/Aman-Coding-Bamzii/yaaos

[Service]
Type=notify
ExecStart=/usr/bin/yaaos-agent %i
WatchdogSec=60

# Resource isolation via cgroups v2
Slice=yaaos-agents.slice
CPUQuota=10%
MemoryMax=512M
IOWeight=50

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/yaaos /var/lib/yaaos/agents/%i
PrivateTmp=true

# Restart policy (overridden per-agent via drop-ins)
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60

# Structured logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=yaaos-agent-%i
Environment=AGENT_NAME=%i PYTHONUNBUFFERED=1

[Install]
WantedBy=systemagentd.service
```

**Supervisor unit:**

```ini
# systemd/systemagentd.service
[Unit]
Description=YAAOS SystemAgentd — Agent Supervisor
Documentation=https://github.com/Aman-Coding-Bamzii/yaaos
After=yaaos-modelbus.service yaaos-sfs.service
Wants=yaaos-modelbus.service yaaos-sfs.service

[Service]
Type=notify
ExecStart=/usr/bin/systemagentd
ExecReload=/bin/kill -HUP $MAINPID
RuntimeDirectory=yaaos
RuntimeDirectoryMode=0755

KillMode=mixed
TimeoutStopSec=30
WatchdogSec=60
NotifyAccess=all

Restart=on-failure
RestartSec=5

# Supervisor gets more resources than individual agents
MemoryMax=1G
LimitNOFILE=4096

ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/yaaos /var/lib/yaaos
NoNewPrivileges=true
PrivateTmp=true

StandardOutput=journal
StandardError=journal
SyslogIdentifier=systemagentd
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**Socket activation for Crash-Agent:**

```ini
# systemd/systemagentd-agent-crash.socket
[Unit]
Description=YAAOS Crash-Agent Socket Activation

[Socket]
ListenStream=/run/yaaos/crash-agent.sock
Accept=no

[Install]
WantedBy=sockets.target
```

The Crash-Agent starts only when a core dump handler writes to the socket — no idle resource usage.

**Agent resource slice:**

```ini
# systemd/yaaos-agents.slice
[Unit]
Description=YAAOS Agent Resource Slice

[Slice]
# All agents combined get max 30% CPU and 2GB RAM
CPUQuota=30%
MemoryMax=2G
IOWeight=50
```

### 3.4 Tool Registry

```python
@dataclass
class ToolSchema:
    """JSON Schema for tool parameters — MCP-compatible."""
    name: str
    description: str
    parameters: dict           # JSON Schema object
    args_template: str         # Jinja2 template → CLI args
    output_format: str         # json | text | exitcode

@dataclass
class ToolDefinition:
    """A registered tool with all metadata."""
    name: str
    description: str
    binary: str
    capabilities: list[str]
    schemas: dict[str, ToolSchema]
    permissions: dict
    sandbox_config: dict | None

class ToolRegistry:
    """Discovers, validates, and invokes CLI tools for agents."""

    def __init__(self, tool_dirs: list[Path]):
        self._tools: dict[str, ToolDefinition] = {}
        self._load_tools(tool_dirs)

    def _load_tools(self, dirs: list[Path]):
        """Scan tool dirs for .toml manifests, validate, register."""
        for d in dirs:
            for toml_file in d.glob("*.toml"):
                tool = self._parse_manifest(toml_file)
                if self._validate_tool(tool):
                    self._tools[tool.name] = tool

    def find_tools(self, capability: str = None, name: str = None) -> list[ToolDefinition]:
        """Discovery: find tools by capability or name."""
        ...

    async def invoke(
        self,
        tool_name: str,
        action: str,
        params: dict,
        *,
        timeout: float = 30.0,
        sandbox: bool = True,
    ) -> ToolResult:
        """Invoke a tool action with validated parameters.

        1. Validate params against JSON Schema
        2. Render args_template with params
        3. Execute binary with args (optionally in bwrap sandbox)
        4. Parse output according to output_format
        5. Return structured result
        """
        tool = self._tools[tool_name]
        schema = tool.schemas[action]

        # Validate
        self._validate_params(params, schema.parameters)

        # Build command
        args = self._render_args(schema.args_template, params)
        cmd = [tool.binary] + args

        # Execute (with optional sandbox)
        if sandbox and tool.sandbox_config:
            cmd = self._wrap_with_bwrap(cmd, tool.sandbox_config)

        result = await self._exec(cmd, timeout=timeout)
        return self._parse_output(result, schema.output_format)
```

### 3.5 Agent Bus API

JSON-RPC 2.0 methods on `/run/yaaos/agentbus.sock`:

| Method | Description |
|--------|-------------|
| `agents.list` | List all agents with status, uptime, last cycle |
| `agents.status` | Detailed status for a specific agent |
| `agents.start` | Start a stopped/failed agent |
| `agents.stop` | Gracefully stop an agent |
| `agents.restart` | Restart an agent |
| `agents.logs` | Recent journal entries for an agent |
| `tools.list` | List all registered tools |
| `tools.schema` | Get JSON Schema for a tool action |
| `tools.invoke` | Manually invoke a tool (admin/debug) |
| `health` | Supervisor health + resource usage |
| `config.reload` | Hot-reload agent configuration |

### 3.6 Configuration

```toml
# ~/.config/yaaos/agentd.toml

[supervisor]
socket_path = "/run/yaaos/agentbus.sock"
reconcile_interval_sec = 10
max_restarts = 5
max_restart_window_sec = 60
log_level = "info"

[tool_dirs]
system = "/etc/yaaos/tools.d"
user = "~/.config/yaaos/tools.d"

[agents.log]
enabled = true
module = "yaaos_agentd.agents.log_agent"
restart_policy = "permanent"
reconcile_interval_sec = 5
config.units = ["yaaos-modelbus", "yaaos-sfs", "sshd", "docker"]
config.anomaly_threshold = 2.0  # std deviations
config.batch_window_sec = 10

[agents.crash]
enabled = true
module = "yaaos_agentd.agents.crash_agent"
restart_policy = "transient"
socket_activated = true
config.coredump_dir = "/var/lib/systemd/coredump"
config.max_analysis_tokens = 4096

[agents.resource]
enabled = true
module = "yaaos_agentd.agents.resource_agent"
restart_policy = "permanent"
reconcile_interval_sec = 15
config.cpu_warn_pct = 85
config.memory_warn_pct = 80
config.prediction_window_sec = 300

[agents.net]
enabled = true
module = "yaaos_agentd.agents.net_agent"
restart_policy = "permanent"
reconcile_interval_sec = 30
config.watch_interfaces = ["eth0", "wlan0"]
config.connection_rate_limit = 100  # per minute

[agents.fs]
enabled = true
module = "yaaos_agentd.agents.fs_agent"
restart_policy = "permanent"
config.delegate_to = "yaaos-sfs.service"  # wraps existing SFS daemon
```

---

## 4. Built-In Agents

### 4.1 Log-Agent — Real-Time journald Analysis

**Purpose:** Stream system logs, detect anomalies, surface actionable insights.

**Observe:**
- Connect to journald via `systemd.journal.Reader` (Python systemd bindings)
- Filter by configured unit names
- Buffer entries in batches (10s window)

**Reason (tiered):**
1. **Rule-based:** Known error patterns → immediate alert (e.g., OOM killed, segfault, connection refused)
2. **Statistical:** Log rate deviation from 5-minute moving average → flag spike
3. **LLM (on anomaly only):** Send batch of anomalous log entries to Model Bus → "Analyze these log entries. What's the root cause? Suggest a fix."

**Act:**
- Write structured alert to journal (PRIORITY=3, AGENT_NAME=log, ALERT_TYPE=anomaly)
- Optionally invoke tools to gather more context (e.g., `systemctl status <unit>`)

**State:**
- Moving average of log rates per unit (in-memory, rebuilt on restart)
- Known error pattern database (static, shipped with agent)

### 4.2 Crash-Agent — Core Dump Analysis (Socket-Activated)

**Purpose:** When a process crashes and produces a core dump, analyze it and suggest a fix.

**Activation:** Socket-activated — starts only when triggered by systemd-coredump or a manual request. Exits when done (transient restart policy).

**Observe:**
- Read core dump metadata from `coredumpctl --json`
- Extract backtrace via `coredumpctl debug --debugger-arguments="-batch -ex bt"`
- If debug symbols available, extract source context around crash point

**Reason (always LLM):**
- Send to Model Bus: executable name, signal, backtrace, source context
- Prompt: "Analyze this crash. Explain the likely cause and suggest a fix."

**Act:**
- Write analysis to journal with structured fields
- Optionally query SFS: "find related source files" → include in analysis
- Store analysis in `/var/lib/yaaos/agents/crash/analyses/`

### 4.3 Resource-Agent — CPU/RAM/GPU Prediction

**Purpose:** Monitor system resources, predict exhaustion, proactively prevent OOM.

**Observe:**
- `psutil.cpu_percent(interval=1)` — per-core CPU
- `psutil.virtual_memory()` — RAM usage, available, pressure
- `pynvml` — GPU utilization, VRAM, temperature
- `/sys/fs/cgroup/` — per-agent cgroup stats (delegated from systemd)

**Reason (tiered):**
1. **Threshold-based:** CPU > 85% sustained 60s → warn. Memory available < 10% → critical.
2. **Trend-based:** Exponential weighted moving average of memory usage over 5 min. If trend predicts exhaustion within `prediction_window_sec` → proactive alert.
3. **LLM (on critical only):** "System memory at 92%, trending to OOM in ~3 minutes. Top consumers: [process list]. Suggest actions."

**Act:**
- Alert to journal
- If `proactive_mode` enabled: suggest cgroup adjustments (not auto-apply in Phase 3 — safety)
- Report to Agent Bus API for dashboard consumption

### 4.4 Net-Agent — Network Anomaly Detection

**Purpose:** Detect unusual network activity on a developer workstation.

**Observe:**
- Parse `/proc/net/tcp`, `/proc/net/tcp6` for connection state
- Track connection rate (new connections per minute)
- Monitor DNS queries via `/var/log/dnsmasq.log` or `resolvectl monitor`
- Check for unusual listening ports

**Reason (tiered):**
1. **Rule-based:** New listening port detected → alert. Known malicious port → critical.
2. **Statistical:** Connection rate exceeds 2x normal → flag.
3. **LLM (on anomaly only):** "Process X (PID Y) opened 47 outbound connections to IP Z in 1 minute. Is this normal developer activity or suspicious?"

**Act:**
- Alert to journal with connection details
- Optionally query SFS: "find config files related to [process name]"

---

## 5. State Persistence

**Source:** [LangGraph Checkpointing](https://docs.langchain.com/oss/python/langgraph/persistence)

Agents need to survive restarts without losing context. Strategy:

| State Type | Storage | Rebuilt on Restart? |
|---|---|---|
| **Working memory** (current observation, in-flight actions) | In-memory | Yes — lost on crash, rebuilt at next cycle |
| **Statistical accumulators** (moving averages, baselines) | SQLite per-agent | No — loaded from DB |
| **Analysis history** (crash reports, anomaly logs) | Filesystem (`/var/lib/yaaos/agents/<name>/`) | No — persisted |
| **Agent configuration** | TOML config file | No — read on start |

```
/var/lib/yaaos/agents/
├── log/
│   ├── state.db          # SQLite: moving averages, log baselines
│   └── anomalies/        # recent anomaly reports
├── crash/
│   └── analyses/         # crash analysis reports
├── resource/
│   ├── state.db          # SQLite: resource trend history
│   └── predictions/      # prediction logs
└── net/
    ├── state.db          # SQLite: connection baselines
    └── alerts/           # alert history
```

Each agent's SQLite database is independent — no shared state between agents. This is intentional: agent isolation means one corrupt database doesn't affect others.

---

## 6. Project Structure

```
src/yaaos-agentd/
├── pyproject.toml
├── src/yaaos_agentd/
│   ├── __init__.py
│   ├── types.py              # AgentSpec, Action, ActionResult, ToolResult, etc.
│   ├── errors.py             # AgentError, ToolError, SupervisorError
│   ├── config.py             # TOML config loading (matches modelbus pattern)
│   │
│   ├── supervisor.py         # OTP-style supervisor with reconciliation loop
│   ├── agent_base.py         # BaseAgent ABC (observe/reason/act)
│   ├── agent_runner.py       # Runs a single agent process (sd_notify, signal handling)
│   │
│   ├── server.py             # Agent Bus API (JSON-RPC over Unix socket)
│   ├── client.py             # Python SDK for Agent Bus (sync + async)
│   ├── cli.py                # `systemagentctl` CLI
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py       # ToolRegistry: discover, validate, invoke
│   │   ├── sandbox.py        # bubblewrap integration for sandboxed execution
│   │   └── manifests/        # Built-in tool manifests (.toml)
│   │       ├── docker.toml
│   │       ├── git.toml
│   │       ├── pacman.toml
│   │       ├── systemctl.toml
│   │       ├── journalctl.toml
│   │       ├── coredumpctl.toml
│   │       ├── adb.toml
│   │       └── gradle.toml
│   │
│   └── agents/
│       ├── __init__.py
│       ├── log_agent.py      # Log-Agent: journald streaming + anomaly detection
│       ├── crash_agent.py    # Crash-Agent: core dump analysis
│       ├── resource_agent.py # Resource-Agent: CPU/RAM/GPU prediction
│       ├── net_agent.py      # Net-Agent: network anomaly detection
│       └── fs_agent.py       # FS-Agent: SFS daemon wrapper
│
├── systemd/
│   ├── systemagentd.service
│   ├── systemagentd-agent@.service
│   ├── systemagentd-agent-crash.socket
│   └── yaaos-agents.slice
│
├── tools.d/                  # Default tool manifests (installed to /etc/yaaos/tools.d/)
│   ├── docker.toml
│   ├── git.toml
│   └── ...
│
└── tests/
    ├── conftest.py
    ├── test_supervisor.py    # Restart strategies, intensity limits, reconciliation
    ├── test_agent_base.py    # Agent lifecycle, observe/reason/act
    ├── test_tool_registry.py # Tool discovery, validation, invocation
    ├── test_server.py        # Agent Bus API protocol
    ├── test_client.py        # SDK tests
    ├── test_config.py        # Config loading
    ├── agents/
    │   ├── test_log_agent.py
    │   ├── test_crash_agent.py
    │   ├── test_resource_agent.py
    │   └── test_net_agent.py
    └── tools/
        ├── test_sandbox.py
        └── test_manifest_parsing.py
```

---

## 7. Dependencies

```toml
[project]
name = "yaaos-agentd"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "yaaos-modelbus",              # Model Bus client SDK
    "httpx>=0.27",                 # HTTP client (reused from modelbus)
    "orjson>=3.10",                # Fast JSON (matches modelbus)
    "structlog>=24.0",             # Structured logging (matches modelbus)
    "click>=8.0",                  # CLI framework (matches SFS + modelbus)
    "rich>=13.0",                  # Terminal output (matches SFS + modelbus)
    "psutil>=5.9",                 # System resource monitoring
    "jinja2>=3.1",                 # args_template rendering for tools
    "jsonschema>=4.0",             # Runtime validation of tool inputs against JSON Schema
    "dbus-next>=0.2",              # Async D-Bus client for systemd unit management
    "tomli>=2.0;python_version<'3.11'",
]

[project.optional-dependencies]
nvidia = ["pynvml>=12.0"]         # NVIDIA GPU monitoring
systemd = ["sdnotify>=0.3"]       # systemd notify protocol
journal = ["systemd-python>=235"] # journald reader (for Log-Agent)
all = ["yaaos-agentd[nvidia,systemd,journal]"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.5",
    "pytest-timeout>=2.0",
]

[project.scripts]
systemagentd = "yaaos_agentd.supervisor:main"
yaaos-agent = "yaaos_agentd.agent_runner:main"
systemagentctl = "yaaos_agentd.cli:main"

[project.entry-points."yaaos.agentd.agents"]
log = "yaaos_agentd.agents.log_agent:LogAgent"
crash = "yaaos_agentd.agents.crash_agent:CrashAgent"
resource = "yaaos_agentd.agents.resource_agent:ResourceAgent"
net = "yaaos_agentd.agents.net_agent:NetAgent"
fs = "yaaos_agentd.agents.fs_agent:FsAgent"
```

---

## 8. Implementation Phases

### Phase A: Supervisor Core + Agent Framework (Foundation)

**Goal:** A working supervisor that starts, monitors, and restarts a dummy agent.

| Task | Deliverable |
|------|-------------|
| A1 | Project scaffolding (pyproject.toml, src layout, uv setup) |
| A2 | `types.py` — AgentSpec, Action, ActionResult, ToolResult, AgentStatus |
| A3 | `errors.py` — AgentError, SupervisorError, ToolError |
| A4 | `config.py` — TOML config loading with agent definitions |
| A5 | `agent_base.py` — BaseAgent ABC with observe/reason/act + lifecycle hooks |
| A6 | `agent_runner.py` — Single-agent process runner with sd_notify + signal handling |
| A7 | `supervisor.py` — OTP-style supervisor with reconciliation loop, restart strategies, intensity limits |
| A8 | Unit tests: config, types, agent lifecycle, supervisor restart logic |

**Success:** Supervisor starts a dummy agent, detects crash, restarts it. Intensity limit stops restart after 5 crashes in 60s.

### Phase B: Tool Registry (CLI Tool Abstraction)

**Goal:** Agents can discover and invoke CLI tools via structured schemas.

| Task | Deliverable |
|------|-------------|
| B1 | `tools/registry.py` — TOML manifest parsing, tool discovery, JSON Schema validation |
| B2 | `tools/sandbox.py` — bubblewrap wrapper for sandboxed execution |
| B3 | Built-in tool manifests: git, docker, systemctl, journalctl, coredumpctl, pacman |
| B4 | `args_template` rendering (Jinja2 → CLI args) |
| B5 | Output parsing (json, text, exitcode) |
| B6 | Unit tests: manifest parsing, schema validation, invocation, sandbox |

**Success:** `registry.invoke("docker", "ps", {})` returns parsed JSON of running containers.

### Phase C: Agent Bus API + CLI

**Goal:** External tools can query agent status and invoke tools.

| Task | Deliverable |
|------|-------------|
| C1 | `server.py` — Agent Bus JSON-RPC server (reuse model bus server pattern) |
| C2 | `client.py` — Agent Bus Python SDK (sync + async) |
| C3 | `cli.py` — `systemagentctl` CLI: status, list, start, stop, logs, tools |
| C4 | Agent health reporting (last cycle time, error count, state) |
| C5 | Unit tests: API protocol, client, CLI commands |

**Success:** `systemagentctl status` shows all agents. `systemagentctl tools list` shows registered tools.

### Phase D: Built-In Agents

**Goal:** Four real agents solving real problems.

| Task | Deliverable |
|------|-------------|
| D1 | Log-Agent — journald streaming, rule-based + statistical anomaly detection, LLM analysis |
| D2 | Crash-Agent — coredumpctl integration, backtrace extraction, LLM crash analysis |
| D3 | Resource-Agent — psutil + pynvml monitoring, trend prediction, OOM prevention alerts |
| D4 | Net-Agent — /proc/net parsing, connection tracking, anomaly detection |
| D5 | Agent-specific tests (mocked journal, mocked coredumps, mocked psutil) |

**Success:** Log-Agent detects a log spike and explains it. Crash-Agent analyzes a core dump and suggests a fix. Resource-Agent predicts memory exhaustion 3 minutes ahead.

### Phase E: systemd Integration + SFS Migration

**Goal:** Production-ready daemon with systemd units and SFS running as a managed agent.

| Task | Deliverable |
|------|-------------|
| E1 | systemd unit files: supervisor, agent template, crash socket, agent slice |
| E2 | sd_notify integration: READY=1, WATCHDOG=1, STATUS= |
| E3 | Structured journal logging with custom fields (AGENT_NAME=, AGENT_CYCLE=) |
| E4 | FS-Agent: wrapper that manages the existing SFS daemon as an agent |
| E5 | Graceful shutdown: SIGTERM → stop all agents → drain API connections → exit |
| E6 | Config hot-reload: SIGHUP → re-read config → start new agents, stop removed ones |
| E7 | Integration tests: full supervisor → agent → tool invocation flow |
| E8 | Additional tool manifests for Phase 4 prep: adb, gradle, sdkmanager |

**Success:** `systemctl start systemagentd` → all agents running. `systemctl status systemagentd-agent@log` shows healthy. SFS runs as FS-Agent.

---

## 9. Testing Strategy

| Layer | Type | Count Target |
|-------|------|-------------|
| Types + Config | Unit | ~15 |
| Supervisor (restart strategies, intensity, reconcile) | Unit | ~25 |
| Agent Base (lifecycle, observe/reason/act) | Unit | ~15 |
| Tool Registry (manifest, schema, invoke, sandbox) | Unit | ~25 |
| Agent Bus API (protocol, methods) | Unit | ~15 |
| Client SDK (sync + async) | Unit | ~10 |
| Log-Agent | Unit | ~15 |
| Crash-Agent | Unit | ~10 |
| Resource-Agent | Unit | ~15 |
| Net-Agent | Unit | ~10 |
| Full integration (supervisor → agents → tools) | Integration | ~15 |
| **Total** | | **~170+** |

Testing approach matches existing codebase:
- `pytest` + `pytest-asyncio`
- Agent tests use mocked system interfaces (mock journald, mock psutil, mock coredumpctl)
- Tool invocation tests use stub binaries
- Integration tests marked `@pytest.mark.integration`
- No real LLM calls in CI — mock Model Bus responses

---

## 10. CLI: `systemagentctl`

```bash
# Overview
$ systemagentctl status
SystemAgentd: healthy (uptime 2h 14m)
Agents: 5/5 running

  log       ✓ running  cycles: 1,247  last: 2s ago   errors: 0
  crash     ○ idle     socket-activated, waiting for core dumps
  resource  ✓ running  cycles: 532    last: 12s ago  errors: 0
  net       ✓ running  cycles: 267    last: 28s ago  errors: 0
  fs        ✓ running  cycles: 89     last: 45s ago  errors: 0

# Agent details
$ systemagentctl status log
Agent: log
  Status: running
  PID: 4821
  Uptime: 2h 14m
  Cycles: 1,247 (5s interval)
  Last observation: 42 log entries from 4 units
  Last action: "Flagged anomaly: sshd log rate 3.2x normal"
  Errors: 0
  Memory: 48 MB / 512 MB limit
  CPU: 1.2% / 10% limit

# List tools
$ systemagentctl tools list
docker      Container runtime          7 actions   /usr/bin/docker
git         Version control            12 actions  /usr/bin/git
systemctl   systemd control            6 actions   /usr/bin/systemctl
journalctl  Journal query              3 actions   /usr/bin/journalctl
coredumpctl Core dump management       2 actions   /usr/bin/coredumpctl
pacman      Package manager            4 actions   /usr/bin/pacman
adb         Android Debug Bridge       8 actions   /usr/bin/adb
gradle      Build automation           3 actions   /usr/bin/gradle

# Manual tool invocation
$ systemagentctl tools invoke docker ps
[{"ID":"abc123","Image":"postgres:15","Status":"Up 2 hours",...}]

# View recent agent logs
$ systemagentctl logs log --lines 20
```

---

## 11. Success Criteria

| # | Criterion | How to Verify |
|---|-----------|---------------|
| 1 | Agents run as systemd services with cgroup isolation | `systemctl status systemagentd-agent@log` shows running, `systemd-cgtop` shows resource usage |
| 2 | Crash-Agent analyzes a core dump and suggests a fix | Trigger segfault in test binary → check journal for analysis |
| 3 | Log-Agent surfaces anomalies from journalctl in real-time | Generate log spike → agent detects within 10s |
| 4 | Resource-Agent predicts resource exhaustion | Run memory-consuming process → agent alerts before OOM |
| 5 | `systemagentctl status` shows all running agents | CLI output matches expected format |
| 6 | Tool Registry discovers and invokes CLI tools | `systemagentctl tools invoke docker ps` returns structured output |
| 7 | Supervisor restarts crashed agents (one_for_one) | Kill agent process → supervisor restarts within 5s |
| 8 | Restart intensity limit prevents crash loops | Crash agent 6 times in 60s → supervisor marks degraded |
| 9 | SFS daemon runs as a managed agent | `systemagentctl status fs` shows FS-Agent wrapping SFS |
| 10 | Config hot-reload works | Modify agents.toml → SIGHUP → new agents start |

---

## 12. VRAM/Resource Budget

SystemAgentd itself uses no GPU. Agents call Model Bus only for LLM reasoning, which is the existing VRAM budget from Phase 2.

| Component | CPU Budget | RAM Budget |
|-----------|-----------|------------|
| systemagentd supervisor | < 1% | ~50 MB |
| Log-Agent | < 2% | ~50 MB |
| Crash-Agent (idle) | 0% | 0 MB (socket-activated) |
| Crash-Agent (active) | < 5% | ~100 MB |
| Resource-Agent | < 2% | ~50 MB |
| Net-Agent | < 1% | ~30 MB |
| FS-Agent (wraps SFS) | Existing SFS budget | Existing SFS budget |
| **Total overhead** | **< 10% CPU** | **< 300 MB RAM** |

All agents are cgroup-limited via `yaaos-agents.slice` (30% CPU cap, 2GB RAM cap total).

---

## 13. Security Considerations

1. **No arbitrary code execution** — Agents invoke tools from the registry only. No `eval()`, no dynamic code generation.
2. **Tool sandboxing** — bubblewrap (bwrap) namespace isolation for tool execution. Filesystem restricted to declared paths. Network restricted per tool manifest.
3. **Privilege separation** — Agents run as unprivileged user. Tools requiring root must be explicitly declared and the user must opt-in.
4. **cgroup isolation** — Each agent in its own cgroup slice. A runaway agent cannot starve the system.
5. **No outbound network by default** — Agents don't make network calls. Only Model Bus (local socket) and Tool Registry (local CLI). Cloud LLM calls go through Model Bus which handles API keys.
6. **Structured audit log** — Every tool invocation logged to journald with full parameters and result.

---

## 14. Anti-Patterns to Avoid (from Research)

These are validated failure modes from studying Erlang/OTP, Kubernetes, systemd, and existing AI agent frameworks:

| Anti-Pattern | What Goes Wrong | Mitigation |
|---|---|---|
| **Replacing systemd** | Reimplementing process spawn, signals, cgroups | Orchestrate THROUGH systemd, don't replace it |
| **Crash loop amplification** | Agent crashes → immediate restart → crash again → log flood | OTP intensity limiter + systemd StartLimitBurst as backstop |
| **Cascading Model Bus failure** | Model Bus dies → all agents fail → thundering herd restart | Detect Model Bus health, pause agents, stagger restarts with jitter |
| **Blocking D-Bus calls** | Synchronous D-Bus in asyncio → event loop stall → watchdog kills daemon | Use `dbus-next` (fully async) for all systemd management |
| **sd_notify race** | Send READY=1 before socket is bound → dependents start too early | Bind socket → start reconciler → THEN sd_notify |
| **Orphaned agents** | Supervisor restarts but forgets pre-existing agents | Reconciliation loop queries systemd for all `agent@*` units on startup |
| **Template %i escaping** | Agent names with dots/slashes break systemd instantiation | Restrict agent IDs to `[a-z][a-z0-9-]*` |
| **Command injection** | LLM puts `; rm -rf /` in tool args | `create_subprocess_exec` (never `shell=True`), validate against JSON Schema |
| **LLM tool hallucination** | LLM invents nonexistent tools or wrong arg types | Validate tool name exists + args match inputSchema + max 10-15 tools per agent |
| **Unbounded ReAct loops** | LLM calls same tool repeatedly forever | `max_turns` limit per reasoning cycle (default: 5) |

### Cascading Failure Mitigation (Critical)

When Model Bus goes down, all agents lose their reasoning capability. Naive approach: all agents crash, all restart when Bus returns, thundering herd. Correct approach:

```
Model Bus DOWN detected (via health check)
  → SystemAgentd sets all agents to DEGRADED (not FAILED)
  → Agents continue observe() but skip reason() (rule-based fast path only)
  → No restarts triggered for LLM-related failures

Model Bus UP detected
  → SystemAgentd stagger-restores agents with random jitter (0-5s)
  → Prevents thundering herd on Model Bus
```

This is a direct application of the Kubernetes "graceful degradation" pattern — components degrade in capability rather than crash when dependencies are unavailable.

---

## 15. Future Considerations (Not in Phase 3)

- **Inter-agent messaging** — Pub/sub on Agent Bus for agent-to-agent events. Needed for Phase 4 when aish coordinates agents.
- **A2A protocol support** — Google's Agent2Agent protocol for external agent interoperability. Consider as a transport binding for Agent Bus.
- **MCP server mode** — Expose Agent Bus as an MCP server so external LLMs (Claude, GPT) can invoke YAAOS tools.
- **Agent marketplace** — Third-party agents installed via pacman. Entry-point plugin discovery (same as Model Bus providers).
- **eBPF integration** — Replace `/proc/net` parsing with BCC/bpftrace for lower overhead network monitoring.
- **Predictive ML models** — Replace simple trend extrapolation with trained models for resource prediction.
- **Remote agent management** — SSH-tunneled Agent Bus for managing agents on remote YAAOS machines.

---

## 15. Key Architectural Influences

| Influence | What We Took | Source |
|-----------|-------------|--------|
| **Erlang/OTP** | Supervision trees, restart strategies, intensity limits, "let it crash" | [Erlang Supervisor Design Principles](https://www.erlang.org/doc/system/sup_princ.html) |
| **Kubernetes** | Level-triggered reconciliation loop, desired-state vs actual-state | [Level Triggering in K8s](https://hackernoon.com/level-triggering-and-reconciliation-in-kubernetes-1f17fe30333d) |
| **MCP** | Tool definition schema (name, description, inputSchema), JSON-RPC protocol | [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25) |
| **A2A Protocol** | Agent Card concept (capabilities, discovery), task lifecycle states | [A2A Specification](https://a2a-protocol.org/latest/specification/) |
| **ReAct** | Observe → Reason → Act cycle for agent execution | [ReAct Pattern](https://www.promptingguide.ai/techniques/react) |
| **LangGraph** | Supervisor pattern, checkpointing for state persistence | [LangGraph Supervisor](https://github.com/langchain-ai/langgraph-supervisor-py) |
| **systemd** | Type=notify, watchdog, socket activation, template units, cgroups | [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) |
| **bubblewrap** | Lightweight namespace sandboxing for tool execution | [Anthropic Sandbox Runtime](https://github.com/anthropic-experimental/sandbox-runtime) |
| **s6/daemontools** | Minimal restart backoff, supervision directory scanning | [s6 Overview](https://skarnet.org/software/s6/overview.html) |
| **YAAOS Model Bus** | JSON-RPC 2.0 / NDJSON / Unix socket pattern, structlog, click CLI | Phase 2 codebase |
