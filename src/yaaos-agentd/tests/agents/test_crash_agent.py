"""Tests for Crash-Agent — core dump analysis."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from yaaos_agentd.agents.crash_agent import CrashAgent
from yaaos_agentd.types import AgentSpec


def _crash_spec(**overrides) -> AgentSpec:
    defaults = {
        "name": "crash",
        "module": "yaaos_agentd.agents.crash_agent",
        "reconcile_interval_sec": 30.0,
        "config": {},
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


class TestCrashAgentObserve:
    @pytest.mark.asyncio
    async def test_observe_no_coredumpctl(self):
        """Without coredumpctl, observe returns empty dumps."""
        agent = CrashAgent(_crash_spec())

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            obs = await agent.observe()
            assert obs["total_dumps"] == 0
            assert obs["new_dumps"] == 0

    @pytest.mark.asyncio
    async def test_observe_with_dumps(self):
        """With mock coredumpctl output, observe parses dumps."""
        agent = CrashAgent(_crash_spec())

        dumps = [
            {"pid": 1234, "exe": "/usr/bin/myapp", "sig": 11, "timestamp": "2024-01-01T00:00:00"},
            {"pid": 5678, "exe": "/usr/bin/other", "sig": 6, "timestamp": "2024-01-01T00:01:00"},
        ]

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps(dumps).encode(), b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            obs = await agent.observe()
            assert obs["total_dumps"] == 2
            assert obs["new_dumps"] == 2

    @pytest.mark.asyncio
    async def test_observe_filters_analyzed(self):
        """Already-analyzed dumps are filtered out."""
        agent = CrashAgent(_crash_spec())
        agent._analyzed.add("1234-2024-01-01T00:00:00")

        dumps = [
            {"pid": 1234, "timestamp": "2024-01-01T00:00:00"},
            {"pid": 5678, "timestamp": "2024-01-01T00:01:00"},
        ]

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps(dumps).encode(), b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            obs = await agent.observe()
            assert obs["new_dumps"] == 1


class TestCrashAgentReason:
    @pytest.mark.asyncio
    async def test_reason_creates_actions_for_new_dumps(self):
        agent = CrashAgent(_crash_spec())
        observation = {
            "total_dumps": 1,
            "new_dumps": 1,
            "dumps": [
                {"pid": 1234, "exe": "/usr/bin/myapp", "sig": 11, "timestamp": "t1"},
            ],
        }
        actions = await agent.reason(observation)
        assert len(actions) == 1
        assert actions[0].params["pid"] == "1234"
        assert actions[0].params["exe"] == "/usr/bin/myapp"

    @pytest.mark.asyncio
    async def test_reason_no_actions_for_empty_dumps(self):
        agent = CrashAgent(_crash_spec())
        observation = {"total_dumps": 0, "new_dumps": 0, "dumps": []}
        actions = await agent.reason(observation)
        assert len(actions) == 0


class TestCrashAgentAct:
    @pytest.mark.asyncio
    async def test_act_extracts_backtrace(self):
        agent = CrashAgent(_crash_spec())

        from yaaos_agentd.types import Action
        action = Action(
            tool="coredumpctl",
            action="backtrace",
            params={"pid": "1234", "exe": "myapp", "signal": "11", "timestamp": "t1"},
            description="Analyze crash: myapp",
        )

        # Mock backtrace extraction
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"#0  0x0000 in main () at main.c:42\n#1  0x1234 in __libc_start_main", b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            results = await agent.act([action])
            assert len(results) == 1
            assert results[0].success is True
            assert "main.c:42" in results[0].output

    @pytest.mark.asyncio
    async def test_act_marks_dump_analyzed(self):
        agent = CrashAgent(_crash_spec())

        from yaaos_agentd.types import Action
        action = Action(
            tool="coredumpctl",
            action="backtrace",
            params={"pid": "1234", "exe": "myapp", "signal": "11", "timestamp": "t1"},
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"bt output", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await agent.act([action])
            assert "1234-t1" in agent._analyzed

    @pytest.mark.asyncio
    async def test_act_handles_missing_coredumpctl(self):
        agent = CrashAgent(_crash_spec())

        from yaaos_agentd.types import Action
        action = Action(
            tool="coredumpctl",
            action="backtrace",
            params={"pid": "999", "exe": "myapp", "signal": "11", "timestamp": "t1"},
        )

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            results = await agent.act([action])
            assert len(results) == 1
            assert results[0].success is True  # Gracefully handles missing binary
