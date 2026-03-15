# Phase 3: SystemAgentd (Agent Orchestration & Tool Registry) - Research

**Researched:** 2026-03-15
**Domain:** Process supervision, agent orchestration, fault-tolerant daemon design, tool registry
**Confidence:** HIGH

## Summary

SystemAgentd is a supervisor daemon that manages AI agents as systemd services on Arch Linux. This research covers both the supervisor daemon architecture and the tool registry system. For the daemon, five supervision paradigms were investigated: Erlang/OTP supervisor trees (the gold standard for fault tolerance), systemd service management (our deployment substrate), Kubernetes controller reconciliation loops (the core architecture pattern), traditional process supervisors (supervisord, runit, s6, daemontools), and actor model frameworks (Akka, Ray, Orleans).

The core insight is that SystemAgentd should NOT replace systemd -- it should ORCHESTRATE through systemd. The daemon acts as a higher-level supervisor that uses systemd as its process management substrate, adding agent-specific intelligence: health semantics beyond process-alive, LLM-powered analysis, cross-agent coordination, and a tool registry that wraps CLI tools for agent use. The Kubernetes reconciliation loop pattern (desired state -> observe -> diff -> act) is the correct architecture for the control plane, while Erlang/OTP restart strategies inform the fault tolerance design.

For the tool registry, MCP's tool schema (JSON Schema inputSchema + structured output) is the right wire format, aligning with the existing JSON-RPC 2.0 transport used by ModelBus. Tools are defined as TOML manifests discovered from `tools.d/` directories, with bubblewrap sandboxing for untrusted execution.

**Primary recommendation:** Build a reconciliation-loop daemon that manages agents as systemd template units (`systemagentd-agent@.service`), using `sd_notify` for readiness, watchdog timers for liveness, and an OTP-inspired restart intensity limiter to prevent crash loops. Expose tools via MCP-compatible JSON-RPC methods on the agent bus socket.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio | stdlib | Event loop, subprocess management, Unix socket server | Already used by Model Bus; proven pattern in this project |
| structlog | latest | Structured JSON logging | Already used by Model Bus; consistent logging |
| sdnotify | 0.3.2+ | systemd Type=notify readiness and watchdog | Pure Python, simple API: `n.notify("READY=1")`, `n.notify("WATCHDOG=1")` |
| systemd-python (python-systemd) | latest | Journal access, daemon utilities | Native libsystemd bindings: `systemd.journal`, `systemd.daemon`, `systemd.login` |
| orjson | latest | Fast JSON serialization for NDJSON wire protocol | Already used by Model Bus server |
| click | latest | CLI framework for `systemagentctl` | Already used by Model Bus CLI |
| tomli/tomllib | stdlib (3.11+) | TOML config parsing | Already used by Model Bus config |
| pydantic | 2.x | Tool schema validation, config models | Industry standard for JSON Schema generation from Python types |
| jsonschema | 4.x | Runtime validation of tool inputs against JSON Schema | Standard validator for dynamic tool input validation |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| dbus-next | 0.2.x | Async D-Bus client for systemd unit management | Starting/stopping/querying systemd units programmatically |
| psutil | latest | Process and system resource monitoring | Already a project dependency; CPU/RAM/cgroup stats |
| pynvml | latest | GPU monitoring | Already a project dependency; VRAM tracking |
| python-dotenv | latest | .env loading | Already used; API key management |
| watchfiles | 1.x | Watch tools.d/ directory for manifest changes | Hot-reload tool registry |
| bubblewrap (bwrap) | 0.9+ | CLI tool sandboxing | System package: `pacman -S bubblewrap` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| dbus-next | pystemd | pystemd has tighter systemd binding but less maintained; dbus-next is async-native |
| asyncio subprocesses | direct fork/exec | asyncio integrates with event loop, direct fork loses that |
| Custom reconciliation loop | Kubernetes client-python | Overkill; we need the pattern, not the framework |
| TOML manifests | Python entry_points | TOML is language-agnostic, easier for users to write; entry_points requires pip install |
| Pydantic for schemas | dataclasses + manual JSON Schema | Pydantic auto-generates JSON Schema from type annotations |
| bubblewrap | systemd-run --scope | systemd-run needs root; bwrap works unprivileged with user namespaces |
| Custom tool protocol | Full MCP SDK | MCP SDK adds complexity; adopt the wire format without the full SDK |

**Installation:**
```bash
# Python deps
uv add pydantic orjson structlog sdnotify dbus-next jsonschema watchfiles click psutil python-dotenv

# System deps (Arch Linux)
pacman -S python-systemd bubblewrap
```

## Architecture Patterns

