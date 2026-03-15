# Phase 3: SystemAgentd - Linux Daemon Best Practices Research

**Researched:** 2026-03-15
**Domain:** Linux daemon development, systemd integration, IPC, cgroup resource control, journald, socket activation
**Confidence:** HIGH (official systemd docs + existing YAAOS Model Bus patterns + official library docs)

## Summary

SystemAgentd is a Python asyncio supervisor daemon that manages AI agents as systemd service units on Arch Linux. This research covers seven critical domains for building a proper Linux daemon: (1) sd_notify and Type=notify daemon protocol, (2) Unix domain socket servers with JSON-RPC 2.0, (3) D-Bus integration for agent lifecycle signals, (4) cgroup v2 resource control, (5) journald structured logging, (6) systemd socket activation for on-demand agents, and (7) graceful shutdown with signal handling.

The existing Model Bus daemon (`yaaos-modelbus`) provides a battle-tested template. It already uses asyncio Unix sockets, NDJSON/JSON-RPC 2.0 framing, the `sdnotify` library, `structlog`, and signal-based graceful shutdown. SystemAgentd should follow the same patterns and extend them with D-Bus integration (via `dbus-next`), cgroup management (via `systemctl set-property`), journald structured logging (via `python-systemd`), and socket activation for on-demand agents like Crash-Agent.

This document complements the tool registry and agent orchestration research (MCP protocol, ReAct loops, bubblewrap sandboxing, TOML tool manifests) in `03-RESEARCH.md`.

**Primary recommendation:** Build SystemAgentd as an asyncio daemon following the Model Bus pattern (Unix socket + JSON-RPC 2.0 + NDJSON on `/run/yaaos/agentbus.sock`). Use `sdnotify` for systemd notification, `dbus-next` for D-Bus agent lifecycle signals, `python-systemd` for journald structured logging, and `systemctl set-property` for runtime cgroup tuning. Delegate all process supervision to systemd via service templates (`systemagentd-agent@.service`).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sdnotify | 0.3.2 | systemd READY/WATCHDOG/STATUS/STOPPING notifications | Pure Python, zero deps, graceful degradation on non-systemd systems |
| dbus-next | 0.2.3 | D-Bus service exposure + signal emission (asyncio-native) | Pure Python, zero deps, asyncio coroutine support, MIT license |
| systemd-python | 235 | journald structured logging (journal.send, JournalHandler) + journal reading | Official systemd bindings from the systemd team |
| structlog | latest | Structured logging (JSON in production, console in dev) | Already used by Model Bus, integrates with JournalHandler |
| orjson | latest | Fast JSON serialization for NDJSON wire format | Already used by Model Bus server.py |
| asyncio | stdlib | Event loop, Unix socket server, signal handling, subprocess | Already proven in Model Bus daemon.py + server.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| click | latest | CLI for `systemagentctl` management tool | Already used by Model Bus CLI |
| tomli/tomllib | stdlib 3.11+ | Config parsing (`/etc/yaaos/agents.toml`) | Already used by Model Bus config |
| psutil | latest | Process monitoring, resource usage queries | Already used by Model Bus ResourceManager |
| pynvml | latest | GPU VRAM monitoring for agent resource limits | Already used by Model Bus |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| sdnotify (pure Python) | systemd-python daemon module | systemd-python requires libsystemd C library; sdnotify is pure Python, simpler, graceful degradation |
| dbus-next (asyncio) | pydbus (GLib) | pydbus unmaintained since 2017, requires GLib main loop, not asyncio-compatible |
| dbus-next | jeepney | jeepney is lower-level, more boilerplate for same result |
| dbus-next | dbus-python (legacy) | C-based bindings, not asyncio-compatible, legacy project |
| systemctl set-property | direct /sys/fs/cgroup writes | systemd manages the cgroup tree; direct writes break delegation |

**Installation:**
```bash
# Python deps (in project venv via uv)
uv add sdnotify dbus-next structlog orjson click psutil

# System package (Arch Linux) -- provides systemd.journal, systemd.daemon
sudo pacman -S python-systemd
```

## Architecture Patterns

### Pattern 1: sd_notify Integration (Type=notify Daemon)

**What:** The sd_notify protocol lets a daemon tell systemd exactly when it is ready, its current status, that it is reloading config, or stopping. Combined with WatchdogSec, systemd monitors the daemon's health and restarts it if it hangs.

**When to use:** Every YAAOS daemon (Model Bus already uses this pattern).

**Key notification strings** (source: [sd_notify(3)](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html)):

| String | When to Send | Effect |
|--------|-------------|--------|
| `READY=1` | After server is listening + providers initialized | systemd marks unit "active (running)" |
| `STATUS=...` | During init, on state changes | Visible in `systemctl status` output |
| `WATCHDOG=1` | Periodically (half of WatchdogSec) | Resets watchdog timer, prevents SIGABRT |
| `STOPPING=1` | On SIGTERM, before shutdown begins | systemd knows shutdown is intentional |
| `RELOADING=1` | On SIGHUP, before config reload | Must be followed by READY=1 when done |
| `EXTEND_TIMEOUT_USEC=...` | During long operations | Extends startup/shutdown/runtime timeout |
| `WATCHDOG=trigger` | On internal error detection | Forces watchdog restart without waiting |
| `MAINPID=...` | When main process PID changes | Updates systemd's tracked PID |

