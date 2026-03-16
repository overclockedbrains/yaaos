"""Integration tests — full supervisor → agent → tool invocation flows.

These tests exercise the complete pipeline, from supervisor managing agents
to agents running cycles and tools being invoked through the Agent Bus API.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from yaaos_agentd.client import AsyncAgentBusClient
from yaaos_agentd.config import Config, SupervisorConfig
from yaaos_agentd.server import AgentBusServer
from yaaos_agentd.supervisor import Supervisor
from yaaos_agentd.tools.manifest import ToolDefinition, ToolSchema
from yaaos_agentd.tools.registry import ToolRegistry
from yaaos_agentd.types import AgentSpec, AgentState, RestartPolicy

from tests.conftest import DummyAgent, FatalCrashAgent

pytestmark = pytest.mark.integration


# ── Helpers ──────────────────────────────────────────────────


def _full_config(tmp_path: Path, **agent_overrides) -> Config:
    socket_path = tmp_path / "integration.sock"
    agents = {
        "test-agent": AgentSpec(
            name="test-agent",
            module="tests.conftest",
            reconcile_interval_sec=0.1,
            max_restarts=3,
            max_restart_window_sec=10.0,
            **agent_overrides,
        ),
    }
    return Config(
        supervisor=SupervisorConfig(
            socket_path=socket_path,
            reconcile_interval_sec=0.1,
            log_level="debug",
        ),
        agents=agents,
    )


def _full_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(ToolDefinition(
        name="echo",
        description="Echo tool",
        binary="echo",
        capabilities=["text"],
        schemas={
            "say": ToolSchema(
                name="say",
                description="Echo a message",
                args_template="{{ message }}",
                parameters={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                output_format="text",
            ),
        },
    ))
    return registry


async def _start_stack(tmp_path, *, agent_class=DummyAgent, **agent_overrides):
    """Start supervisor + server + registry and return components."""
    config = _full_config(tmp_path, **agent_overrides)
    supervisor = Supervisor(config, agent_class_override=agent_class)
    supervisor._started_at = time.monotonic()
    registry = _full_registry()
    server = AgentBusServer(config.supervisor.socket_path, supervisor, registry)

    await server.start()
    await supervisor.reconcile()
    await asyncio.sleep(0.15)  # Let agents run a cycle

    return config, supervisor, server, registry


async def _stop_stack(server, supervisor):
    await server.stop()
    await supervisor.shutdown()


# ── Full Pipeline Tests ──────────────────────────────────────


class TestSupervisorAgentPipeline:
    @pytest.mark.asyncio
    async def test_supervisor_starts_agent_and_reports_health(self, tmp_path):
        """Full flow: supervisor → start agent → health check via API."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            health = await client.health()
            assert health["status"] == "healthy"
            assert health["agents_running"] >= 1
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_agent_runs_cycles_and_reports_status(self, tmp_path):
        """Agent completes cycles and status is queryable via API."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            status = await client.agent_status("test-agent")
            assert status["name"] == "test-agent"
            assert status["cycle_count"] >= 1
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_agent_list_through_api(self, tmp_path):
        """List agents through the Agent Bus API."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            agents = await client.list_agents()
            assert len(agents) == 1
            assert agents[0]["name"] == "test-agent"
            assert agents[0]["state"] in ("running", "starting")
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_stop_and_restart_agent_via_api(self, tmp_path):
        """Stop an agent via API, verify it stopped, restart it."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)

            # Stop
            result = await client.stop_agent("test-agent")
            assert result["status"] == "stopped"

            # Verify stopped
            agents = await client.list_agents()
            assert len(agents) == 0  # Stopped agents are removed from handles

            # Restart
            result = await client.restart_agent("test-agent")
            assert result["status"] == "restarted"

            await asyncio.sleep(0.15)
            agents = await client.list_agents()
            assert len(agents) == 1
        finally:
            await _stop_stack(server, supervisor)


class TestToolInvocationPipeline:
    @pytest.mark.asyncio
    async def test_invoke_tool_through_api(self, tmp_path):
        """Full flow: client → API → tool registry → subprocess → result."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            result = await client.invoke_tool("echo", "say", {"message": "integration-test"})
            assert result["exit_code"] == 0
            assert "integration-test" in result["stdout"]
            assert result["is_error"] is False
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_list_tools_through_api(self, tmp_path):
        """List registered tools through the API."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"
            assert "say" in tools[0]["actions"]
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_tool_schema_through_api(self, tmp_path):
        """Get tool schema through the API."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            schema = await client.tool_schema("echo")
            assert "say" in schema["schemas"]
            assert schema["schemas"]["say"]["description"] == "Echo a message"
        finally:
            await _stop_stack(server, supervisor)


