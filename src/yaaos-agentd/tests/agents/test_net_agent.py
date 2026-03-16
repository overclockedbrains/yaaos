"""Tests for Net-Agent — network monitoring and anomaly detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from yaaos_agentd.agents.net_agent import NetAgent, _TCP_STATES
from yaaos_agentd.types import AgentSpec


def _net_spec(**overrides) -> AgentSpec:
    defaults = {
        "name": "net",
        "module": "yaaos_agentd.agents.net_agent",
        "reconcile_interval_sec": 30.0,
        "config": {"rate_threshold": 2.0, "expected_ports": [22, 80]},
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


# Sample /proc/net/tcp content (hex encoded)
_MOCK_PROC_NET_TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345
   1: 0100007F:0035 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12346
   2: 0100007F:1F90 0A0A0A01:C350 01 00000000:00000000 00:00000000 00000000  1000        0 12347
   3: 0100007F:1F90 0A0A0A02:C351 01 00000000:00000000 00:00000000 00000000  1000        0 12348
   4: 0100007F:1F90 0A0A0A03:C352 06 00000000:00000000 00:00000000 00000000  1000        0 12349
"""
# Port 0016 = 22 (ssh, LISTEN)
# Port 0035 = 53 (dns, LISTEN)
# Port 1F90 = 8080 (ESTABLISHED x2, TIME_WAIT x1)


class TestTCPStates:
    def test_all_states_mapped(self):
        assert _TCP_STATES["01"] == "ESTABLISHED"
        assert _TCP_STATES["0A"] == "LISTEN"
        assert _TCP_STATES["06"] == "TIME_WAIT"


class TestNetAgentObserve:
    @pytest.mark.asyncio
    async def test_observe_with_mock_proc(self, tmp_path):
        """Parse mocked /proc/net/tcp data."""
        agent = NetAgent(_net_spec())

        tcp_file = tmp_path / "tcp"
        tcp_file.write_text(_MOCK_PROC_NET_TCP)

        # Patch Path to redirect /proc/net/tcp reads
        original_exists = Path.exists
        original_read_text = Path.read_text

        def mock_exists(self):
            if str(self) in ("/proc/net/tcp", "/proc/net/tcp6"):
                return str(self) == "/proc/net/tcp"
            return original_exists(self)

        def mock_read_text(self, *args, **kwargs):
            if str(self) == "/proc/net/tcp":
                return _MOCK_PROC_NET_TCP
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "exists", mock_exists), \
             patch.object(Path, "read_text", mock_read_text):
            obs = await agent.observe()

        assert obs["established"] == 2
        assert obs["listening"] == 2
        assert obs["time_wait"] == 1
        assert obs["total"] == 5
        assert 22 in obs["listening_ports"]
        assert 53 in obs["listening_ports"]

    @pytest.mark.asyncio
    async def test_observe_no_proc_files(self):
        """Gracefully handle missing /proc files."""
        agent = NetAgent(_net_spec())

        with patch.object(Path, "exists", return_value=False):
            obs = await agent.observe()
            assert obs["total"] == 0


class TestNetAgentReason:
    @pytest.mark.asyncio
    async def test_no_alerts_first_cycle(self):
        """First cycle establishes baseline — no alerts."""
        agent = NetAgent(_net_spec())
        obs = {"established": 10, "listening": 2, "time_wait": 1, "total": 13, "listening_ports": [22, 80]}
        actions = await agent.reason(obs)
        # First cycle: no alerts (establishing baseline)
        new_listener_alerts = [a for a in actions if a.action == "new_listener"]
        assert len(new_listener_alerts) == 0

    @pytest.mark.asyncio
    async def test_new_listener_detection(self):
        """Detect unexpected new listening ports."""
        agent = NetAgent(_net_spec(config={"expected_ports": [22, 80], "rate_threshold": 2.0}))

        # First cycle: establish baseline
        obs1 = {"established": 10, "listening": 2, "time_wait": 0, "total": 12, "listening_ports": [22, 80]}
        await agent.reason(obs1)

        # Second cycle: new port appears
        obs2 = {"established": 10, "listening": 3, "time_wait": 0, "total": 13, "listening_ports": [22, 80, 4444]}
        actions = await agent.reason(obs2)
        new_listener = [a for a in actions if a.action == "new_listener"]
        assert len(new_listener) == 1
        assert new_listener[0].params["port"] == 4444

    @pytest.mark.asyncio
    async def test_expected_port_not_alerted(self):
        """Expected ports don't trigger alerts."""
        agent = NetAgent(_net_spec(config={"expected_ports": [22, 80, 443], "rate_threshold": 2.0}))

        obs1 = {"established": 10, "listening": 1, "time_wait": 0, "total": 11, "listening_ports": [22]}
        await agent.reason(obs1)

        obs2 = {"established": 10, "listening": 2, "time_wait": 0, "total": 12, "listening_ports": [22, 443]}
        actions = await agent.reason(obs2)
        new_listener = [a for a in actions if a.action == "new_listener"]
        assert len(new_listener) == 0

    @pytest.mark.asyncio
    async def test_connection_spike_detection(self):
        """Detect connection count spike above baseline."""
        agent = NetAgent(_net_spec(config={"rate_threshold": 2.0, "expected_ports": []}))
        agent._baseline_rate = 10.0
        agent._first_cycle = False

        obs = {"established": 25, "listening": 1, "time_wait": 0, "total": 26, "listening_ports": [22]}
        actions = await agent.reason(obs)
        spikes = [a for a in actions if a.action == "connection_spike"]
        assert len(spikes) == 1
        assert spikes[0].params["current"] == 25

    @pytest.mark.asyncio
    async def test_no_spike_within_threshold(self):
        """No alert when within normal range."""
        agent = NetAgent(_net_spec(config={"rate_threshold": 2.0, "expected_ports": []}))
        agent._baseline_rate = 10.0
        agent._first_cycle = False

        obs = {"established": 15, "listening": 1, "time_wait": 0, "total": 16, "listening_ports": [22]}
        actions = await agent.reason(obs)
        spikes = [a for a in actions if a.action == "connection_spike"]
        assert len(spikes) == 0


class TestNetAgentAct:
    @pytest.mark.asyncio
    async def test_alert_action(self):
        from yaaos_agentd.types import Action
        agent = NetAgent(_net_spec())
        actions = [
            Action(
                tool="alert",
                action="new_listener",
                params={"port": 4444},
                description="New listening port: 4444",
            ),
        ]
        results = await agent.act(actions)
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_llm_not_triggered_without_flag(self):
        agent = NetAgent(_net_spec(config={
            "llm_enabled": False,
            "rate_threshold": 2.0,
            "expected_ports": [],
        }))
        agent._baseline_rate = 10.0
        agent._first_cycle = False
        agent._known_listeners = {22}

        obs = {"established": 100, "listening": 2, "time_wait": 0, "total": 102, "listening_ports": [22, 9999]}
        actions = await agent.reason(obs)
        assert not any(a.tool == "model_bus" for a in actions)
