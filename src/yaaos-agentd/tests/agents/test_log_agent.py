"""Tests for Log-Agent — journald analysis and anomaly detection."""

from __future__ import annotations

import time

import pytest

from yaaos_agentd.agents.log_agent import LogAgent, UnitStats, _CRITICAL_PATTERNS
from yaaos_agentd.types import AgentSpec


def _log_spec(**overrides) -> AgentSpec:
    defaults = {
        "name": "log",
        "module": "yaaos_agentd.agents.log_agent",
        "reconcile_interval_sec": 1.0,
        "config": {},
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


class TestUnitStats:
    def test_rate_per_min_empty(self):
        stats = UnitStats()
        assert stats.rate_per_min == 0.0

    def test_rate_per_min_with_entries(self):
        stats = UnitStats()
        now = time.monotonic()
        # Simulate 60 entries over 60 seconds = 60/min
        for i in range(60):
            stats.timestamps.append(now - 60 + i)
        rate = stats.rate_per_min
        assert rate > 0

    def test_entry_count(self):
        stats = UnitStats()
        stats.entry_count = 42
        assert stats.entry_count == 42


class TestLogAgentObserve:
    @pytest.mark.asyncio
    async def test_observe_no_journal(self):
        """Without journald, observe returns empty entries."""
        agent = LogAgent(_log_spec())
        obs = await agent.observe()
        assert obs["entry_count"] == 0
        assert obs["units_seen"] == []

    @pytest.mark.asyncio
    async def test_observe_with_mocked_journal(self):
        """With a mock journal reader, entries are collected."""
        spec = _log_spec(config={"units": ["test.service"]})
        agent = LogAgent(spec)

        # Mock journal reader as an iterable
        mock_entries = [
            {
                "_SYSTEMD_UNIT": "test.service",
                "MESSAGE": "Something happened",
                "PRIORITY": 6,
            },
            {
                "_SYSTEMD_UNIT": "test.service",
                "MESSAGE": "Error: Connection refused",
                "PRIORITY": 3,
            },
        ]
        agent._journal_reader = iter(mock_entries)

        obs = await agent.observe()
        assert obs["entry_count"] == 2
        assert "test.service" in obs["units_seen"]


class TestLogAgentReason:
    @pytest.mark.asyncio
    async def test_critical_pattern_detection(self):
        agent = LogAgent(_log_spec())
        agent._entry_buffer = [
            {"unit": "test.service", "message": "Out of memory: killed process 1234"},
        ]
        actions = await agent.reason({"entry_count": 1, "units_seen": ["test.service"]})
        assert len(actions) >= 1
        assert actions[0].params["alert_type"] == "oom"

    @pytest.mark.asyncio
    async def test_segfault_detection(self):
        agent = LogAgent(_log_spec())
        agent._entry_buffer = [
            {"unit": "app.service", "message": "traps: myapp[1234] segfault at 0x0"},
        ]
        actions = await agent.reason({"entry_count": 1, "units_seen": ["app.service"]})
        assert any(a.params.get("alert_type") == "segfault" for a in actions)

    @pytest.mark.asyncio
    async def test_no_alerts_for_normal_logs(self):
        agent = LogAgent(_log_spec())
        agent._entry_buffer = [
            {"unit": "test.service", "message": "Started successfully"},
        ]
        actions = await agent.reason({"entry_count": 1, "units_seen": ["test.service"]})
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_rate_spike_detection(self):
        agent = LogAgent(_log_spec(config={"rate_threshold": 2.0}))
        # Set a known baseline
        agent._baseline_rates["test.service"] = 10.0

        # Create stats showing a spike
        stats = UnitStats()
        now = time.monotonic()
        for i in range(100):
            stats.timestamps.append(now - 30 + i * 0.3)
        agent._unit_stats["test.service"] = stats

        agent._entry_buffer = []
        actions = await agent.reason({"entry_count": 0, "units_seen": []})
        spike_actions = [a for a in actions if a.action == "rate_spike"]
        assert len(spike_actions) >= 1

    @pytest.mark.asyncio
    async def test_llm_not_triggered_without_flag(self):
        agent = LogAgent(_log_spec(config={"llm_enabled": False}))
        agent._entry_buffer = [
            {"unit": "test.service", "message": "Out of memory: killed"},
        ]
        actions = await agent.reason({"entry_count": 1, "units_seen": ["test.service"]})
        assert not any(a.tool == "model_bus" for a in actions)


class TestLogAgentAct:
    @pytest.mark.asyncio
    async def test_alert_action(self):
        from yaaos_agentd.types import Action
        agent = LogAgent(_log_spec())
        actions = [
            Action(
                tool="alert",
                action="critical",
                params={"alert_type": "oom", "unit": "test.service", "message": "OOM killed"},
                description="Critical: oom in test.service",
            ),
        ]
        results = await agent.act(actions)
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_multiple_critical_patterns(self):
        """Verify all critical patterns are valid regexes."""
        for pattern, name in _CRITICAL_PATTERNS:
            assert pattern.pattern  # Not empty
            assert name  # Has a name
