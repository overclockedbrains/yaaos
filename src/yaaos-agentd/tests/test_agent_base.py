"""Tests for BaseAgent lifecycle and observe/reason/act cycle."""

from __future__ import annotations

import asyncio

import pytest

from yaaos_agentd.types import Action, AgentState

# Fixtures from conftest
from .conftest import DummyAgent


class TestAgentLifecycle:
    @pytest.mark.asyncio
    async def test_run_single_cycle(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        await agent.run_cycle()
        assert agent.status.cycle_count == 1
        assert agent.status.error_count == 0
        assert len(agent.observations) == 1
        assert len(agent.actions_taken) == 1

    @pytest.mark.asyncio
    async def test_run_multiple_cycles(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        for _ in range(5):
            await agent.run_cycle()
        assert agent.status.cycle_count == 5
        assert len(agent.observations) == 5

    @pytest.mark.asyncio
    async def test_cycle_error_increments_error_count(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        agent.fail_on_cycle = 0
        with pytest.raises(RuntimeError, match="Simulated crash"):
            await agent.run_cycle()
        assert agent.status.error_count == 1
        assert agent.status.last_error is not None

    @pytest.mark.asyncio
    async def test_run_loop_starts_and_stops(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        task = asyncio.create_task(agent.run_loop())

        # Let it run a few cycles
        await asyncio.sleep(0.5)
        agent.request_stop()
        await asyncio.wait_for(task, timeout=2.0)

        assert agent.started is True
        assert agent.stopped is True
        assert agent.status.state == AgentState.STOPPED
        assert agent.status.cycle_count > 0

    @pytest.mark.asyncio
    async def test_run_loop_cancellation(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        task = asyncio.create_task(agent.run_loop())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert agent.status.state == AgentState.STOPPED

    @pytest.mark.asyncio
    async def test_on_start_called(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        task = asyncio.create_task(agent.run_loop())
        await asyncio.sleep(0.2)
        assert agent.started is True
        agent.request_stop()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_on_stop_called(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        task = asyncio.create_task(agent.run_loop())
        await asyncio.sleep(0.2)
        agent.request_stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert agent.stopped is True

    @pytest.mark.asyncio
    async def test_empty_actions_no_act_called(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        agent.cycle_callback = lambda obs: []  # Return no actions
        await agent.run_cycle()
        assert agent.status.cycle_count == 1
        assert len(agent.actions_taken) == 0

    @pytest.mark.asyncio
    async def test_status_tracking(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        assert agent.status.state == AgentState.SPEC_ONLY

        task = asyncio.create_task(agent.run_loop())
        await asyncio.sleep(0.3)
        assert agent.status.state == AgentState.RUNNING
        assert agent.status.last_cycle_at is not None

        agent.request_stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert agent.status.state == AgentState.STOPPED


class TestAgentHealth:
    @pytest.mark.asyncio
    async def test_healthy_agent(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        task = asyncio.create_task(agent.run_loop())
        await asyncio.sleep(0.3)
        assert agent.is_healthy() is True
        agent.request_stop()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_stopped_agent_not_healthy(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        assert agent.is_healthy() is False  # SPEC_ONLY state

    @pytest.mark.asyncio
    async def test_custom_reason_with_actions(self, dummy_spec):
        agent = DummyAgent(dummy_spec)
        agent.cycle_callback = lambda obs: [
            Action(tool="docker", action="ps", description="List containers"),
            Action(tool="systemctl", action="status", params={"unit": "sshd"}),
        ]
        await agent.run_cycle()
        assert len(agent.actions_taken[0]) == 2
        assert agent.status.last_action is not None
