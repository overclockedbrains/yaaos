"""Tests for the OTP-style supervisor — restart strategies, intensity limits, reconciliation."""

from __future__ import annotations

import asyncio
import time

import pytest

from yaaos_agentd.config import Config, SupervisorConfig
from yaaos_agentd.errors import AgentNotFoundError
from yaaos_agentd.supervisor import AgentHandle, RestartIntensityLimiter, Supervisor
from yaaos_agentd.types import AgentSpec, AgentState, RestartPolicy

from .conftest import DummyAgent


# ── RestartIntensityLimiter Tests ───────────────────────────────


class TestRestartIntensityLimiter:
    def test_allows_restart_under_limit(self):
        limiter = RestartIntensityLimiter(max_restarts=3, period_seconds=60.0)
        assert limiter.can_restart() is True
        limiter.record_restart()
        assert limiter.can_restart() is True
        limiter.record_restart()
        assert limiter.can_restart() is True

    def test_blocks_restart_at_limit(self):
        limiter = RestartIntensityLimiter(max_restarts=3, period_seconds=60.0)
        for _ in range(3):
            limiter.record_restart()
        assert limiter.can_restart() is False

    def test_window_expiry(self):
        limiter = RestartIntensityLimiter(max_restarts=2, period_seconds=0.2)
        limiter.record_restart()
        limiter.record_restart()
        assert limiter.can_restart() is False

        # Wait for window to expire
        time.sleep(0.3)
        assert limiter.can_restart() is True

    def test_restart_count(self):
        limiter = RestartIntensityLimiter(max_restarts=5, period_seconds=60.0)
        assert limiter.restart_count == 0
        limiter.record_restart()
        limiter.record_restart()
        assert limiter.restart_count == 2

    def test_reset(self):
        limiter = RestartIntensityLimiter(max_restarts=2, period_seconds=60.0)
        limiter.record_restart()
        limiter.record_restart()
        assert limiter.can_restart() is False
        limiter.reset()
        assert limiter.can_restart() is True
        assert limiter.restart_count == 0


# ── AgentHandle Tests ───────────────────────────────────────────


class TestAgentHandle:
    def test_not_running_without_task(self):
        spec = AgentSpec(name="test", module="test.module")
        handle = AgentHandle(spec=spec)
        assert handle.is_running is False

    def test_limiter_initialized_from_spec(self):
        spec = AgentSpec(
            name="test",
            module="test.module",
            max_restarts=10,
            max_restart_window_sec=120.0,
        )
        handle = AgentHandle(spec=spec)
        assert handle.limiter._max_restarts == 10
        assert handle.limiter._period == 120.0


# ── Supervisor Tests ────────────────────────────────────────────


