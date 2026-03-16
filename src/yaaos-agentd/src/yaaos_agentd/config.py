"""Configuration loading for SystemAgentd.

Config priority: defaults < TOML file < environment variables.
Follows the same pattern as Model Bus config.py.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib as tomli
except ImportError:
    import tomli  # type: ignore[no-redef]

from yaaos_agentd.errors import ConfigError
from yaaos_agentd.types import AGENT_NAME_PATTERN, AgentSpec, RestartPolicy, RestartStrategy

_DEFAULT_CONFIG_PATH = Path("~/.config/yaaos/agentd.toml")
_DEFAULT_SOCKET_PATH = "/run/yaaos/agentbus.sock"
_FALLBACK_SOCKET_DIR = Path("~/.local/run/yaaos")

_DEFAULT_TOOL_DIRS = [
    Path("/etc/yaaos/tools.d"),
    Path("~/.config/yaaos/tools.d"),
]


@dataclass
class SupervisorConfig:
    """Supervisor-level configuration."""

    socket_path: Path = field(default_factory=lambda: Path(_DEFAULT_SOCKET_PATH))
    reconcile_interval_sec: float = 10.0
    max_restarts: int = 5
    max_restart_window_sec: float = 60.0
    log_level: str = "info"
    max_connections: int = 8
    restart_strategy: RestartStrategy = RestartStrategy.ONE_FOR_ONE
    allow_root_tools: bool = False


@dataclass
class Config:
    """SystemAgentd configuration."""

    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    tool_dirs: list[Path] = field(default_factory=lambda: list(_DEFAULT_TOOL_DIRS))

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config from TOML file with env var overrides.

        Falls back to defaults if no config file exists.
        """
        config_path = (path or _DEFAULT_CONFIG_PATH).expanduser()

        if config_path.exists():
            with open(config_path, "rb") as f:
                raw = tomli.load(f)
        else:
            raw = {}

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict) -> Config:
        """Build Config from a parsed TOML dict."""
        sup_raw = raw.get("supervisor", {})
        agents_raw = raw.get("agents", {})
        tool_dirs_raw = raw.get("tool_dirs", {})

        # Resolve socket path
        socket_str = sup_raw.get("socket_path", _DEFAULT_SOCKET_PATH)
        socket_path = Path(socket_str)
        if not _can_create_socket(socket_path):
            socket_path = _FALLBACK_SOCKET_DIR.expanduser() / "agentbus.sock"

        # Env override for socket path
        env_socket = os.environ.get("YAAOS_AGENTBUS_SOCKET")
        if env_socket:
            socket_path = Path(env_socket)

        supervisor = SupervisorConfig(
            socket_path=socket_path,
            reconcile_interval_sec=sup_raw.get("reconcile_interval_sec", 10.0),
            max_restarts=sup_raw.get("max_restarts", 5),
            max_restart_window_sec=sup_raw.get("max_restart_window_sec", 60.0),
            log_level=os.environ.get("YAAOS_AGENTD_LOG_LEVEL", sup_raw.get("log_level", "info")),
            max_connections=sup_raw.get("max_connections", 8),
            restart_strategy=RestartStrategy(sup_raw.get("restart_strategy", "one_for_one")),
            allow_root_tools=sup_raw.get("allow_root_tools", False),
        )

        # Parse agent specs
        agents = {}
        for name, agent_raw in agents_raw.items():
            agent = _parse_agent_spec(name, agent_raw, supervisor)
            agents[name] = agent

        # Parse tool directories
        tool_dirs = list(_DEFAULT_TOOL_DIRS)
        if "system" in tool_dirs_raw:
            tool_dirs[0] = Path(tool_dirs_raw["system"])
        if "user" in tool_dirs_raw:
            tool_dirs.append(Path(tool_dirs_raw["user"]).expanduser())

        return cls(
            supervisor=supervisor,
            agents=agents,
            tool_dirs=[d.expanduser() for d in tool_dirs],
        )


def _parse_agent_spec(name: str, raw: dict, supervisor: SupervisorConfig) -> AgentSpec:
    """Parse a single agent spec from TOML config."""
    # Validate agent name
    if not re.match(AGENT_NAME_PATTERN, name):
        raise ConfigError(
            f"Invalid agent name '{name}': must match {AGENT_NAME_PATTERN}",
            data={"agent": name, "pattern": AGENT_NAME_PATTERN},
        )

    # Parse restart policy
    policy_str = raw.get("restart_policy", "permanent")
    try:
        restart_policy = RestartPolicy(policy_str)
    except ValueError:
        raise ConfigError(
            f"Invalid restart_policy '{policy_str}' for agent '{name}'",
            data={"agent": name, "value": policy_str, "valid": [p.value for p in RestartPolicy]},
        )

    # Extract agent-specific config (keys that start with "config.")
    agent_config: dict = {}
    for key, value in raw.items():
        if key.startswith("config."):
            agent_config[key[7:]] = value  # Strip "config." prefix
    # Also accept a nested [agents.X.config] table
    if "config" in raw and isinstance(raw["config"], dict):
        agent_config.update(raw["config"])

    return AgentSpec(
        name=name,
        module=raw.get("module", f"yaaos_agentd.agents.{name}_agent"),
        enabled=raw.get("enabled", True),
        restart_policy=restart_policy,
        reconcile_interval_sec=raw.get("reconcile_interval_sec", supervisor.reconcile_interval_sec),
        max_restarts=raw.get("max_restarts", supervisor.max_restarts),
        max_restart_window_sec=raw.get("max_restart_window_sec", supervisor.max_restart_window_sec),
        resource_limits={
            k: v
            for k, v in raw.items()
            if k in ("cpu_quota", "memory_max", "memory_high", "io_weight", "tasks_max")
        },
        config=agent_config,
    )


def _can_create_socket(path: Path) -> bool:
    """Check if we can create a socket at the given path."""
    parent = path.parent
    try:
        if parent.exists():
            return os.access(parent, os.W_OK)
        return os.access(parent.parent, os.W_OK) if parent.parent.exists() else False
    except OSError:
        return False