### Recommended Project Structure
```
src/yaaos-agentd/
├── pyproject.toml
├── src/yaaos_agentd/
│   ├── __init__.py
│   ├── daemon.py             # Main entry, signal handling, sd_notify
│   ├── server.py             # JSON-RPC Unix socket server (agentbus.sock)
│   ├── reconciler.py         # Core reconciliation loop (desired -> observe -> diff -> act)
│   ├── supervisor.py         # OTP-inspired restart strategies, intensity limiter
│   ├── agent.py              # Agent lifecycle: spec, state machine, health
│   ├── config.py             # TOML config: /etc/yaaos/agents.toml
│   ├── cli.py                # systemagentctl CLI
│   ├── errors.py             # Error types
│   ├── types.py              # Core dataclasses/Pydantic models
│   ├── systemd_manager.py    # D-Bus interface to systemd (start/stop/status units)
│   ├── health.py             # Health check protocol (beyond process-alive)
│   ├── registry/
│   │   ├── __init__.py
│   │   ├── discovery.py      # Scan tools.d/ directories, watch for changes
│   │   ├── manifest.py       # Parse TOML tool manifests
│   │   ├── registry.py       # ToolRegistry class (list, get, validate, invoke)
│   │   └── schemas.py        # Pydantic models for tool manifests
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── bwrap.py          # Bubblewrap subprocess wrapper
│   │   ├── policy.py         # Sandbox policy (what to bind, what to block)
│   │   └── executor.py       # Tool execution with sandboxing
│   └── agents/
│       ├── __init__.py
│       ├── base.py           # Base agent class/protocol
│       ├── log_agent.py      # Log-Agent (journald analysis)
│       ├── crash_agent.py    # Crash-Agent (core dump analysis)
│       ├── resource_agent.py # Resource-Agent (CPU/RAM prediction)
│       └── net_agent.py      # Net-Agent (network anomaly detection)
├── systemd/
│   ├── systemagentd.service           # Main supervisor unit
│   ├── systemagentd-agent@.service    # Template unit for agents
│   └── systemagentd-crash.socket      # Socket activation for crash agent
└── tests/
```

### Pattern 1: Reconciliation Loop (Kubernetes-inspired)

**What:** A non-terminating control loop that continuously reconciles desired state with actual state. This is the CORE architecture of SystemAgentd.
**When to use:** Every tick: read desired state from config, observe actual state from systemd, compute diff, take action.

**Why this is robust (from Kubernetes docs):** "Controllers track at least one Kubernetes resource type with a spec field representing desired state. Controllers work to move the current cluster state closer to the desired state." Missed events don't matter because the loop always compares desired vs actual. Self-healing by design.

```python
# Source: Kubernetes controller pattern (https://kubernetes.io/docs/concepts/architecture/controller/)
class Reconciler:
    """Kubernetes-style reconciliation loop.

    The thermostat analogy: desired_state is the temperature you set,
    actual_state is the room temperature, actions are heating/cooling.
    """

    def __init__(self, registry: AgentRegistry, systemd: SystemdManager):
        self._registry = registry
        self._systemd = systemd
        self._interval = 5.0  # seconds between reconciliation ticks

    async def run(self):
        """Main reconciliation loop -- runs forever."""
        while not self._stopping:
            try:
                desired = self._registry.get_desired_state()  # From config
                actual = await self._systemd.get_actual_state()  # From systemd D-Bus
                actions = self._diff(desired, actual)
                await self._act(actions)
            except Exception:
                logger.exception("reconciler.tick_failed")
            await asyncio.sleep(self._interval)

    def _diff(self, desired: dict, actual: dict) -> list[Action]:
        """Compute actions needed to reach desired state."""
        actions = []
        for agent_id, spec in desired.items():
            if agent_id not in actual:
                actions.append(Action("start", agent_id, spec))
            elif actual[agent_id].state == "failed":
                actions.append(Action("restart", agent_id, spec))
            elif actual[agent_id].state == "running" and not spec.enabled:
                actions.append(Action("stop", agent_id, spec))
        # Also stop agents that are running but not in desired state
        for agent_id in actual:
            if agent_id not in desired:
                actions.append(Action("stop", agent_id, None))
        return actions
```

### Pattern 2: OTP-Inspired Restart Intensity Limiter

**What:** Prevent crash loops by tracking restart frequency. If more than MaxR restarts happen in MaxT seconds, escalate (stop the agent, alert, or apply backoff).
**When to use:** Every agent restart decision goes through this limiter.

**From Erlang OTP docs:** "If more than MaxR number of restarts occur in the last MaxT seconds, the supervisor terminates all the child processes and then itself." Default: intensity=1, period=5s. For AI agents with slower startup, use intensity=3, period=60s.

