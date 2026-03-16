"""Tests for Resource-Agent — CPU/RAM monitoring and prediction."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from yaaos_agentd.agents.resource_agent import ResourceAgent, ResourceTrend
from yaaos_agentd.types import AgentSpec


def _resource_spec(**overrides) -> AgentSpec:
    defaults = {
        "name": "resource",
        "module": "yaaos_agentd.agents.resource_agent",
        "reconcile_interval_sec": 10.0,
        "config": {
            "cpu_threshold": 85.0,
            "memory_warn_pct": 80.0,
            "memory_critical_pct": 90.0,
            "prediction_window_sec": 180.0,
            "gpu_enabled": False,
        },
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


class TestResourceTrend:
    def test_empty_prediction_returns_none(self):
        trend = ResourceTrend()
        assert trend.predict_time_to_threshold(90.0) is None

    def test_flat_trend_returns_none(self):
        trend = ResourceTrend()
        now = time.monotonic()
        for i in range(20):
            trend.update(now + i * 10, 50.0)
        assert trend.predict_time_to_threshold(90.0) is None

    def test_rising_trend_predicts(self):
        trend = ResourceTrend()
        base = time.monotonic()
        # Memory rising from 70% to 85% over 300 seconds
        for i in range(30):
            value = 70.0 + (i * 0.5)  # 0.5% per 10 seconds
            trend.update(base + i * 10, value)

        prediction = trend.predict_time_to_threshold(90.0)
        assert prediction is not None
        assert prediction > 0

    def test_already_exceeded_returns_zero(self):
        trend = ResourceTrend()
        base = time.monotonic()
        for i in range(20):
            trend.update(base + i * 10, 91.0 + i * 0.1)
        assert trend.predict_time_to_threshold(90.0) == 0.0

    def test_ewma_updates(self):
        trend = ResourceTrend()
        trend.update(0, 50.0)
        assert trend.ewma == 50.0
        trend.update(1, 60.0)
        assert trend.ewma > 50.0
        assert trend.ewma < 60.0


class TestResourceAgentObserve:
    @pytest.mark.asyncio
    async def test_observe_with_psutil(self):
        agent = ResourceAgent(_resource_spec())

        mock_mem = MagicMock()
        mock_mem.percent = 65.0
        mock_mem.available = 8 * 1024 * 1024 * 1024  # 8 GB
        mock_mem.total = 32 * 1024 * 1024 * 1024  # 32 GB

        with patch("psutil.cpu_percent", return_value=25.0), \
             patch("psutil.virtual_memory", return_value=mock_mem):
            obs = await agent.observe()
            assert obs["cpu_percent"] == 25.0
            assert obs["memory_percent"] == 65.0
            assert obs["memory_available_mb"] > 0

    @pytest.mark.asyncio
    async def test_observe_without_psutil(self):
        """Gracefully handles missing psutil by catching ImportError."""
        agent = ResourceAgent(_resource_spec())

        # Remove psutil from sys.modules cache so the lazy import fails
        import sys as _sys
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        real_psutil = _sys.modules.get("psutil")

        def mock_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        _sys.modules.pop("psutil", None)
        with patch("builtins.__import__", side_effect=mock_import):
            obs = await agent.observe()
            assert obs["cpu_percent"] == 0.0

        # Restore
        if real_psutil is not None:
            _sys.modules["psutil"] = real_psutil


class TestResourceAgentReason:
    @pytest.mark.asyncio
    async def test_no_alerts_normal_load(self):
        agent = ResourceAgent(_resource_spec())
        obs = {"cpu_percent": 30.0, "memory_percent": 50.0, "memory_available_mb": 16000}
        actions = await agent.reason(obs)
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_memory_warning(self):
        agent = ResourceAgent(_resource_spec())
        obs = {"cpu_percent": 30.0, "memory_percent": 82.0, "memory_available_mb": 5000}
        actions = await agent.reason(obs)
        warnings = [a for a in actions if a.action == "memory_warning"]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_memory_critical(self):
        agent = ResourceAgent(_resource_spec())
        obs = {"cpu_percent": 30.0, "memory_percent": 95.0, "memory_available_mb": 1000}
        actions = await agent.reason(obs)
        criticals = [a for a in actions if a.action == "memory_critical"]
        assert len(criticals) == 1

    @pytest.mark.asyncio
    async def test_cpu_not_sustained(self):
        """CPU alert requires sustained high usage (>60s)."""
        agent = ResourceAgent(_resource_spec())
        obs = {"cpu_percent": 90.0, "memory_percent": 50.0, "memory_available_mb": 16000}
        # First call — starts the timer but no alert yet
        actions = await agent.reason(obs)
        cpu_alerts = [a for a in actions if a.action == "cpu_high"]
        assert len(cpu_alerts) == 0

    @pytest.mark.asyncio
    async def test_cpu_sustained_alert(self):
        """After 60s of sustained high CPU, an alert fires."""
        agent = ResourceAgent(_resource_spec())
        obs = {"cpu_percent": 90.0, "memory_percent": 50.0, "memory_available_mb": 16000}
        # First call starts timer
        await agent.reason(obs)
        # Simulate 61 seconds have passed
        agent._sustained_cpu_start = time.monotonic() - 61
        actions = await agent.reason(obs)
        cpu_alerts = [a for a in actions if a.action == "cpu_high"]
        assert len(cpu_alerts) == 1

    @pytest.mark.asyncio
    async def test_memory_prediction_alert(self):
        """Memory trend prediction fires when exhaustion is imminent."""
        agent = ResourceAgent(_resource_spec(config={
            "memory_critical_pct": 90.0,
            "prediction_window_sec": 300.0,
            "gpu_enabled": False,
        }))
        # Seed the trend with rising memory usage
        base = time.monotonic()
        for i in range(30):
            agent._memory_trend.update(base + i * 10, 70.0 + i * 0.5)

        obs = {"cpu_percent": 30.0, "memory_percent": 85.0, "memory_available_mb": 5000}
        actions = await agent.reason(obs)
        predicted = [a for a in actions if a.action == "memory_predicted"]
        assert len(predicted) >= 1

    @pytest.mark.asyncio
    async def test_llm_not_triggered_without_flag(self):
        agent = ResourceAgent(_resource_spec(config={
            "llm_enabled": False,
            "memory_critical_pct": 90.0,
            "gpu_enabled": False,
        }))
        obs = {"cpu_percent": 30.0, "memory_percent": 95.0, "memory_available_mb": 500}
        actions = await agent.reason(obs)
        assert not any(a.tool == "model_bus" for a in actions)


class TestResourceAgentAct:
    @pytest.mark.asyncio
    async def test_alert_action(self):
        from yaaos_agentd.types import Action
        agent = ResourceAgent(_resource_spec())
        actions = [
            Action(
                tool="alert",
                action="memory_critical",
                params={"memory_percent": 95.0, "available_mb": 500},
                description="Critical: memory at 95%",
            ),
        ]
        results = await agent.act(actions)
        assert len(results) == 1
        assert results[0].success is True
