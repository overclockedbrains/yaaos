# YAAOS Development Commands

Quick reference for all build, test, lint, and run commands across the three YAAOS components.

## Prerequisites

Every command assumes this prefix (or export it once per session):

```bash
export PATH=$HOME/.local/bin:$PATH
```

Windows paths map to WSL as `/mnt/c/Aman/Coding-Bamzii/yaaos`.

Package manager: `uv` (installed at `~/.local/bin/uv`).

---

## Per-Component Roots

| Component | WSL Path |
|-----------|----------|
| SFS | `/mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-sfs` |
| ModelBus | `/mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-modelbus` |
| AgentD | `/mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-agentd` |

All commands below assume you `cd` into the component root first.

---

## Install / Sync Dependencies

```bash
# Sync all deps including dev extras
uv sync --extra dev

# SFS with all optional extras (docs, media, code, modelbus)
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-sfs
uv sync --extra dev --extra all

# AgentD with systemd/nvidia extras
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-agentd
uv sync --extra dev --extra systemd

# Lock deps (resolves all extras — avoid if systemd-python headers missing)
uv lock
```

**Known issue:** `uv lock` in agentd tries to build `systemd-python` which needs `libsystemd-dev` headers. The journal extra is commented out in pyproject.toml. On Arch target: `pacman -S python-systemd`.

---

## Testing

```bash
# Run all tests (verbose, short tracebacks)
uv run python -m pytest tests/ -v --tb=short

# Single test file
uv run python -m pytest tests/test_server.py -v --tb=short

# Single test class/method
uv run python -m pytest tests/test_supervisor.py::TestSupervisor::test_crash_loop_detection -v

# With output capture disabled (see print statements)
uv run python -m pytest tests/ -v --tb=short -s

# Only integration-marked tests
uv run python -m pytest tests/ -v --tb=short -m integration
```

### From Windows (calling WSL inline)

```cmd
wsl -e bash -c "export PATH=$HOME/.local/bin:$PATH && cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-agentd && uv run python -m pytest tests/ -v --tb=short 2>&1"
```

Replace `yaaos-agentd` with `yaaos-sfs` or `yaaos-modelbus` for other components.

---

## Linting (Ruff)

```bash
# Check for lint errors
uv run ruff check src/ tests/

# Auto-fix what ruff can
uv run ruff check src/ tests/ --fix

# Format code
uv run ruff format src/ tests/
```

---

## Running Daemons

### SFS Daemon
```bash
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-sfs
uv run yaaos-sfs
```
Watches `~/semantic`, indexes files, serves queries on port 9749.

### SFS CLI (search)
```bash
uv run yaaos-find "search query"
uv run yaaos-find --status
uv run yaaos-find "python database" --type py
uv run yaaos-find "report" --type pdf,docx --top 5
```

### ModelBus Daemon
```bash
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-modelbus
uv run yaaos-modelbusd
```
Socket: `~/.local/run/yaaos/modelbus.sock` (fallback when `/run/yaaos/` not writable).

### ModelBus CLI
```bash
uv run yaaos-bus health
uv run yaaos-bus models
uv run yaaos-bus config get
```

### SystemAgentd Daemon
```bash
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-agentd
uv run systemagentd
uv run systemagentd --config /path/to/custom/agentd.toml
```
Socket: `~/.local/run/yaaos/agentbus.sock`.

### SystemAgentd CLI
```bash
uv run systemagentctl status
uv run systemagentctl agent log
uv run systemagentctl start log
uv run systemagentctl stop log
uv run systemagentctl restart resource
uv run systemagentctl logs log --lines 20
uv run systemagentctl tools list
uv run systemagentctl tools schema docker
uv run systemagentctl tools invoke docker ps '{}'
uv run systemagentctl reload
```

### Single Agent Runner (standalone mode for systemd template units)
```bash
uv run yaaos-agent log
uv run yaaos-agent crash --config /path/to/agentd.toml
```

---

## Quick Smoke Tests (daemon up/down verification)

```bash
# Start daemon in background, verify socket exists, then kill
cd /mnt/c/Aman/Coding-Bamzii/yaaos/src/yaaos-agentd
uv run systemagentd &
DAEMON_PID=$!
sleep 2
ls -la ~/.local/run/yaaos/agentbus.sock
uv run systemagentctl status
kill $DAEMON_PID
wait $DAEMON_PID 2>/dev/null
# Socket should be cleaned up
ls ~/.local/run/yaaos/agentbus.sock 2>&1  # should say "No such file"
```

---

## Module Import Checks

```bash
# Verify all modules load without errors
uv run python -c "from yaaos_agentd.supervisor import Supervisor; print('supervisor OK')"
uv run python -c "from yaaos_agentd.server import AgentBusServer; print('server OK')"
uv run python -c "from yaaos_agentd.tools.registry import ToolRegistry; print('registry OK')"
uv run python -c "from yaaos_agentd.state import AgentStateDB; print('statedb OK')"
uv run python -c "from yaaos_agentd.systemd import SystemdManager; print('systemd OK')"
uv run python -c "from yaaos_agentd.agents.log_agent import LogAgent; print('log OK')"
uv run python -c "from yaaos_agentd.agents.crash_agent import CrashAgent; print('crash OK')"
uv run python -c "from yaaos_agentd.agents.resource_agent import ResourceAgent; print('resource OK')"
uv run python -c "from yaaos_agentd.agents.net_agent import NetAgent; print('net OK')"
uv run python -c "from yaaos_agentd.agents.fs_agent import FsAgent; print('fs OK')"
```

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `YAAOS_MODELBUS_SOCKET` | Override ModelBus socket path | `/tmp/test-modelbus.sock` |
| `YAAOS_AGENTBUS_SOCKET` | Override AgentBus socket path | `/tmp/test-agentbus.sock` |
| `YAAOS_AGENTD_LOG_LEVEL` | Override agentd log level | `debug` |
| `OPENAI_API_KEY` | OpenAI provider | (your key) |
| `ANTHROPIC_API_KEY` | Anthropic provider | (your key) |
| `VOYAGE_API_KEY` | Voyage provider | (your key) |

---

## Config File Locations

| Config | Default Path |
|--------|-------------|
| SFS | `~/.config/yaaos/config.toml` |
| ModelBus | `~/.config/yaaos/modelbus.toml` |
| AgentD | `~/.config/yaaos/agentd.toml` |
| Tool manifests | `/etc/yaaos/tools.d/` and `~/.config/yaaos/tools.d/` |

---

## Common Gotchas

1. **`uv lock` fails on systemd-python**: Headers missing. Ignore — journal extra is commented out. Install natively on Arch: `pacman -S python-systemd`.
2. **Socket permission denied**: `/run/yaaos/` needs root. Daemons auto-fallback to `~/.local/run/yaaos/`.
3. **SFS integration tests slow**: First run downloads `all-MiniLM-L6-v2` (~90MB). Subsequent runs use cache.
4. **dbus-next tests on WSL**: D-Bus session bus may not be running. Tests mock it — real D-Bus needs `dbus-daemon --session` running.
5. **Windows line endings**: If ruff/pytest behave oddly, check `git config core.autocrlf`. Set to `input` for WSL compatibility.