```python
# Source: Erlang OTP supervisor (https://www.erlang.org/doc/design_principles/sup_princ)
import time
from collections import deque

class RestartIntensityLimiter:
    """OTP-style MaxR/MaxT restart limiter.

    If more than max_restarts occur within period_seconds,
    the agent is marked as crash-looping and not restarted.
    """

    def __init__(self, max_restarts: int = 3, period_seconds: float = 60.0):
        self._max_restarts = max_restarts
        self._period = period_seconds
        self._restart_times: deque[float] = deque()

    def can_restart(self) -> bool:
        """Check if a restart is allowed within intensity limits."""
        now = time.monotonic()
        # Purge old entries outside the window
        while self._restart_times and (now - self._restart_times[0]) > self._period:
            self._restart_times.popleft()
        return len(self._restart_times) < self._max_restarts

    def record_restart(self) -> None:
        self._restart_times.append(time.monotonic())
```

### Pattern 3: OTP Restart Strategies

**What:** Erlang/OTP defines four restart strategies that determine how sibling failures are handled. These map directly to agent group management.

**From OTP docs (https://www.erlang.org/doc/design_principles/sup_princ):**

| Strategy | Behavior | YAAOS Mapping |
|----------|----------|---------------|
| `one_for_one` | Only restart the failed child | Default: agents are independent |
| `one_for_all` | If one fails, restart all siblings | For tightly coupled agent groups (future) |
| `rest_for_one` | Restart failed + all started after it | For dependency chains within a group |
| `simple_one_for_one` | All children are same type, dynamically added | Maps to template units -- all agents use same template |

**Key OTP concepts to adopt:**
- **Restart types per child:** `permanent` (always restart), `transient` (restart on abnormal exit only), `temporary` (never restart). Map to systemd: `Restart=always`, `Restart=on-failure`, `Restart=no`.
- **Shutdown strategy:** `brutal_kill` vs timeout vs `infinity`. Map to systemd `TimeoutStopSec`.
- **Supervision trees nest:** A supervisor can supervise other supervisors. SystemAgentd is the root supervisor; future agent groups could have sub-supervisors.
- **Let it crash:** Agents should crash cleanly rather than trying to handle every error internally. The supervisor handles restart logic.

### Pattern 4: Agent State Machine

**What:** Each agent has a well-defined lifecycle with clear state transitions.
**When to use:** All agent lifecycle management.

```python
from enum import Enum

class AgentState(Enum):
    """Agent lifecycle states.
    Inspired by s6's distinction between 'up' (running) and 'ready' (functional).
    """
    SPEC_ONLY = "spec_only"      # Config loaded, not started
    STARTING = "starting"         # systemd unit starting, waiting for READY=1
    RUNNING = "running"           # Healthy, passing watchdog
    DEGRADED = "degraded"         # Running but health checks failing
    STOPPING = "stopping"         # Graceful shutdown in progress
    STOPPED = "stopped"           # Clean exit
    FAILED = "failed"             # Crashed, eligible for restart
    CRASH_LOOP = "crash_loop"     # Exceeded restart intensity, needs manual intervention
```

### Pattern 5: systemd Template Units

**What:** A single template unit file that manages all agents uniformly.
**When to use:** All agent process management.

```ini
# systemd/systemagentd-agent@.service
# Source: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
[Unit]
Description=YAAOS Agent: %i
After=systemagentd.service yaaos-modelbus.service
BindsTo=systemagentd.service
PartOf=systemagentd.service

[Service]
Type=notify
ExecStart=/usr/bin/yaaos-agent %i
WatchdogSec=30
Restart=on-failure
RestartSec=5s
StartLimitBurst=5
StartLimitIntervalSec=120
# Resource isolation via cgroups
CPUQuota=10%%
MemoryMax=512M
# Environment
Environment=YAAOS_AGENT_ID=%i
EnvironmentFile=-/etc/yaaos/agents/%i.env

[Install]
WantedBy=systemagentd.service
```

**systemd Type=notify details (from official docs):** The daemon sends "READY=1" via `sd_notify()` before systemd considers it started. This enables proper startup verification. `NotifyAccess=` should be set appropriately. Recommended over Type=forking (which systemd explicitly discourages).

**WatchdogSec (from official docs):** Service must send periodic "WATCHDOG=1" messages. Failure to send within the interval triggers `SIGABRT`. Set WatchdogSec to 2x the heartbeat interval.

**Socket activation:** Crash-Agent uses a `.socket` unit -- systemd listens on the socket and starts the agent on-demand when data arrives. Socket survives agent restarts.

### Pattern 6: Health Check Protocol (Beyond Process-Alive)

**What:** Agents report semantic health, not just "process is running." Inspired by s6's distinction between "up" (running) and "ready" (functional).

**From s6 docs:** s6 "distinguishes between a daemon being 'up' (running) and 'ready' (actually functional)." An agent can be alive but unhealthy (e.g., lost connection to Model Bus, stale data).

```python
class HealthStatus:
    """Agent health report -- richer than systemd's binary alive/dead."""
    healthy: bool
    status: str                    # Human-readable status message
    last_activity: float           # Timestamp of last meaningful work
    checks: dict[str, bool]        # Named health checks
    # e.g., {"model_bus_connected": True, "journal_stream_active": True}
```

### Pattern 7: Tool Manifest (TOML) and MCP-Compatible Wire Format

**What:** CLI tools wrapped for agent use get TOML manifests declaring schema, permissions, and execution details. Exposed via MCP-compatible `tools/list` and `tools/call` JSON-RPC methods.
**When to use:** For every CLI tool the agent bus needs to invoke.

```toml
# /etc/yaaos/tools.d/git-status.toml
[tool]
name = "git_status"
version = "1.0.0"
title = "Git Repository Status"
description = "Show the working tree status of a git repository"
category = "vcs"

[tool.execution]
command = "git"
args_template = ["status", "--porcelain={porcelain}"]
working_dir = "{repo_path}"
timeout_seconds = 30
sandbox = "read-only"

[tool.input.properties.repo_path]
type = "string"
description = "Path to the git repository"

[tool.input.properties.porcelain]
type = "string"
enum = ["v1", "v2"]
default = "v2"

[tool.input.required]
values = ["repo_path"]

[tool.output]
format = "text"
success_exit_codes = [0, 1]

[tool.permissions]
requires = ["filesystem.read"]
dangerous = false
```

### Pattern 8: Sandbox Policies (Bubblewrap)

**What:** Different tools get different isolation levels via bubblewrap.

| Tier | Name | Use Case | Isolation |
|------|------|----------|-----------|
| 0 | none | Trusted system tools (systemctl, journalctl) | No sandbox |
| 1 | read-only | Read-only tools (git status, docker ps) | `--ro-bind / /` + RW working dir |
| 2 | network-isolated | Build tools (gradle, make) | Tier 1 + `--unshare-net` |
| 3 | full | Untrusted/downloaded tools | Minimal binds + all namespace isolation |

### Anti-Patterns to Avoid

- **Replacing systemd:** Do NOT implement process spawning, signal handling, or cgroup management. Let systemd do what it does. SystemAgentd orchestrates THROUGH systemd.
- **Polling systemd state too frequently:** 5-second reconciliation ticks are sufficient. Sub-second polling wastes CPU and D-Bus bandwidth.
- **Synchronous D-Bus calls:** Always use async D-Bus (dbus-next). Blocking calls in an asyncio daemon will stall the entire event loop.
- **Global restart policy:** Each agent needs its own restart intensity limiter. A crash-looping log agent should not prevent the resource agent from restarting.
- **Ignoring backpressure:** If Model Bus is down, all agents querying it fail simultaneously. Detect Model Bus availability and pause/degrade agents rather than letting them crash-loop.
- **Embedding tool definitions in Python code:** Tools should be data (TOML manifests), not code. Enables hot-reload and non-programmer contributions.
- **Passing raw user input to subprocess:** Always validate inputs against JSON Schema before constructing commands. Never use shell=True.
- **Unbounded ReAct loops:** Always set max_turns. LLMs can get stuck in loops calling the same tool repeatedly.
- **Monolithic agent process:** Each agent runs as its own systemd unit with cgroup isolation.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process management | Custom fork/exec/waitpid | systemd template units | cgroups, journald, watchdog, socket activation, dependency ordering -- all free |
| D-Bus communication | Raw socket protocol | dbus-next (async) | D-Bus wire protocol is complex; libraries handle auth, marshaling, signals |
| Watchdog heartbeat | Custom TCP keepalive | systemd WatchdogSec + sd_notify("WATCHDOG=1") | systemd kills and restarts unresponsive services automatically |
| Log aggregation | Custom log collection | journald + systemd.journal.Reader | All systemd services log to journal; filter by unit with journal API |
| Socket activation | Listen-then-fork | systemd .socket units | Sockets survive daemon restarts; zero-downtime upgrades possible |
| JSON-RPC server | New implementation | Copy/adapt Model Bus JsonRpcServer | Already proven in this project with NDJSON, streaming, connection tracking |
| Config hot-reload | File-polling loop | SIGHUP handler + inotify | Standard Unix pattern; systemd can send SIGHUP via `systemctl reload` |
| JSON Schema validation | Custom validator | `jsonschema` library | Edge cases with $ref, oneOf, format validators, defaults |
| Process sandboxing | Custom namespace/seccomp | `bubblewrap` (bwrap) | Battle-tested by Flatpak, handles namespace setup correctly |
| Tool input templating | String format/f-strings | Pydantic model + explicit substitution | Prevents injection attacks in command args |

**Key insight:** SystemAgentd's value is in ORCHESTRATION INTELLIGENCE (what agents to run, when to restart, how to coordinate, LLM-powered analysis, tool mediation), not in process management. Every cycle spent reimplementing systemd features is a cycle not spent on agent intelligence.

## Common Pitfalls

### Pitfall 1: Crash Loop Amplification
**What goes wrong:** Agent crashes, gets restarted immediately, crashes again -- consuming CPU and filling logs.
**Why it happens:** No restart rate limiting (or using systemd's `Restart=always` without `RestartSec` and `StartLimitBurst`).
**How to avoid:** Implement OTP-style restart intensity (MaxR/MaxT) at the SystemAgentd level AND configure systemd's `StartLimitBurst`/`StartLimitIntervalSec` as a backstop. Use exponential backoff for repeated failures.
**Warning signs:** Journal filling with repeated start/stop entries for same unit.

### Pitfall 2: Cascading Failure When Model Bus Goes Down
**What goes wrong:** Model Bus crashes. All agents that depend on it fail simultaneously. All get restarted. Thundering herd.
**Why it happens:** No dependency-aware restart coordination.
**How to avoid:** SystemAgentd should detect Model Bus state via its health endpoint. When Model Bus is down, pause agent restarts and set agents to DEGRADED. When Model Bus returns, stagger agent restarts with jitter.
**Warning signs:** All agents entering FAILED state within seconds of each other.

### Pitfall 3: Blocking the Event Loop with D-Bus
**What goes wrong:** Synchronous D-Bus calls block the asyncio event loop, making the daemon unresponsive (including watchdog heartbeats, causing systemd to kill it).
**Why it happens:** Using python-systemd's synchronous API in an async context.
**How to avoid:** Use dbus-next (fully async) for all systemd unit management. Never call synchronous D-Bus methods from async code.
**Warning signs:** Daemon's own watchdog fires; `systemagentctl status` times out.

### Pitfall 4: sd_notify Race Condition
**What goes wrong:** Sending READY=1 before the daemon is actually ready to handle requests.
**Why it happens:** Sending notification too early in startup sequence.
**How to avoid:** Follow Model Bus pattern: start server, bind socket, THEN notify. Order: 1) bind socket, 2) start reconciler, 3) sd_notify READY=1.
**Warning signs:** Dependent units start before SystemAgentd can serve requests.

