"""Shared test fixtures for SystemAgentd tests."""

from __future__ import annotations

from typing import Any

import pytest

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.config import Config, SupervisorConfig
from yaaos_agentd.types import Action, ActionResult, AgentSpec, RestartPolicy


class DummyAgent(BaseAgent):
    """A minimal agent for testing the supervisor and agent lifecycle."""

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self.observations: list[dict] = []
        self.actions_taken: list[list[Action]] = []
        self.cycle_callback: Any = None  # Optional callback per cycle
        self.fail_on_cycle: int | None = None  # Crash on this cycle number
        self.started = False
        self.stopped = False

    async def observe(self) -> dict:
        obs = {"cycle": self._status.cycle_count, "timestamp": "now"}
        self.observations.append(obs)
        return obs

    async def reason(self, observation: dict) -> list[Action]:
        if self.fail_on_cycle is not None and observation["cycle"] >= self.fail_on_cycle:
            raise RuntimeError(f"Simulated crash on cycle {observation['cycle']}")

        if self.cycle_callback:
            return self.cycle_callback(observation)

        return [
            Action(
                tool="test",
                action="noop",
                description=f"Test action cycle {observation['cycle']}",
            )
        ]

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        self.actions_taken.append(actions)
        return [
            ActionResult(
                action=a,
                success=True,
                output="ok",
                duration_ms=1.0,
            )
            for a in actions
        ]

    async def on_start(self) -> None:
        self.started = True

    async def on_stop(self) -> None:
        self.stopped = True


class CrashingAgent(BaseAgent):
    """An agent that crashes immediately for testing restart logic.

    Raises in observe(), but run_loop catches cycle errors and continues.
    Use FatalCrashAgent for tests that need the agent task to actually exit.
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self.crash_count = 0

    async def observe(self) -> dict:
        self.crash_count += 1
        raise RuntimeError(f"Intentional crash #{self.crash_count}")

    async def reason(self, observation: dict) -> list[Action]:
        return []

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        return []


class FatalCrashAgent(BaseAgent):
    """An agent that fatally crashes during startup — task exits immediately.

    Raises in on_start(), which is outside the cycle error handler in run_loop().
    The exception propagates to _run_agent_supervised(), which sets FAILED state
    and completes the task. This lets the supervisor detect the exit and trigger
    restart logic on the next reconcile cycle.
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)

    async def on_start(self) -> None:
        raise RuntimeError("Fatal startup crash")

    async def observe(self) -> dict:
        return {}

    async def reason(self, observation: dict) -> list[Action]:
        return []

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        return []


@pytest.fixture
def dummy_spec() -> AgentSpec:
    """A minimal agent spec for testing."""
    return AgentSpec(
        name="test-agent",
        module="tests.conftest",
        reconcile_interval_sec=0.1,
        max_restarts=3,
        max_restart_window_sec=10.0,
    )


@pytest.fixture
def crash_spec() -> AgentSpec:
    """An agent spec for the crashing agent."""
    return AgentSpec(
        name="crash-agent",
        module="tests.conftest",
        restart_policy=RestartPolicy.PERMANENT,
        reconcile_interval_sec=0.1,
        max_restarts=3,
        max_restart_window_sec=10.0,
    )


@pytest.fixture
def tmp_socket(tmp_path):
    """Return a temporary Unix socket path."""
    return tmp_path / "test_agentbus.sock"


@pytest.fixture
def test_config(tmp_socket, dummy_spec) -> Config:
    """Return a Config with a dummy agent for testing."""
    return Config(
        supervisor=SupervisorConfig(
            socket_path=tmp_socket,
            reconcile_interval_sec=0.1,
            log_level="debug",
        ),
        agents={"test-agent": dummy_spec},
    )
