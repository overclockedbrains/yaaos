"""Core data types for SystemAgentd."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(Enum):
    """Agent lifecycle states.

    Inspired by s6's distinction between 'up' (running) and 'ready' (functional),
    and Erlang/OTP child process states.
    """

    SPEC_ONLY = "spec_only"  # Config loaded, not started
    STARTING = "starting"  # Process starting, waiting for READY
    RUNNING = "running"  # Healthy, passing health checks
    DEGRADED = "degraded"  # Running but health checks failing
    STOPPING = "stopping"  # Graceful shutdown in progress
    STOPPED = "stopped"  # Clean exit
    FAILED = "failed"  # Crashed, eligible for restart
    CRASH_LOOP = "crash_loop"  # Exceeded restart intensity, needs manual intervention


class RestartStrategy(Enum):
    """OTP-inspired supervisor restart strategy.

    one_for_one: If one agent crashes, only that agent restarts.
    rest_for_one: If one agent crashes, restart it and all agents started after it.
    """

    ONE_FOR_ONE = "one_for_one"
    REST_FOR_ONE = "rest_for_one"


class RestartPolicy(Enum):
    """OTP-inspired restart policy for agents.

    permanent: Always restart (default for long-running agents).
    transient: Restart only on abnormal exit (non-zero, signal).
    temporary: Never restart (one-shot tasks like Crash-Agent analysis).
    """

    PERMANENT = "permanent"
    TRANSIENT = "transient"
    TEMPORARY = "temporary"


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Agent definition loaded from configuration.

    Immutable specification — changes require config reload.
    """

    name: str
    module: str  # e.g. "yaaos_agentd.agents.log_agent"
    enabled: bool = True
    restart_policy: RestartPolicy = RestartPolicy.PERMANENT
    reconcile_interval_sec: float = 30.0
    max_restarts: int = 5
    max_restart_window_sec: float = 60.0
    resource_limits: dict[str, Any] = field(default_factory=dict)  # CPUQuota, MemoryMax
    config: dict[str, Any] = field(default_factory=dict)  # Agent-specific config

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "module": self.module,
            "enabled": self.enabled,
            "restart_policy": self.restart_policy.value,
            "reconcile_interval_sec": self.reconcile_interval_sec,
            "max_restarts": self.max_restarts,
            "max_restart_window_sec": self.max_restart_window_sec,
            "resource_limits": self.resource_limits,
            "config": self.config,
        }


@dataclass(slots=True)
class AgentStatus:
    """Runtime status of a managed agent."""

    name: str
    state: AgentState = AgentState.SPEC_ONLY
    pid: int | None = None
    uptime_sec: float = 0.0
    cycle_count: int = 0
    error_count: int = 0
    last_cycle_at: float | None = None
    last_error: str | None = None
    last_action: str | None = None
    memory_bytes: int | None = None
    cpu_percent: float | None = None
    started_at: float | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "state": self.state.value,
            "cycle_count": self.cycle_count,
            "error_count": self.error_count,
        }
        if self.pid is not None:
            d["pid"] = self.pid
        if self.started_at is not None:
            d["uptime_sec"] = round(time.monotonic() - self.started_at, 1)
        if self.last_cycle_at is not None:
            d["last_cycle_ago_sec"] = round(time.monotonic() - self.last_cycle_at, 1)
        if self.last_error is not None:
            d["last_error"] = self.last_error
        if self.last_action is not None:
            d["last_action"] = self.last_action
        if self.memory_bytes is not None:
            d["memory_mb"] = round(self.memory_bytes / (1024 * 1024), 1)
        if self.cpu_percent is not None:
            d["cpu_percent"] = round(self.cpu_percent, 1)
        return d


@dataclass(frozen=True, slots=True)
class Action:
    """A planned action from an agent's reason() phase."""

    tool: str  # Tool name from registry, or built-in action
    action: str  # Specific action (e.g. "ps", "status", "alert")
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        d: dict = {"tool": self.tool, "action": self.action}
        if self.params:
            d["params"] = self.params
        if self.description:
            d["description"] = self.description
        return d


@dataclass(slots=True)
class ActionResult:
    """Result of executing an action."""

    action: Action
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        d: dict = {
            "action": self.action.to_dict(),
            "success": self.success,
            "duration_ms": round(self.duration_ms, 1),
        }
        if self.output:
            d["output"] = self.output
        if self.error:
            d["error"] = self.error
        return d


@dataclass(slots=True)
class ToolResult:
    """Result of a tool invocation via the Tool Registry."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    is_error: bool

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": round(self.duration_ms, 1),
            "is_error": self.is_error,
        }


@dataclass(frozen=True, slots=True)
class SupervisorHealth:
    """Health status of the supervisor daemon."""

    status: str  # "healthy" | "degraded" | "unhealthy"
    uptime_sec: float
    agent_count: int
    agents_running: int
    agents_failed: int
    agents_degraded: int
    total_cycles: int = 0
    total_restarts: int = 0
    process_memory_mb: float | None = None
    process_cpu_percent: float | None = None
    pid: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "status": self.status,
            "uptime_sec": round(self.uptime_sec, 1),
            "agent_count": self.agent_count,
            "agents_running": self.agents_running,
            "agents_failed": self.agents_failed,
            "agents_degraded": self.agents_degraded,
            "total_cycles": self.total_cycles,
            "total_restarts": self.total_restarts,
        }
        if self.pid is not None:
            d["pid"] = self.pid
        if self.process_memory_mb is not None:
            d["process_memory_mb"] = round(self.process_memory_mb, 1)
        if self.process_cpu_percent is not None:
            d["process_cpu_percent"] = round(self.process_cpu_percent, 1)
        return d


# Valid agent name pattern: lowercase alphanumeric + hyphens
# Prevents systemd template %i escaping issues
AGENT_NAME_PATTERN = r"^[a-z][a-z0-9-]*$"