### Pitfall 5: Orphaned Agent Processes
**What goes wrong:** SystemAgentd restarts but doesn't know about agents running before the restart.
**Why it happens:** Agent state was only in-memory; no reconciliation with systemd.
**How to avoid:** The reconciliation loop solves this by design. On startup, query systemd for ALL `systemagentd-agent@*.service` units and reconcile with desired state from config.
**Warning signs:** `systemctl list-units 'systemagentd-agent@*'` shows running agents SystemAgentd doesn't track.

### Pitfall 6: Template Unit %i Escaping
**What goes wrong:** Agent names with special characters break systemd unit instantiation.
**Why it happens:** The `%i` specifier undergoes systemd escaping rules.
**How to avoid:** Restrict agent IDs to `[a-z][a-z0-9-]*` -- lowercase alphanumeric and hyphens only. Validate in config loading.
**Warning signs:** `systemctl start systemagentd-agent@some.weird.name.service` fails.

### Pitfall 7: Command Injection via Tool Arguments
**What goes wrong:** LLM provides input like `; rm -rf /` in a tool argument.
**Why it happens:** Using `shell=True` or string formatting for commands.
**How to avoid:** Always use `create_subprocess_exec` (not `_shell`). Build argument lists as Python lists. Validate all inputs against JSON Schema.
**Warning signs:** Any code that concatenates strings to build commands.