**Example:**
```python
import sdnotify
import asyncio
import os

notifier = sdnotify.SystemdNotifier()

async def run_daemon(config):
    # Phase 1: Initialize (systemd is waiting for READY=1)
    notifier.notify("STATUS=Initializing agent supervisor...")

    server = JsonRpcServer(socket_path=config.socket_path)
    await server.start()

    dbus_iface = AgentBusInterface()
    dbus_bus = await setup_dbus(dbus_iface)

    for agent in config.autostart_agents:
        await start_agent(agent)

    # Phase 2: Signal readiness -- ONLY after everything is up
    notifier.notify("READY=1")
    notifier.notify(f"STATUS=Managing {len(config.autostart_agents)} agents")

    # Phase 3: Watchdog loop
    # systemd passes the interval via WATCHDOG_USEC env var
    watchdog_usec = int(os.environ.get("WATCHDOG_USEC", 0))
    if watchdog_usec > 0:
        interval = watchdog_usec / 1_000_000 / 2  # Ping at half the interval
        asyncio.create_task(_watchdog_loop(interval))

    # Phase 4: Wait for shutdown signal
    await shutdown_event.wait()

    notifier.notify("STOPPING=1")
    await graceful_shutdown()

async def _watchdog_loop(interval: float):
    """Periodically ping systemd watchdog. Runs as background task.
    If this task cannot execute, the event loop is blocked."""
    while True:
        notifier.notify("WATCHDOG=1")
        await asyncio.sleep(interval)

# Config reload via SIGHUP:
async def handle_reload():
    notifier.notify("RELOADING=1")
    notifier.notify("STATUS=Reloading configuration...")
    await reload_config()
    notifier.notify("READY=1")  # MUST follow RELOADING=1
    notifier.notify("STATUS=Configuration reloaded")
```

**Corresponding unit file:**
```ini
[Service]
Type=notify
WatchdogSec=60
NotifyAccess=all
# NotifyAccess=all allows child agent processes to also send notifications.
# NotifyAccess=main restricts to only the supervisor PID.
```

### Pattern 2: Unix Domain Socket Server (JSON-RPC 2.0 + NDJSON)

**What:** Reuse the proven Model Bus server pattern for the Agent Bus socket.

**When to use:** The Agent Bus API on `/run/yaaos/agentbus.sock`.

The Model Bus `server.py` (already in the codebase at `src/yaaos-modelbus/src/yaaos_modelbus/server.py`) implements:
- `asyncio.start_unix_server()` with per-line NDJSON framing
- JSON-RPC 2.0 dispatch with method registration (`register()` and `register_stream()`)
- Semaphore-based concurrency limiting (`max_connections`)
- Streaming handlers (async generators yielding chunks as notifications)
- Graceful shutdown: stop accepting, drain in-flight with configurable timeout, force-cancel remaining
- Broken pipe / connection reset protection
- Request counting, in-flight tracking, uptime metrics