class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_supervisor_detects_crash_and_restarts(self, tmp_path):
        """Supervisor detects agent crash and restarts it per policy."""
        config = Config(
            supervisor=SupervisorConfig(
                socket_path=tmp_path / "crash_test.sock",
                reconcile_interval_sec=0.1,
            ),
            agents={
                "crash-agent": AgentSpec(
                    name="crash-agent",
                    module="tests.conftest",
                    restart_policy=RestartPolicy.PERMANENT,
                    reconcile_interval_sec=0.1,
                    max_restarts=3,
                    max_restart_window_sec=10.0,
                ),
            },
        )
        supervisor = Supervisor(config, agent_class_override=FatalCrashAgent)
        supervisor._started_at = time.monotonic()

        # First reconcile starts the agent
        await supervisor.reconcile()
        await asyncio.sleep(0.2)  # Let the agent task crash and complete

        # Agent should have crashed, second reconcile triggers restart
        await supervisor.reconcile()
        handle = supervisor.handles.get("crash-agent")
        assert handle is not None
        # The limiter should have recorded at least one restart
        assert handle.limiter.restart_count >= 1

        await supervisor.shutdown()

    @pytest.mark.asyncio
    async def test_crash_loop_detection(self, tmp_path):
        """After exceeding restart limits, agent enters crash_loop state."""
        config = Config(
            supervisor=SupervisorConfig(
                socket_path=tmp_path / "loop_test.sock",
                reconcile_interval_sec=0.05,
            ),
            agents={
                "loop-agent": AgentSpec(
                    name="loop-agent",
                    module="tests.conftest",
                    restart_policy=RestartPolicy.PERMANENT,
                    reconcile_interval_sec=0.05,
                    max_restarts=2,
                    max_restart_window_sec=60.0,
                ),
            },
        )
        supervisor = Supervisor(config, agent_class_override=FatalCrashAgent)
        supervisor._started_at = time.monotonic()

        # Run several reconcile cycles to trigger crash loop
        for _ in range(10):
            await supervisor.reconcile()
            await asyncio.sleep(0.1)

        handle = supervisor.handles.get("loop-agent")
        assert handle is not None
        status = supervisor.get_agent_status("loop-agent")
        assert status.state == AgentState.CRASH_LOOP

        await supervisor.shutdown()


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_all_agents(self, tmp_path):
        """Graceful shutdown stops all running agents."""
        config, supervisor, server, _ = await _start_stack(tmp_path)

        # Verify agents are running
        assert len(supervisor.handles) == 1
        handle = supervisor.handles["test-agent"]
        assert handle.is_running

        # Shutdown
        await _stop_stack(server, supervisor)

        # All handles should be cleared
        assert len(supervisor.handles) == 0

    @pytest.mark.asyncio
    async def test_server_stops_cleanly(self, tmp_path):
        """Server socket is cleaned up on shutdown."""
        config, supervisor, server, _ = await _start_stack(tmp_path)

        assert config.supervisor.socket_path.exists()
        await _stop_stack(server, supervisor)
        assert not config.supervisor.socket_path.exists()


class TestConfigHotReload:
    @pytest.mark.asyncio
    async def test_reconcile_starts_new_agents_after_config_change(self, tmp_path):
        """After config change, reconcile starts newly added agents."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            # Add a second agent to config
            new_spec = AgentSpec(
                name="new-agent",
                module="tests.conftest",
                reconcile_interval_sec=0.1,
            )
            new_agents = dict(supervisor.config.agents)
            new_agents["new-agent"] = new_spec
            supervisor.config = Config(
                supervisor=config.supervisor,
                agents=new_agents,
            )

            await supervisor.reconcile()
            await asyncio.sleep(0.15)

            assert "new-agent" in supervisor.handles
            assert supervisor.handles["new-agent"].is_running
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_reconcile_stops_removed_agents(self, tmp_path):
        """After config change, reconcile stops removed agents."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            # Remove the agent from config
            supervisor.config = Config(
                supervisor=config.supervisor,
                agents={},
            )

            await supervisor.reconcile()
            await asyncio.sleep(0.1)

            assert len(supervisor.handles) == 0
        finally:
            await _stop_stack(server, supervisor)


class TestHealthReporting:
    @pytest.mark.asyncio
    async def test_health_includes_server_and_agent_data(self, tmp_path):
        """Health response includes both supervisor and server statistics."""
        config, supervisor, server, _ = await _start_stack(tmp_path)
        try:
            client = AsyncAgentBusClient(config.supervisor.socket_path)
            health = await client.health()

            # Supervisor data
            assert "status" in health
            assert "agent_count" in health
            assert "agents_running" in health
            assert "uptime_sec" in health
            assert "total_cycles" in health

            # Server data
            assert "server" in health
            assert health["server"]["request_count"] >= 1
        finally:
            await _stop_stack(server, supervisor)

    @pytest.mark.asyncio
    async def test_degraded_health_on_failed_agent(self, tmp_path):
        """Health shows degraded when an agent has failed."""
        config = Config(
            supervisor=SupervisorConfig(
                socket_path=tmp_path / "degraded_test.sock",
                reconcile_interval_sec=0.1,
            ),
            agents={
                "crash-agent": AgentSpec(
                    name="crash-agent",
                    module="tests.conftest",
                    restart_policy=RestartPolicy.TEMPORARY,  # Won't restart
                    reconcile_interval_sec=0.1,
                    max_restarts=0,
                ),
            },
        )
        supervisor = Supervisor(config, agent_class_override=FatalCrashAgent)
        supervisor._started_at = time.monotonic()
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        await supervisor.reconcile()
        await asyncio.sleep(0.3)  # Let the crash happen and task complete

        try:
            health = supervisor.get_health()
            # Agent crashed, health should be degraded
            assert health.status == "degraded" or health.agents_failed >= 1
        finally:
            await server.stop()
            await supervisor.shutdown()