### Pitfall 8: LLM Tool Call Hallucination
**What goes wrong:** The LLM invents tool names that don't exist or passes wrong argument types.
**Why it happens:** LLMs are probabilistic.
**How to avoid:** Validate tool name exists. Validate arguments against inputSchema. Return clear error messages. Limit tools exposed per agent (10-15 max).
**Warning signs:** Frequent "unknown tool" errors in agent logs.

## Code Examples

### systemd Integration via D-Bus (Async)

```python
# Using dbus-next for async systemd unit management
from dbus_next.aio import MessageBus

class SystemdManager:
    """Async interface to systemd via D-Bus."""

    SYSTEMD_BUS = "org.freedesktop.systemd1"
    SYSTEMD_PATH = "/org/freedesktop/systemd1"
    MANAGER_IFACE = "org.freedesktop.systemd1.Manager"

    async def connect(self):
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await self._bus.introspect(self.SYSTEMD_BUS, self.SYSTEMD_PATH)
        proxy = self._bus.get_proxy_object(
            self.SYSTEMD_BUS, self.SYSTEMD_PATH, introspection
        )
        self._manager = proxy.get_interface(self.MANAGER_IFACE)

    async def start_agent(self, agent_id: str) -> str:
        unit_name = f"systemagentd-agent@{agent_id}.service"
        job_path = await self._manager.call_start_unit(unit_name, "replace")
        return job_path

    async def stop_agent(self, agent_id: str) -> str:
        unit_name = f"systemagentd-agent@{agent_id}.service"
        job_path = await self._manager.call_stop_unit(unit_name, "replace")
        return job_path

    async def get_unit_state(self, agent_id: str) -> str:
        """Get ActiveState: active, inactive, failed, activating..."""
        unit_name = f"systemagentd-agent@{agent_id}.service"
        unit_path = await self._manager.call_get_unit(unit_name)
        introspection = await self._bus.introspect(self.SYSTEMD_BUS, unit_path)
        proxy = self._bus.get_proxy_object(self.SYSTEMD_BUS, unit_path, introspection)
        props = proxy.get_interface("org.freedesktop.DBus.Properties")
        state = await props.call_get("org.freedesktop.systemd1.Unit", "ActiveState")
        return state.value
```

