"""Tests for core data types."""

from __future__ import annotations

import re
import time

from yaaos_agentd.types import (
    AGENT_NAME_PATTERN,
    Action,
    ActionResult,
    AgentSpec,
    AgentState,
    AgentStatus,
    RestartPolicy,
    SupervisorHealth,
    ToolResult,
)


class TestAgentState:
    def test_all_states_have_string_values(self):
        for state in AgentState:
            assert isinstance(state.value, str)

    def test_state_transitions(self):
        """Verify key states exist for the lifecycle."""
        assert AgentState.SPEC_ONLY.value == "spec_only"
        assert AgentState.STARTING.value == "starting"
        assert AgentState.RUNNING.value == "running"
        assert AgentState.DEGRADED.value == "degraded"
        assert AgentState.STOPPING.value == "stopping"
        assert AgentState.STOPPED.value == "stopped"
        assert AgentState.FAILED.value == "failed"
        assert AgentState.CRASH_LOOP.value == "crash_loop"


class TestRestartPolicy:
    def test_all_policies(self):
        assert RestartPolicy.PERMANENT.value == "permanent"
        assert RestartPolicy.TRANSIENT.value == "transient"
        assert RestartPolicy.TEMPORARY.value == "temporary"

    def test_from_string(self):
        assert RestartPolicy("permanent") == RestartPolicy.PERMANENT
        assert RestartPolicy("transient") == RestartPolicy.TRANSIENT
        assert RestartPolicy("temporary") == RestartPolicy.TEMPORARY


class TestAgentSpec:
    def test_defaults(self):
        spec = AgentSpec(name="test", module="test.module")
        assert spec.enabled is True
        assert spec.restart_policy == RestartPolicy.PERMANENT
        assert spec.reconcile_interval_sec == 30.0
        assert spec.max_restarts == 5
        assert spec.resource_limits == {}
        assert spec.config == {}

    def test_to_dict(self):
        spec = AgentSpec(
            name="log",
            module="yaaos_agentd.agents.log_agent",
            restart_policy=RestartPolicy.PERMANENT,
            reconcile_interval_sec=5.0,
            config={"units": ["sshd"]},
        )
        d = spec.to_dict()
        assert d["name"] == "log"
        assert d["restart_policy"] == "permanent"
        assert d["reconcile_interval_sec"] == 5.0
        assert d["config"] == {"units": ["sshd"]}

    def test_frozen(self):
        spec = AgentSpec(name="test", module="test.module")
        try:
            spec.name = "changed"  # type: ignore
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass


class TestAgentStatus:
    def test_defaults(self):
        status = AgentStatus(name="test")
        assert status.state == AgentState.SPEC_ONLY
        assert status.pid is None
        assert status.cycle_count == 0
        assert status.error_count == 0

    def test_to_dict_minimal(self):
        status = AgentStatus(name="test")
        d = status.to_dict()
        assert d["name"] == "test"
        assert d["state"] == "spec_only"
        assert "pid" not in d

    def test_to_dict_full(self):
        status = AgentStatus(
            name="log",
            state=AgentState.RUNNING,
            pid=1234,
            cycle_count=100,
            error_count=2,
            started_at=time.monotonic() - 60,
            last_cycle_at=time.monotonic() - 5,
            last_error="connection refused",
            last_action="systemctl.status",
            memory_bytes=50 * 1024 * 1024,
            cpu_percent=1.5,
        )
        d = status.to_dict()
        assert d["pid"] == 1234
        assert d["cycle_count"] == 100
        assert d["error_count"] == 2
        assert d["uptime_sec"] > 59
        assert d["last_cycle_ago_sec"] < 10
        assert d["last_error"] == "connection refused"
        assert d["memory_mb"] == 50.0


class TestAction:
    def test_to_dict(self):
        action = Action(tool="docker", action="ps", params={"all": True})
        d = action.to_dict()
        assert d["tool"] == "docker"
        assert d["action"] == "ps"
        assert d["params"] == {"all": True}

    def test_to_dict_minimal(self):
        action = Action(tool="test", action="noop")
        d = action.to_dict()
        assert "params" not in d
        assert "description" not in d


class TestActionResult:
    def test_to_dict(self):
        action = Action(tool="git", action="status")
        result = ActionResult(action=action, success=True, output="clean", duration_ms=42.5)
        d = result.to_dict()
        assert d["success"] is True
        assert d["duration_ms"] == 42.5
        assert d["action"]["tool"] == "git"


class TestToolResult:
    def test_to_dict(self):
        result = ToolResult(
            exit_code=0,
            stdout="output",
            stderr="",
            duration_ms=100.0,
            is_error=False,
        )
        d = result.to_dict()
        assert d["exit_code"] == 0
        assert d["is_error"] is False


class TestSupervisorHealth:
    def test_to_dict(self):
        health = SupervisorHealth(
            status="healthy",
            uptime_sec=3600.0,
            agent_count=5,
            agents_running=5,
            agents_failed=0,
            agents_degraded=0,
            total_cycles=1000,
            total_restarts=3,
        )
        d = health.to_dict()
        assert d["status"] == "healthy"
        assert d["agent_count"] == 5
        assert d["total_restarts"] == 3


class TestAgentNamePattern:
    def test_valid_names(self):
        for name in ["log", "crash-agent", "net", "resource", "fs", "a1", "test-123"]:
            assert re.match(AGENT_NAME_PATTERN, name), f"'{name}' should be valid"

    def test_invalid_names(self):
        for name in ["Log", "CRASH", "123-agent", "-bad", "has.dot", "has/slash", ""]:
            assert not re.match(AGENT_NAME_PATTERN, name), f"'{name}' should be invalid"