class TestSupervisor:
    def _sup(self, agents: dict[str, AgentSpec]) -> Supervisor:
        config = Config(
            supervisor=SupervisorConfig(reconcile_interval_sec=0.1, log_level="debug"),
            agents=agents,
        )
        return Supervisor(config, agent_class_override=DummyAgent)

    def _spec(self, name: str = "test-agent", **kwargs) -> AgentSpec:
        defaults = dict(module="tests.conftest", reconcile_interval_sec=0.1)
        defaults.update(kwargs)
        return AgentSpec(name=name, **defaults)

    @pytest.mark.asyncio
    async def test_reconcile_starts_agents(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()
        assert "test-agent" in sup.handles
        assert sup.handles["test-agent"].is_running
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_reconcile_stops_removed_agents(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()
        assert "test-agent" in sup.handles

        sup.config = Config(
            supervisor=SupervisorConfig(reconcile_interval_sec=0.1),
            agents={},
        )
        await sup.reconcile()
        assert "test-agent" not in sup.handles

    @pytest.mark.asyncio
    async def test_reconcile_restarts_crashed_permanent(self):
        spec = self._spec(restart_policy=RestartPolicy.PERMANENT, max_restarts=5)
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()

        # Simulate crash
        handle = sup.handles["test-agent"]
        if handle.task:
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
        handle.status.state = AgentState.FAILED

        await sup.reconcile()
        assert sup._total_restarts >= 1
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_temporary_agent_not_restarted(self):
        spec = self._spec(restart_policy=RestartPolicy.TEMPORARY)
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()

        handle = sup.handles["test-agent"]
        if handle.task:
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
        handle.status.state = AgentState.FAILED

        restarts_before = sup._total_restarts
        await sup.reconcile()
        assert sup._total_restarts == restarts_before
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_transient_agent_not_restarted_on_clean_exit(self):
        spec = self._spec(restart_policy=RestartPolicy.TRANSIENT)
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()

        handle = sup.handles["test-agent"]
        if handle.task:
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
        handle.status.state = AgentState.STOPPED

        restarts_before = sup._total_restarts
        await sup.reconcile()
        assert sup._total_restarts == restarts_before
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_crash_loop_detection(self):
        spec = self._spec(
            restart_policy=RestartPolicy.PERMANENT,
            max_restarts=2,
            max_restart_window_sec=60.0,
        )
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()

        # Simulate multiple crashes exceeding the limit
        for _ in range(3):
            handle = sup.handles["test-agent"]
            if handle.task and not handle.task.done():
                handle.task.cancel()
                try:
                    await handle.task
                except asyncio.CancelledError:
                    pass
            handle.status.state = AgentState.FAILED
            await sup.reconcile()
            await asyncio.sleep(0.05)

        handle = sup.handles["test-agent"]
        assert handle.status.state == AgentState.CRASH_LOOP
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_manual_start_agent(self):
        spec = self._spec(enabled=False)
        sup = self._sup({"test-agent": spec})

        await sup.start_agent("test-agent")
        assert "test-agent" in sup.handles
        assert sup.handles["test-agent"].is_running
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_manual_stop_agent(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()
        assert "test-agent" in sup.handles

        await sup.stop_agent("test-agent")
        assert "test-agent" not in sup.handles

    @pytest.mark.asyncio
    async def test_manual_restart_resets_limiter(self):
        spec = self._spec(max_restarts=2)
        sup = self._sup({"test-agent": spec})
        await sup.reconcile()

        handle = sup.handles["test-agent"]
        handle.limiter.record_restart()
        handle.limiter.record_restart()
        assert handle.limiter.can_restart() is False

        await sup.restart_agent("test-agent")
        new_handle = sup.handles["test-agent"]
        assert new_handle.limiter.can_restart() is True
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_start_nonexistent_agent_raises(self):
        sup = self._sup({})
        with pytest.raises(AgentNotFoundError):
            await sup.start_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_stop_nonexistent_agent_raises(self):
        sup = self._sup({})
        with pytest.raises(AgentNotFoundError):
            await sup.stop_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_health_reporting(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})
        sup._started_at = time.monotonic()

        await sup.reconcile()
        await asyncio.sleep(0.2)

        health = sup.get_health()
        assert health.status == "healthy"
        assert health.agent_count == 1
        assert health.agents_running == 1
        assert health.agents_failed == 0
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_get_agent_status(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})

        await sup.reconcile()
        await asyncio.sleep(0.3)

        status = sup.get_agent_status("test-agent")
        assert status is not None
        assert status.name == "test-agent"
        assert status.cycle_count > 0
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_get_all_statuses(self):
        spec1 = self._spec("agent-a")
        spec2 = self._spec("agent-b")
        sup = self._sup({"agent-a": spec1, "agent-b": spec2})

        await sup.reconcile()
        await asyncio.sleep(0.2)

        statuses = sup.get_all_statuses()
        assert len(statuses) == 2
        assert "agent-a" in statuses
        assert "agent-b" in statuses
        await sup.shutdown()

    @pytest.mark.asyncio
    async def test_disabled_agent_not_started(self):
        spec = self._spec(enabled=False)
        sup = self._sup({"test-agent": spec})

        await sup.reconcile()
        assert "test-agent" not in sup.handles

    @pytest.mark.asyncio
    async def test_shutdown_stops_all_agents(self):
        spec1 = self._spec("agent-a")
        spec2 = self._spec("agent-b")
        sup = self._sup({"agent-a": spec1, "agent-b": spec2})

        await sup.reconcile()
        assert len(sup.handles) == 2

        await sup.shutdown()
        assert len(sup.handles) == 0

    @pytest.mark.asyncio
    async def test_health_degraded_when_agent_failed(self):
        spec = self._spec()
        sup = self._sup({"test-agent": spec})
        sup._started_at = time.monotonic()

        await sup.reconcile()
        handle = sup.handles["test-agent"]
        if handle.task:
            handle.task.cancel()
            try:
                await handle.task
            except asyncio.CancelledError:
                pass
        handle.status.state = AgentState.FAILED

        health = sup.get_health()
        assert health.status == "degraded"
        assert health.agents_failed == 1
        await sup.shutdown()