### Watchdog Heartbeat

```python
# Source: sdnotify (https://github.com/bb4242/sdnotify)
import sdnotify

async def watchdog_loop(notifier: sdnotify.SystemdNotifier, interval: float):
    """Send periodic watchdog pings to systemd.

    WatchdogSec in unit file should be 2x this interval.
    """
    while True:
        notifier.notify("WATCHDOG=1")
        notifier.notify(f"STATUS=Managing {agent_count} agents")
        await asyncio.sleep(interval)

# In daemon startup:
notifier = sdnotify.SystemdNotifier()
# ... init server, reconciler ...
notifier.notify("READY=1")
# WatchdogSec=30 -> heartbeat every 15s
asyncio.create_task(watchdog_loop(notifier, 15.0))
```

### Reading journald Programmatically

```python
# Source: python-systemd (https://github.com/systemd/python-systemd)
from systemd import journal

class JournalReader:
    """Read journal entries for log agent analysis."""

    def __init__(self, unit: str | None = None):
        self._reader = journal.Reader()
        if unit:
            self._reader.add_match(_SYSTEMD_UNIT=unit)
        self._reader.seek_tail()
        self._reader.get_previous()

    async def stream_entries(self):
        """Yield new journal entries as they arrive."""
        import select
        fd = self._reader.fileno()
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        while True:
            if poll.poll(1000):
                self._reader.process()
                for entry in self._reader:
                    yield {
                        "message": entry.get("MESSAGE", ""),
                        "priority": entry.get("PRIORITY", 6),
                        "unit": entry.get("_SYSTEMD_UNIT", ""),
                        "timestamp": entry.get("__REALTIME_TIMESTAMP"),
                    }
```

### Agent Config (TOML)

```toml
# /etc/yaaos/agents.toml

[supervisor]
reconcile_interval = 5.0
socket_path = "/run/yaaos/agentbus.sock"

[defaults]
restart_max = 3               # OTP MaxR
restart_period = 60            # OTP MaxT (seconds)
watchdog_sec = 30
cpu_quota = "10%"
memory_max = "512M"

[agents.log]
enabled = true
description = "Real-time journald analysis"
type = "simple"
restart_max = 5
command = "yaaos-agent log"

[agents.crash]
enabled = true
description = "Core dump analysis"
type = "socket"
socket_path = "/run/yaaos/crash-trigger.sock"
command = "yaaos-agent crash"

[agents.resource]
enabled = true
description = "CPU/RAM prediction and cgroup tuning"
type = "simple"
cpu_quota = "5%"
command = "yaaos-agent resource"

[agents.net]
enabled = false
description = "Network anomaly detection"
type = "simple"
command = "yaaos-agent net"
```

### Tool Executor with Bubblewrap Sandboxing

```python
import asyncio
from dataclasses import dataclass

@dataclass
class ToolResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    is_error: bool

    def to_mcp_result(self) -> dict:
        return {
            "content": [{"type": "text", "text": self.stdout if not self.is_error else self.stderr}],
            "structuredContent": {
                "exit_code": self.exit_code,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "duration_ms": self.duration_ms,
            },
            "isError": self.is_error,
        }

class ToolExecutor:
    async def execute(self, manifest: ToolManifest, arguments: dict) -> ToolResult:
        import time
        cmd_args = self._build_args(manifest, arguments)
        sandbox = SandboxPolicy(level=manifest.execution.sandbox)
        working_dir = self._resolve_working_dir(manifest, arguments)
        bwrap_args = sandbox.to_bwrap_args(working_dir or "/tmp")
        full_cmd = bwrap_args + [manifest.execution.command] + cmd_args

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=manifest.execution.timeout_seconds,
            )
            duration_ms = (time.monotonic() - start) * 1000
            is_error = proc.returncode not in manifest.output.success_exit_codes
            return ToolResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
                is_error=is_error,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                exit_code=-1, stdout="",
                stderr=f"Timed out after {manifest.execution.timeout_seconds}s",
                duration_ms=(time.monotonic() - start) * 1000,
                is_error=True,
            )
```

## Supervision Model Deep Dive