**Key asyncio API** (source: [Python asyncio streams](https://docs.python.org/3/library/asyncio-stream.html)):
```python
# Start server
# Python 3.13+ has cleanup_socket=True for auto-removal
server = await asyncio.start_unix_server(
    client_connected_cb,
    path="/run/yaaos/agentbus.sock",
    limit=1024 * 1024,          # 1 MB max line size
    backlog=100,                 # Connection queue depth
)

# Client connection handler
async def client_connected_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    while True:
        line = await reader.readline()   # Read one NDJSON line
        if not line:
            break                        # Client disconnected

        # Process JSON-RPC request...
        response = orjson.dumps(result) + b"\n"
        writer.write(response)
        await writer.drain()             # Backpressure: wait for buffer to flush

    writer.close()
    await writer.wait_closed()
```

**Recommendation:** Copy `server.py` from Model Bus as starting point. Register Agent Bus methods: `agent.list`, `agent.start`, `agent.stop`, `agent.status`, `agent.resources`, `tools/list`, `tools/call`, `health`.

### Pattern 3: D-Bus Service Interface

**What:** Expose SystemAgentd on the system D-Bus for agent lifecycle signals that Desktop Environment and Agentic Shell can subscribe to.

**When to use:** Agent lifecycle events needing broadcast (D-Bus signals are pub/sub). NOT for heavyweight operations (use Unix sockets).

**Library:** `dbus-next` -- pure Python, asyncio-native, zero dependencies.

**Example** (source: [dbus-next official docs](https://python-dbus-next.readthedocs.io/en/latest/high-level-service/index.html)):
```python
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.service import ServiceInterface, method, signal, dbus_property

class AgentBusInterface(ServiceInterface):
    """D-Bus interface for SystemAgentd.

    Bus name: org.yaaos.AgentBus
    Object path: /org/yaaos/AgentBus
    Interface: org.yaaos.AgentBus
    """

    def __init__(self):
        super().__init__("org.yaaos.AgentBus")
        self._agent_count = 0

    # --- Methods (callable by D-Bus clients) ---

    @method()
    async def ListAgents(self) -> 'as':   # 'as' = array of strings
        """Return list of agent names."""
        return list(agent_registry.keys())

    @method()
    async def StartAgent(self, name: 's') -> 'b':  # 's'=string, 'b'=boolean
        """Start an agent by name. Returns success."""
        success = await _start_agent(name)
        if success:
            self.AgentStarted(name)
        return success

    @method()
    async def StopAgent(self, name: 's') -> 'b':
        """Stop an agent by name."""
        success = await _stop_agent(name)
        if success:
            self.AgentStopped(name)
        return success

    # --- Signals (broadcast to all listeners) ---

    @signal()
    def AgentStarted(self, name: 's') -> 's':
        """Emitted when an agent starts successfully."""
        return name

    @signal()
    def AgentStopped(self, name: 's') -> 's':
        """Emitted when an agent stops."""
        return name

    @signal()
    def AgentError(self, name: 's', error: 's') -> '(ss)':
        """Emitted when an agent encounters an error."""
        return [name, error]

    # --- Properties (queryable state) ---

    @dbus_property()
    def AgentCount(self) -> 'u':  # 'u' = uint32
        """Number of active agents."""
        return self._agent_count

async def setup_dbus(interface: AgentBusInterface) -> MessageBus:
    """Export AgentBus interface on the system D-Bus."""
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export("/org/yaaos/AgentBus", interface)
    await bus.request_name("org.yaaos.AgentBus")
    return bus
```

**D-Bus type annotations** (source: [dbus-next type system](https://python-dbus-next.readthedocs.io/en/latest/type-system/index.html)):

| D-Bus Signature | Python Type | Example |
|----------------|-------------|---------|
| `s` | str | Agent name |
| `b` | bool | Success/failure |
| `u` | int (uint32) | Agent count |
| `i` | int (int32) | PID |
| `as` | list[str] | List of agent names |
| `a{ss}` | dict[str, str] | Name to status mapping |
| `(ss)` | list (2 elements) | [name, error] tuple |
| `v` | Variant | Dynamic type |

**Required D-Bus policy file** (`/etc/dbus-1/system.d/org.yaaos.AgentBus.conf`):
```xml
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <!-- Allow the service to own its bus name -->
  <policy user="root">
    <allow own="org.yaaos.AgentBus"/>
    <allow send_destination="org.yaaos.AgentBus"/>
  </policy>
  <!-- Allow all users to call methods and receive signals -->
  <policy context="default">
    <allow send_destination="org.yaaos.AgentBus"/>
    <allow receive_sender="org.yaaos.AgentBus"/>
  </policy>
</busconfig>
```

**D-Bus vs Unix Socket decision:**
- **D-Bus:** Lifecycle signals (agent started/stopped/error), desktop notifications, lightweight queries. Broadcast/pub-sub model.
- **Unix Socket (Agent Bus):** Management operations (start/stop agents, tool invocation, resource queries, health checks). Request/response with streaming.

### Pattern 4: cgroup v2 Resource Control

**What:** Every agent runs in its own cgroup via systemd's service template. Default limits are in the unit file; runtime tuning uses `systemctl set-property`.

**When to use:** Set defaults in `agent@.service`. Runtime tuning by Resource-Agent when it detects contention.

**Key cgroup v2 controls** (source: [systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)):

| Setting | Purpose | Format | Behavior |
|---------|---------|--------|----------|
| `CPUQuota=` | Hard CPU time limit | Percentage (e.g., `10%`) | Throttles when exceeded |
| `CPUWeight=` | Proportional CPU share | 1-10000 (default 100) | Fair scheduling under contention |
| `MemoryMax=` | Hard memory limit | Bytes with suffix (e.g., `1G`) | OOM-kills if exceeded |
| `MemoryHigh=` | Soft memory limit | Bytes with suffix | Throttles (slows down), no kill |
| `MemoryLow=` | Memory protection | Bytes with suffix | Protects from reclaim |
| `IOWeight=` | Proportional I/O share | 1-10000 (default 100) | Fair I/O scheduling |
| `IOReadBandwidthMax=` | I/O read cap | "device bandwidth" | Hard per-device limit |
| `TasksMax=` | Max processes/threads | Integer | Prevents fork bombs |

**Agent template unit with defaults:**
```ini
# systemagentd-agent@.service
[Unit]
Description=YAAOS Agent: %i
After=systemagentd.service
BindsTo=systemagentd.service

[Service]
Type=notify
ExecStart=/usr/bin/yaaos-agent --name %i
WatchdogSec=30
Restart=on-failure
RestartSec=5

# Default resource limits (overridable at runtime via systemctl set-property)
CPUQuota=10%
MemoryMax=1G
MemoryHigh=768M
TasksMax=64
IOWeight=50

# Security hardening
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/yaaos
NoNewPrivileges=true
PrivateTmp=true

# Directory management (systemd creates and manages these)
RuntimeDirectory=yaaos/agents/%i
StateDirectory=yaaos/agents/%i
# RuntimeDirectory -> /run/yaaos/agents/%i (cleaned on stop)
# StateDirectory -> /var/lib/yaaos/agents/%i (persistent across restarts)

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=yaaos-agent-%i

# Environment
Environment=PYTHONUNBUFFERED=1
Environment=YAAOS_AGENT_NAME=%i

[Install]
WantedBy=systemagentd.service
```

**Runtime tuning from Python:**
```python
import asyncio

async def set_agent_resources(
    name: str,
    cpu_quota: str | None = None,
    memory_max: str | None = None,
    memory_high: str | None = None,
    io_weight: int | None = None,
    tasks_max: int | None = None,
) -> bool:
    """Dynamically adjust agent resource limits without restarting.

    Uses 'systemctl set-property --runtime' which modifies cgroup v2
    controls immediately. --runtime means changes are lost on service
    restart (unit file defaults apply again).
    """
    unit = f"systemagentd-agent@{name}.service"
    args = ["systemctl", "set-property", unit, "--runtime"]

    if cpu_quota:
        args.append(f"CPUQuota={cpu_quota}")
    if memory_max:
        args.append(f"MemoryMax={memory_max}")
    if memory_high:
        args.append(f"MemoryHigh={memory_high}")
    if io_weight is not None:
        args.append(f"IOWeight={io_weight}")
    if tasks_max is not None:
        args.append(f"TasksMax={tasks_max}")

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode == 0

async def get_agent_resource_usage(name: str) -> dict:
    """Read current resource usage from systemd/cgroup."""
    unit = f"systemagentd-agent@{name}.service"
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "show", unit,
        "--property=MemoryCurrent,CPUUsageNSec,TasksCurrent,IOReadBytes,IOWriteBytes",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    props = {}
    for line in stdout.decode().strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return props

# Default resource profiles per agent type:
AGENT_PROFILES = {
    "log":      {"cpu_quota": "5%",  "memory_max": "256M", "io_weight": 50},
    "crash":    {"cpu_quota": "25%", "memory_max": "1G",   "io_weight": 100},
    "resource": {"cpu_quota": "10%", "memory_max": "512M", "io_weight": 75},
    "net":      {"cpu_quota": "5%",  "memory_max": "256M", "io_weight": 50},
}
```

### Pattern 5: Journald Structured Logging

**What:** Emit structured journal entries with custom fields (AGENT_NAME, AGENT_ID) queryable programmatically. Log-Agent reads these to analyze system behavior.

**When to use:** All daemon and agent logging. Custom fields enable per-agent filtering.

**Library:** `python-systemd` (Arch: `python-systemd`, PyPI: `systemd-python`).

**Example** (source: [python-systemd GitHub](https://github.com/systemd/python-systemd)):
```python
from systemd import journal
import logging

# --- Option 1: Direct journal.send() for maximum control ---
journal.send(
    "Agent started successfully",
    AGENT_NAME="log-agent",
    AGENT_ID="la-001",
    PRIORITY=journal.LOG_INFO,
    SYSLOG_IDENTIFIER="yaaos-agent-log",
    YAAOS_VERSION="0.1.0",
    TASK_ID="startup",
)

# --- Option 2: Python logging + JournalHandler (integrates with structlog) ---
logger = logging.getLogger("yaaos-agentd")
handler = journal.JournalHandler(SYSLOG_IDENTIFIER="yaaos-agentd")
logger.addHandler(handler)
logger.warning(
    "Agent %s failed health check",
    "net-agent",
    extra={"AGENT_NAME": "net-agent", "AGENT_ID": "na-001"},
)

# --- Option 3: structlog + JournalHandler ---
import structlog

def configure_logging(level: str = "INFO"):
    """Configure structlog to output to journald."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[
            journal.JournalHandler(SYSLOG_IDENTIFIER="yaaos-agentd"),
        ],
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

# --- Reading journal entries (for Log-Agent) ---
from systemd import journal as j
from datetime import datetime, timedelta

def read_recent_errors(minutes: int = 5) -> list[dict]:
    """Read recent error-level entries from the journal."""
    reader = j.Reader()
    reader.log_level(j.LOG_ERR)  # Filter: ERR and above only
    reader.seek_realtime(datetime.now() - timedelta(minutes=minutes))

    entries = []
    for entry in reader:
        entries.append({
            "message": entry.get("MESSAGE", ""),
            "unit": entry.get("_SYSTEMD_UNIT", ""),
            "priority": entry.get("PRIORITY", 6),
            "timestamp": entry.get("__REALTIME_TIMESTAMP"),
            "agent_name": entry.get("AGENT_NAME"),
        })
    return entries

# Filter by custom field:
reader = j.Reader()
reader.add_match(AGENT_NAME="crash-agent")

# Follow journal in real-time (for Log-Agent streaming):
import select

reader = j.Reader()
reader.seek_tail()
reader.get_previous()  # Position at latest entry

poll = select.poll()
poll.register(reader, reader.get_events())

while True:
    if poll.poll(1000):  # 1 second timeout
        if reader.process() == j.APPEND:
            for entry in reader:
                await process_entry(entry)
```

**CLI querying** (for debugging):
```bash
# All entries from a specific agent
journalctl AGENT_NAME=crash-agent

# Errors from all YAAOS agents
journalctl SYSLOG_IDENTIFIER=yaaos-agentd -p err

# Follow real-time from specific agent unit
journalctl -f _SYSTEMD_UNIT=systemagentd-agent@log.service

# JSON output for programmatic parsing
journalctl -o json AGENT_NAME=log-agent --since "5 min ago"
```

### Pattern 6: Socket Activation for On-Demand Agents

**What:** systemd listens on a socket and starts the Crash-Agent only when something connects (e.g., core dump needs analysis).

**When to use:** Crash-Agent (activated by core dump events), any agent that should not run permanently.

**How it works** (source: [systemd.socket(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html), [sd_listen_fds(3)](https://www.freedesktop.org/software/systemd/man/latest/sd_listen_fds.html)):

1. systemd creates and listens on the socket file
2. When a connection arrives, systemd starts the corresponding `.service` unit
3. systemd passes the already-bound socket as file descriptor 3+ to the service
4. The service reads from/writes to the FD normally
5. Service can stay running or exit (socket stays open; systemd restarts on next connection)

**Key constants and env vars:**
- `SD_LISTEN_FDS_START = 3` -- FDs start at 3 (0=stdin, 1=stdout, 2=stderr)
- `LISTEN_FDS` -- number of FDs passed
- `LISTEN_PID` -- PID that should receive the FDs (must match `os.getpid()`)
- `LISTEN_FDNAMES` -- optional comma-separated names (matches `FileDescriptorName=`)

**Unit files:**
```ini
# systemagentd-crash.socket
[Unit]
Description=YAAOS Crash-Agent Activation Socket

[Socket]
ListenStream=/run/yaaos/crash-agent.sock
FileDescriptorName=crash
Accept=no
# Accept=no: single service instance handles ALL connections (preferred)
# Accept=yes: new service instance per connection (heavier, more isolated)

[Install]
WantedBy=sockets.target
```

```ini
# systemagentd-crash.service
[Unit]
Description=YAAOS Crash-Agent (socket-activated)
Requires=systemagentd-crash.socket
After=systemagentd.service yaaos-modelbus.service

[Service]
Type=notify
ExecStart=/usr/bin/yaaos-agent --name crash --socket-activated
TimeoutStopSec=30
```

**Receiving socket FDs in Python:**
```python
import os
import socket
import asyncio

SD_LISTEN_FDS_START = 3

def get_activation_sockets() -> list[socket.socket]:
    """Receive socket-activated file descriptors from systemd.

    Returns empty list if not socket-activated (dev mode fallback).
    """
    listen_pid = int(os.environ.get("LISTEN_PID", 0))
    listen_fds = int(os.environ.get("LISTEN_FDS", 0))

    if listen_pid != os.getpid() or listen_fds == 0:
        return []

    sockets = []
    for fd in range(SD_LISTEN_FDS_START, SD_LISTEN_FDS_START + listen_fds):
        sock = socket.socket(fileno=fd)
        sock.setblocking(False)
        sockets.append(sock)

    # CRITICAL: Clear env vars to prevent child process confusion
    os.environ.pop("LISTEN_PID", None)
    os.environ.pop("LISTEN_FDS", None)
    os.environ.pop("LISTEN_FDNAMES", None)

    return sockets

async def start_crash_agent_server():
    """Start using systemd-passed socket or create own (dev mode)."""
    activated_socks = get_activation_sockets()

    if activated_socks:
        # Use the socket systemd gave us
        server = await asyncio.start_unix_server(
            handle_crash_request,
            sock=activated_socks[0],
        )
        logger.info("crash_agent.socket_activated")
    else:
        # Fallback: create own socket (development/testing)
        server = await asyncio.start_unix_server(
            handle_crash_request,
            path="/run/yaaos/crash-agent.sock",
        )
        logger.info("crash_agent.standalone_mode")

    return server
```

**Important notes on socket activation:**
- Do NOT call `socket.shutdown()` on systemd-passed FDs -- it affects systemd's copy
- Do NOT call `socket.bind()` -- the socket is already bound by systemd
- The socket FDs are duplicates of systemd's -- changes to socket options are visible to systemd
- After reading FDs, clear the environment variables to prevent child process confusion

**Triggering Crash-Agent from coredump events:**
```ini
# systemagentd-crash-trigger.path
[Unit]
Description=Watch for new core dumps to trigger Crash-Agent

[Path]
PathChanged=/var/lib/systemd/coredump/
# Triggers the associated .service unit when files change

[Install]
WantedBy=paths.target
```

### Pattern 7: Graceful Shutdown and Signal Handling

**What:** Handle SIGTERM (systemd stop), SIGINT (ctrl-c), SIGHUP (reload config) in asyncio.

**When to use:** Both supervisor daemon and individual agents.

**Source:** [Python asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html) + existing Model Bus `daemon.py`.

```python
import asyncio
import signal
import functools

async def run_daemon(config):
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    reload_event = asyncio.Event()

    # --- Register signal handlers (Unix only) ---
    def _handle_term(signame: str):
        logger.info("signal.received", signal=signame)
        shutdown.set()

    def _handle_hup():
        logger.info("signal.reload_requested")
        reload_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            functools.partial(_handle_term, sig.name),
        )
    loop.add_signal_handler(signal.SIGHUP, _handle_hup)

    # --- Start services ---
    server = await start_server(config)
    dbus_bus = await setup_dbus(AgentBusInterface())
    watchdog_task = asyncio.create_task(_watchdog_loop())

    notifier.notify("READY=1")

    # --- Main loop ---
    while not shutdown.is_set():
        reload_task = asyncio.create_task(reload_event.wait())
        shutdown_task = asyncio.create_task(shutdown.wait())

        done, pending = await asyncio.wait(
            [reload_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if reload_event.is_set() and not shutdown.is_set():
            reload_event.clear()
            notifier.notify("RELOADING=1")
            await reload_config()
            notifier.notify("READY=1")

    # --- Graceful shutdown sequence ---
    notifier.notify("STOPPING=1")
    logger.info("daemon.shutting_down")

    # 1. Cancel watchdog (no more pings needed)
    watchdog_task.cancel()

    # 2. Stop accepting new connections
    # (server.stop() in Model Bus pattern handles this)

    # 3. Signal all managed agents to stop
    for agent_name in list(agent_registry.keys()):
        await stop_agent(agent_name)

    # 4. Drain in-flight requests (with timeout)
    await server.stop()  # Model Bus pattern: stop_accepting + drain + force-cancel

    # 5. Close D-Bus connection
    dbus_bus.disconnect()

    logger.info("daemon.stopped")
```

**KillMode configuration** (source: [systemd.service(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)):
```ini
[Service]
# KillMode=mixed: SIGTERM to main process first, then SIGKILL to entire
# cgroup after TimeoutStopSec. Lets daemon cleanly stop agents before
# systemd force-kills everything.
KillMode=mixed
TimeoutStopSec=30
```

### Anti-Patterns to Avoid

- **Hand-rolling process supervision:** Never use `subprocess.Popen` to manage agent lifecycles. Delegate to systemd -- it handles cgroups, restarts, logging, watchdog, and cleanup.
- **Polling systemctl status:** Do not poll `systemctl is-active` in a loop. Subscribe to D-Bus signals from `org.freedesktop.systemd1.Manager` for unit state change notifications.
- **Direct cgroup filesystem writes:** Never write to `/sys/fs/cgroup/` directly. Always use `systemctl set-property` which handles cgroup v2 delegation correctly.
- **Synchronous D-Bus in asyncio:** Always use `dbus_next.aio.MessageBus`, never the blocking `MessageBus`. Blocking calls freeze the event loop and starve the watchdog.
- **Ignoring WATCHDOG_USEC:** If WatchdogSec is set, the daemon MUST ping. Missing pings cause SIGABRT.
- **sd_notify before server ready:** READY=1 before the socket is listening = "connection refused" from clients even though `systemctl status` shows active.
- **shutdown() on socket-activated FDs:** Do not call `socket.shutdown()` on FDs from systemd -- it affects systemd's copy.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process supervision | Custom fork/exec + PID tracking | systemd service templates (`agent@.service`) | Cgroup isolation, restart, logging, watchdog for free |
| IPC lifecycle signals | Custom pub/sub system | D-Bus signals via dbus-next | Standard Linux IPC, introspectable, desktop integration |
| Resource limiting | Direct cgroup v2 writes | `systemctl set-property` | Handles delegation, cgroup tree, error checking |
| Structured logging | Custom format + rotation | journald + python-systemd | Indexed, queryable by field, rate-limited, auto-rotated |
| Socket lifecycle | Manual create/bind/listen/cleanup | systemd socket activation | On-demand startup, FD preservation across restarts |
| Watchdog/health | Custom health thread + timer | systemd WatchdogSec + sd_notify | Kernel-level reliability, automatic SIGABRT on hang |
| Service dependencies | "Wait for X" loops | systemd After=/Requires=/BindsTo= | Declarative boot order, failure cascading |
| JSON-RPC server | Build from scratch | Copy Model Bus `server.py` | Proven NDJSON + streaming + drain pattern |
| Daemon readiness | "Ready file" or port probing | Type=notify + READY=1 | Standard protocol, integrates with systemctl |

**Key insight:** systemd provides 80% of process supervision for free. SystemAgentd is the *orchestration brain* (which agents, when, what config, what tools) -- NOT the process manager.

## Common Pitfalls

### Pitfall 1: READY=1 Before Server is Listening
**What goes wrong:** systemd shows "active" but socket connections fail.
**Why:** READY=1 sent during init, before `await server.start()` completes.
**Fix:** READY=1 ONLY after: socket listening + D-Bus exported + autostart agents launched.
**Detection:** `systemctl status` active but `socat` to socket fails.

### Pitfall 2: NotifyAccess Misconfiguration
**What goes wrong:** Agent processes send sd_notify but systemd ignores them.
**Why:** NotifyAccess=none/main only allows the main PID.
**Fix:** Use NotifyAccess=all on supervisor. Template units use NotifyAccess=main (each agent IS the main PID).
**Detection:** Unit stuck in "activating" until timeout.

### Pitfall 3: Watchdog Timeout During Heavy Work
**What goes wrong:** SIGABRT during model loading or core dump analysis.
**Why:** WatchdogSec too short or blocking operation prevents ping task.
**Fix:** (1) WatchdogSec >= 2x longest operation. (2) EXTEND_TIMEOUT_USEC= for known long ops. (3) Watchdog ping in dedicated asyncio task. (4) Never block the event loop.
**Detection:** SIGABRT + "watchdog timeout" in journal.

### Pitfall 4: Socket Activation FD Leak
**What goes wrong:** Child processes incorrectly think they are socket-activated.
**Why:** LISTEN_FDS/LISTEN_PID env vars inherited by children.
**Fix:** `os.environ.pop()` immediately after reading the FD env vars.
**Detection:** Children fail with "bad file descriptor."

### Pitfall 5: D-Bus System Bus Permission Denied
**What goes wrong:** Cannot own bus name on system bus.
**Why:** Missing policy XML in `/etc/dbus-1/system.d/`.
**Fix:** Ship `org.yaaos.AgentBus.conf` policy file with the pacman package.
**Detection:** `org.freedesktop.DBus.Error.AccessDenied` at startup.

### Pitfall 6: MemoryMax OOM-Kills Without Warning
**What goes wrong:** Agent killed instantly at hard memory limit.
**Why:** MemoryMax is a hard limit -- crossing it triggers OOM killer.
**Fix:** Set MemoryHigh (soft limit, throttles) BELOW MemoryMax. Example: MemoryHigh=768M, MemoryMax=1G gives 256M warning buffer. Agents should monitor via psutil.
**Detection:** "Out of memory" in journal, unit failed with signal 9.

### Pitfall 7: Blocking the asyncio Event Loop
**What goes wrong:** Everything hangs -- watchdog pings stop, connections time out.
**Why:** Synchronous I/O or subprocess calls blocking the event loop thread.
**Fix:** `asyncio.to_thread()` for blocking ops. `asyncio.create_subprocess_exec()` not `subprocess.run()`. Watchdog task serves as canary.
**Detection:** asyncio debug warnings, watchdog timeouts under load.

## Complete Daemon Entry Point Example

```python
"""SystemAgentd daemon -- complete entry point pattern."""
import asyncio
import functools
import os
import signal
import sys
from pathlib import Path

import sdnotify
import structlog

logger = structlog.get_logger()
notifier = sdnotify.SystemdNotifier()

async def run_daemon(config):
    """Main daemon coroutine following Model Bus pattern."""
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    reload_event = asyncio.Event()

    # Signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)
    loop.add_signal_handler(signal.SIGHUP, reload_event.set)

    # 1. Start JSON-RPC server on /run/yaaos/agentbus.sock
    notifier.notify("STATUS=Starting agent bus server...")
    server = JsonRpcServer(socket_path=config.socket_path)
    server.register("health", handle_health)
    server.register("agent.list", handle_agent_list)
    server.register("agent.start", handle_agent_start)
    server.register("agent.stop", handle_agent_stop)
    server.register("agent.status", handle_agent_status)
    server.register("agent.resources", handle_agent_resources)
    server.register("tools/list", handle_tools_list)
    server.register("tools/call", handle_tools_call)
    await server.start()

    # 2. Export D-Bus interface
    notifier.notify("STATUS=Connecting to D-Bus...")
    dbus_iface = AgentBusInterface()
    dbus_bus = await setup_dbus(dbus_iface)

    # 3. Start configured agents via systemd
    notifier.notify("STATUS=Starting agents...")
    for agent_name in config.autostart_agents:
        success = await start_agent(agent_name)
        if success:
            dbus_iface.AgentStarted(agent_name)

    # 4. Watchdog
    watchdog_usec = int(os.environ.get("WATCHDOG_USEC", 0))
    watchdog_task = None
    if watchdog_usec > 0:
        interval = watchdog_usec / 1_000_000 / 2
        watchdog_task = asyncio.create_task(_watchdog_loop(interval))

    # 5. READY!
    notifier.notify("READY=1")
    notifier.notify(f"STATUS=Managing {len(config.autostart_agents)} agents")
    logger.info("daemon.ready", agents=config.autostart_agents)

    # Main loop
    while not shutdown.is_set():
        reload_t = asyncio.create_task(reload_event.wait())
        shutdown_t = asyncio.create_task(shutdown.wait())
        done, pending = await asyncio.wait(
            [reload_t, shutdown_t], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

        if reload_event.is_set() and not shutdown.is_set():
            reload_event.clear()
            notifier.notify("RELOADING=1")
            await reload_config(config)
            notifier.notify("READY=1")

    # Shutdown
    notifier.notify("STOPPING=1")
    if watchdog_task:
        watchdog_task.cancel()

    for name in list(agent_registry.keys()):
        await stop_agent(name)
        dbus_iface.AgentStopped(name)

    dbus_bus.disconnect()
    await server.stop()
    logger.info("daemon.stopped")

def main():
    """CLI entry point for systemagentd."""
    from dotenv import load_dotenv
    load_dotenv()

    config = Config.load()
    configure_logging(config.log_level)

    try:
        asyncio.run(run_daemon(config))
    except KeyboardInterrupt:
        pass
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| cgroup v1 separate hierarchies | cgroup v2 unified hierarchy | Default on Arch ~2020 | Single hierarchy, systemd manages fully |
| pydbus (GLib-based) | dbus-next (pure Python, asyncio) | ~2020 | No GLib dep, native asyncio |
| dbus-python (C bindings) | dbus-next (pure Python) | ~2020 | No C compilation, asyncio-native |
| Type=forking + PID files | Type=notify + sd_notify | Always for modern daemons | Reliable readiness, no stale PIDs |
| syslog + logrotate | journald structured logging | systemd era | Indexed, queryable, auto-rotated |
| inotify for coredumps | systemd-coredump + .path units | systemd 215+ | Reliable, integrates with coredumpctl |
| Manual socket management | systemd socket activation | systemd 183+ | On-demand, FD preservation |

**Deprecated (do NOT use):**
- **pydbus:** Unmaintained since 2017, requires GLib.
- **dbus-python:** Legacy C bindings, not asyncio.
- **Type=forking:** No reason for Python daemons.
- **Direct /sys/fs/cgroup/ writes:** Breaks systemd delegation.
- **python-daemon library:** Not needed; systemd handles daemonization.

## Open Questions

1. **D-Bus vs Unix Socket for agent-to-agent communication**
   - What we know: D-Bus = broadcast signals + discovery. Unix sockets = point-to-point throughput.
   - What's unclear: Whether agents communicate directly or always via supervisor.
   - Recommendation: D-Bus for lifecycle events. Agent Bus socket for management. Agents use Model Bus + SFS sockets for AI/search.

2. **systemd-coredump integration for Crash-Agent**
   - What we know: Core dumps stored in `/var/lib/systemd/coredump/`, accessible via `coredumpctl`.
   - What's unclear: Best trigger mechanism -- .path unit, journal match, or handler script.
   - Recommendation: `.path` unit with `PathChanged=/var/lib/systemd/coredump/`.

3. **systemd-python vs sdnotify**
   - What we know: sdnotify is pure Python, already used by Model Bus. systemd-python requires system package but adds journal access.
   - Recommendation: Use both. sdnotify for sd_notify (pure Python, works everywhere). systemd-python for journal reading/writing only.

## Sources

### Primary (HIGH confidence)
- [sd_notify(3)](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html) - Complete notification protocol
- [systemd.service(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) - Type=notify, WatchdogSec, NotifyAccess, KillMode
- [systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html) - CPUQuota, MemoryMax, MemoryHigh, IOWeight, TasksMax
- [systemd.socket(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html) - Socket activation, Accept=, FileDescriptorName=
- [systemd.exec(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html) - RuntimeDirectory, StateDirectory, ProtectSystem, NoNewPrivileges
- [sd_listen_fds(3)](https://www.freedesktop.org/software/systemd/man/latest/sd_listen_fds.html) - SD_LISTEN_FDS_START=3, env vars
- [Python asyncio streams](https://docs.python.org/3/library/asyncio-stream.html) - start_unix_server, StreamReader/Writer
- [Python asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html) - add_signal_handler, create_subprocess_exec
- YAAOS Model Bus: `src/yaaos-modelbus/src/yaaos_modelbus/daemon.py` - Proven sd_notify + signal handling
- YAAOS Model Bus: `src/yaaos-modelbus/src/yaaos_modelbus/server.py` - Proven JSON-RPC + NDJSON server
- YAAOS Model Bus: `src/yaaos-modelbus/systemd/yaaos-modelbus.service` - Proven systemd unit config

### Secondary (MEDIUM confidence)
- [dbus-next docs](https://python-dbus-next.readthedocs.io/) - ServiceInterface, method/signal/property decorators, type system
- [python-systemd GitHub](https://github.com/systemd/python-systemd) - journal.send, JournalHandler, Reader
- [sdnotify GitHub](https://github.com/bb4242/sdnotify) - SystemdNotifier API (v0.3.2, stable)

### Tertiary (LOW confidence)
- None. All critical findings verified against official documentation.

## Metadata

**Confidence breakdown:**
- sd_notify protocol: HIGH - official man page verified
- Unix socket server: HIGH - Python docs + existing Model Bus code
- Signal handling: HIGH - Python asyncio docs + existing Model Bus code
- D-Bus integration: MEDIUM-HIGH - dbus-next docs verified; system bus permissions need runtime test
- cgroup v2 resource control: HIGH - official systemd.resource-control(5) verified
- Journald structured logging: HIGH - python-systemd GitHub docs verified
- Socket activation: MEDIUM-HIGH - protocol verified; Python FD passing needs runtime test

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable domain; systemd and asyncio APIs change rarely)
