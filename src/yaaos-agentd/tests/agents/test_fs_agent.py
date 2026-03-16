"""Tests for FS-Agent — SFS daemon wrapper via systemd D-Bus."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yaaos_agentd.agents.fs_agent import FsAgent
from yaaos_agentd.types import Action, AgentSpec


def _fs_spec(**overrides) -> AgentSpec:
    defaults = {
        "name": "fs",
        "module": "yaaos_agentd.agents.fs_agent",
        "reconcile_interval_sec": 30.0,
        "config": {},
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


class TestFsAgentInit:
    def test_default_unit_name(self):
        agent = FsAgent(_fs_spec())
        assert agent._unit_name == "yaaos-sfs.service"

    def test_custom_unit_name(self):
        agent = FsAgent(_fs_spec(config={"delegate_to": "custom-sfs.service"}))
        assert agent._unit_name == "custom-sfs.service"


class TestFsAgentOnStart:
    @pytest.mark.asyncio
    async def test_on_start_connects_dbus(self):
        """on_start creates a SystemdManager and connects."""
        agent = FsAgent(_fs_spec())

        mock_mgr = AsyncMock()
        mock_mgr.connect = AsyncMock()

        with patch("yaaos_agentd.systemd.SystemdManager", return_value=mock_mgr):
            await agent.on_start()
            mock_mgr.connect.assert_awaited_once()
            assert agent._systemd is mock_mgr

    @pytest.mark.asyncio
    async def test_on_start_dbus_unavailable(self):
        """on_start gracefully handles D-Bus connection failure."""
        agent = FsAgent(_fs_spec())

        mock_mgr = AsyncMock()
        mock_mgr.connect = AsyncMock(side_effect=Exception("No D-Bus"))

        with patch("yaaos_agentd.systemd.SystemdManager", return_value=mock_mgr):
            await agent.on_start()
            # Should set _systemd to None on failure
            assert agent._systemd is None


class TestFsAgentOnStop:
    @pytest.mark.asyncio
    async def test_on_stop_disconnects(self):
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.disconnect = AsyncMock()

        await agent.on_stop()
        agent._systemd.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_stop_no_systemd(self):
        """on_stop is safe when _systemd is None."""
        agent = FsAgent(_fs_spec())
        agent._systemd = None
        await agent.on_stop()  # Should not raise


class TestFsAgentObserve:
    @pytest.mark.asyncio
    async def test_observe_no_dbus(self):
        """Without D-Bus, observe returns no_dbus status."""
        agent = FsAgent(_fs_spec())
        agent._systemd = None

        obs = await agent.observe()
        assert obs["status"] == "no_dbus"
        assert obs["unit"] == "yaaos-sfs.service"

    @pytest.mark.asyncio
    async def test_observe_not_connected(self):
        """With disconnected SystemdManager, observe returns no_dbus."""
        agent = FsAgent(_fs_spec())
        agent._systemd = MagicMock()
        agent._systemd.is_connected = False

        obs = await agent.observe()
        assert obs["status"] == "no_dbus"

    @pytest.mark.asyncio
    async def test_observe_active_unit(self):
        """Observe returns full status for an active unit."""
        agent = FsAgent(_fs_spec())

        mock_status = MagicMock()
        mock_status.active_state = "active"
        mock_status.sub_state = "running"
        mock_status.main_pid = 1234
        mock_status.memory_bytes = 50_000_000

        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.unit_status = AsyncMock(return_value=mock_status)

        obs = await agent.observe()
        assert obs["status"] == "ok"
        assert obs["active_state"] == "active"
        assert obs["sub_state"] == "running"
        assert obs["main_pid"] == 1234
        assert obs["memory_bytes"] == 50_000_000

    @pytest.mark.asyncio
    async def test_observe_dbus_error(self):
        """Observe returns error status on D-Bus exception."""
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.unit_status = AsyncMock(side_effect=Exception("D-Bus timeout"))

        obs = await agent.observe()
        assert obs["status"] == "error"
        assert "D-Bus timeout" in obs["error"]


class TestFsAgentReason:
    @pytest.mark.asyncio
    async def test_reason_no_dbus(self):
        """No actions when D-Bus is unavailable."""
        agent = FsAgent(_fs_spec())
        actions = await agent.reason({"status": "no_dbus"})
        assert actions == []

    @pytest.mark.asyncio
    async def test_reason_error_increments_failures(self):
        """Error observations increment consecutive failure counter."""
        agent = FsAgent(_fs_spec())
        assert agent._consecutive_failures == 0

        await agent.reason({"status": "error", "error": "fail1"})
        assert agent._consecutive_failures == 1

        await agent.reason({"status": "error", "error": "fail2"})
        assert agent._consecutive_failures == 2

    @pytest.mark.asyncio
    async def test_reason_ok_resets_failures(self):
        """Successful observation resets failure counter."""
        agent = FsAgent(_fs_spec())
        agent._consecutive_failures = 5

        actions = await agent.reason({"status": "ok", "active_state": "active"})
        assert agent._consecutive_failures == 0
        assert actions == []

    @pytest.mark.asyncio
    async def test_reason_failed_unit_restarts(self):
        """A failed SFS unit triggers a restart action."""
        agent = FsAgent(_fs_spec())
        actions = await agent.reason({"status": "ok", "active_state": "failed"})
        assert len(actions) == 1
        assert actions[0].tool == "systemd"
        assert actions[0].action == "restart"
        assert actions[0].params["unit"] == "yaaos-sfs.service"

    @pytest.mark.asyncio
    async def test_reason_inactive_unit_starts(self):
        """An inactive SFS unit triggers a start action."""
        agent = FsAgent(_fs_spec())
        actions = await agent.reason({"status": "ok", "active_state": "inactive"})
        assert len(actions) == 1
        assert actions[0].tool == "systemd"
        assert actions[0].action == "start"

    @pytest.mark.asyncio
    async def test_reason_active_unit_no_action(self):
        """An active SFS unit needs no action."""
        agent = FsAgent(_fs_spec())
        actions = await agent.reason({"status": "ok", "active_state": "active"})
        assert actions == []


class TestFsAgentAct:
    @pytest.mark.asyncio
    async def test_act_no_dbus(self):
        """Act returns failure when D-Bus is not connected."""
        agent = FsAgent(_fs_spec())
        agent._systemd = None

        action = Action(
            tool="systemd",
            action="restart",
            params={"unit": "yaaos-sfs.service"},
            description="Restart SFS",
        )
        results = await agent.act([action])
        assert len(results) == 1
        assert results[0].success is False
        assert "not connected" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_act_start_success(self):
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.start_unit = AsyncMock()

        action = Action(
            tool="systemd",
            action="start",
            params={"unit": "yaaos-sfs.service"},
        )
        results = await agent.act([action])
        assert len(results) == 1
        assert results[0].success is True
        agent._systemd.start_unit.assert_awaited_once_with("yaaos-sfs.service")

    @pytest.mark.asyncio
    async def test_act_restart_success(self):
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.restart_unit = AsyncMock()

        action = Action(
            tool="systemd",
            action="restart",
            params={"unit": "yaaos-sfs.service"},
        )
        results = await agent.act([action])
        assert results[0].success is True
        agent._systemd.restart_unit.assert_awaited_once_with("yaaos-sfs.service")

    @pytest.mark.asyncio
    async def test_act_stop_success(self):
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.stop_unit = AsyncMock()

        action = Action(
            tool="systemd",
            action="stop",
            params={"unit": "yaaos-sfs.service"},
        )
        results = await agent.act([action])
        assert results[0].success is True
        agent._systemd.stop_unit.assert_awaited_once_with("yaaos-sfs.service")

    @pytest.mark.asyncio
    async def test_act_handles_exception(self):
        """Act catches exceptions from systemd calls."""
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.restart_unit = AsyncMock(side_effect=Exception("Unit not found"))

        action = Action(
            tool="systemd",
            action="restart",
            params={"unit": "yaaos-sfs.service"},
        )
        results = await agent.act([action])
        assert results[0].success is False
        assert "Unit not found" in results[0].error

    @pytest.mark.asyncio
    async def test_act_multiple_actions(self):
        """Act processes multiple actions sequentially."""
        agent = FsAgent(_fs_spec())
        agent._systemd = AsyncMock()
        agent._systemd.is_connected = True
        agent._systemd.stop_unit = AsyncMock()
        agent._systemd.start_unit = AsyncMock()

        actions = [
            Action(tool="systemd", action="stop", params={"unit": "yaaos-sfs.service"}),
            Action(tool="systemd", action="start", params={"unit": "yaaos-sfs.service"}),
        ]
        results = await agent.act(actions)
        assert len(results) == 2
        assert all(r.success for r in results)