### Erlang/OTP Supervisor Trees (Gold Standard)

**Source:** https://www.erlang.org/doc/design_principles/sup_princ

**Architecture:** Hierarchical tree of supervisor and worker processes. "Workers are processes that perform computations and other actual work. Supervisors are processes that monitor workers."

**Restart strategies:**
- `one_for_one`: Only restart the failed child. Default. Independent agents.
- `one_for_all`: All siblings restart when one fails. For tightly coupled groups.
- `rest_for_one`: Restart failed + all started after it. For dependency chains.
- `simple_one_for_one`: Dynamic children of same type. Maps to template units.

**Intensity limiter:** MaxR restarts in MaxT seconds. If exceeded, supervisor itself terminates (escalates up tree). "Multi-level supervision multiplies total allowed restarts (e.g., 10x10=100 total)."

**Child restart types:** `permanent` (always restart), `transient` (restart on abnormal exit only), `temporary` (never restart).

**Auto-shutdown:** Supervisors can terminate when significant children complete: `any_significant` or `all_significant`.

**"Let it crash" philosophy:** Processes should crash cleanly. The supervisor handles restart. Don't try to handle every error inside the process.

**Replication in Python:** Implement `RestartIntensityLimiter` per agent. Use `one_for_one` as default (agents are independent). The reconciliation loop acts as the supervisor. Each agent's restart type maps to systemd's `Restart=` directive.

### systemd as Supervisor

**Source:** https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html

**Key features for SystemAgentd:**
- **Type=notify:** Daemon sends READY=1 via sd_notify before systemd considers it started. "The use of Type=forking is discouraged, use notify instead."
- **WatchdogSec:** Service must send WATCHDOG=1 periodically. Failure triggers SIGABRT.
- **Restart=on-failure:** Restart on non-zero exit, signal, timeout, or watchdog failure.
- **StartLimitBurst/IntervalSec:** systemd's own crash loop prevention (backstop for OTP limiter).
- **Template units (foo@.service):** `%i` is the instance parameter. `systemagentd-agent@log.service`.
- **Socket activation:** Sockets survive daemon restarts. Crash-Agent activated on demand.
- **BindsTo/PartOf:** Agent units bound to SystemAgentd -- stop when supervisor stops.
- **CPUQuota/MemoryMax:** cgroup resource isolation per agent unit.

**sd_notify from Python:** `sdnotify` library. `n.notify("READY=1")`, `n.notify("WATCHDOG=1")`, `n.notify("STATUS=...")`, `n.notify("STOPPING=1")`.

### Kubernetes Controller Pattern

**Source:** https://kubernetes.io/docs/concepts/architecture/controller/

**Core concept:** Non-terminating control loop. Thermostat analogy. Desired state (spec) vs actual state. Controller moves actual toward desired.

**Two control mechanisms:** (1) Control via API server (most common) -- observe resources, send messages, API creates/removes objects. (2) Direct control -- communicate directly with external systems.

**Design principles:** Separation of concerns (each controller manages one resource type). Resilience (controller failure doesn't cascade). Label-based differentiation (controllers use labels to identify their resources).

**Why level-triggered beats edge-triggered:** The reconciliation loop checks current state, not events. Missed events don't cause state drift. After a restart, the controller catches up by comparing desired vs actual.

### Process Supervisor Comparison

| Feature | supervisord | runit | s6 | daemontools | systemd |
|---------|------------|-------|-----|-------------|---------|
| Direct supervision (no PID files) | Yes | Yes | Yes | Yes | Yes |
| Readiness notification | No | No | Yes (fd write) | No | Yes (sd_notify) |
| Resource isolation | No | No | No | No | Yes (cgroups) |
| Socket activation | No | No | Limited | No | Yes |
| Structured logging | No | No | No | No | Yes (journald) |
| Watchdog | No | No | No | No | Yes |
| API for management | XML-RPC | No | CLI only | CLI only | D-Bus |
| Dependency ordering | Priority-based | No | Limited | No | Yes |

**s6 standout insight:** Distinguishes "up" (running) from "ready" (functional). "Daemons can die unexpectedly... the supervision tree always has the same environment, so starting conditions are reproducible." No PID files -- "the supervisor always knows the correct PID through direct parent-child relationships."

**Key takeaway:** systemd provides everything the other supervisors provide plus cgroups, journald, socket activation, and watchdog. supervisord's XML-RPC API pattern (programmatic process management) is what SystemAgentd's JSON-RPC API replaces.

### Actor Model Frameworks

**Ray (Python-native):** `@ray.remote` decorator. `max_restarts` and `max_task_retries` for fault tolerance. Stateful workers in dedicated processes. Serial execution per actor. Supervisor actors can manage trees of actors.

**Akka:** Parent actors supervise children. Strategies: restart, stop, escalate. Backoff supervision for retry with exponential delay. DeathWatch for monitoring actor termination.

**Orleans (virtual actors / "Grains"):** Activated on demand, deactivated when idle. Lifecycle stages: First -> SetupState -> Activate -> Last. Memory-based activation shedding under pressure. Grains can migrate between silos (relevant for future multi-node YAAOS).

**Key takeaways:**
- Orleans' virtual actor pattern maps to socket-activated agents (activated on demand, deactivated when idle)
- Ray's `max_restarts` is a simpler OTP intensity limiter
- Akka's backoff supervision (exponential delay on restart) should be implemented
- Orleans' memory-based activation shedding informs resource-aware agent management

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| supervisord (monolithic) | systemd native units | ~2015 | cgroups, journal, socket activation for free |
| PID files for tracking | Direct parent-child or systemd tracking | ~2012 | No race conditions |
| Polling process state | D-Bus signals + reconciliation loop | Current | Event-driven + self-healing |
| Type=forking daemons | Type=notify daemons | systemd recommendation | Precise readiness signaling |
| Custom log infrastructure | journald + structured logging | ~2015 | Automatic log capture |
| Restart=always | Restart=on-failure + intensity limiting | Current | Prevents crash loops |
| Custom tool protocols | MCP as universal tool protocol | 2024-2025 | Interop between AI frameworks |
| Hardcoded tool lists | Dynamic tool discovery with hot-reload | 2024-2025 | Extensible tool ecosystems |

## Open Questions

1. **D-Bus library choice: dbus-next vs pystemd**
   - What we know: dbus-next is fully async, pure Python. pystemd is Cython-based, Facebook-maintained.
   - What's unclear: Performance difference for ~10 units
   - Recommendation: Start with dbus-next (async-native fits asyncio architecture)

2. **Agent state persistence across restarts**
   - What we know: systemd restarts agents, but conversation history is lost
   - What's unclear: How much state is worth saving? SQLite? Filesystem?
   - Recommendation: Start stateless (agents restart fresh). Add SQLite state store later if needed.

3. **Tool Registry architecture: static vs dynamic discovery**
   - What we know: Agents need CLI tools (adb, gradle, docker, git, pacman)
   - What's unclear: Static config only, or also PATH scan + capability probing?
   - Recommendation: Hybrid. Static TOML manifests in `tools.d/`. Dynamic availability check on startup (`shutil.which()`).

4. **Inter-agent communication**
   - What we know: Architecture doc shows D-Bus for inter-agent comm
   - What's unclear: Do agents need direct communication or only via SystemAgentd?
   - Recommendation: Start hub-and-spoke (agents -> SystemAgentd -> agents). Add D-Bus signals if latency demands.

5. **MCP server mode**
   - What we know: Using MCP-compatible wire format on agent bus is straightforward
   - What's unclear: Should external MCP clients connect directly?
   - Recommendation: Internal JSON-RPC only initially. Add MCP stdio adapter later.

## Sources

### Primary (HIGH confidence)
- Erlang OTP Supervisor Design Principles - https://www.erlang.org/doc/design_principles/sup_princ
- Erlang OTP Design Principles Overview - https://www.erlang.org/doc/design_principles/des_princ
- systemd.service man page - https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- Kubernetes Controller Concepts - https://kubernetes.io/docs/concepts/architecture/controller/
- s6 Overview - https://skarnet.org/software/s6/overview.html
- sdnotify PyPI/GitHub - https://github.com/bb4242/sdnotify
- python-systemd GitHub - https://github.com/systemd/python-systemd
- Ray Actors Documentation - https://docs.ray.io/en/latest/ray-core/actors.html
- Orleans Grain Lifecycle - https://learn.microsoft.com/en-us/dotnet/orleans/grains/grain-lifecycle
- supervisord Introduction - https://supervisord.org/introduction.html
- Python asyncio subprocess docs - https://docs.python.org/3/library/asyncio-subprocess.html
- YAAOS Model Bus daemon.py - existing project pattern for asyncio daemon, sd_notify, signal handling

### Secondary (MEDIUM confidence)
- dbus-next as async D-Bus library - ecosystem analysis, not benchmark-verified
- Bubblewrap docs (Arch Wiki) - verified from wiki, bwrap flags from man page
- MCP Tools specification - https://modelcontextprotocol.io/docs/concepts/tools

### Tertiary (LOW confidence)
- pystemd comparison - general knowledge, not verified benchmarks

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries verified from official sources and/or proven in YAAOS project
- Supervisor architecture (reconciliation loop, OTP): HIGH - verified from authoritative sources
- systemd integration: HIGH - verified from official freedesktop.org docs
- Tool registry and sandboxing: MEDIUM-HIGH - MCP format verified, bubblewrap from Arch Wiki
- Process supervisor comparison: HIGH - verified from official project documentation
- Actor model patterns: HIGH - verified from official Ray/Orleans docs

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable domain, slow-moving ecosystem)
