"""Tests for Agent Bus client SDK."""

from __future__ import annotations

import asyncio

import pytest

from yaaos_agentd.client import AgentBusClient, AsyncAgentBusClient
from yaaos_agentd.config import Config, SupervisorConfig
from yaaos_agentd.errors import AgentdError, DaemonNotRunning
from yaaos_agentd.server import AgentBusServer
from yaaos_agentd.supervisor import Supervisor
from yaaos_agentd.tools.manifest import ToolDefinition, ToolSchema
from yaaos_agentd.tools.registry import ToolRegistry
from yaaos_agentd.types import AgentSpec

from tests.conftest import DummyAgent


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
async def running_server(tmp_path):
    """Start a server with a supervisor and registry, yield socket path, then clean up."""
    socket_path = tmp_path / "client_test.sock"
    config = Config(
        supervisor=SupervisorConfig(
            socket_path=socket_path,
            reconcile_interval_sec=0.1,
            log_level="debug",
        ),
        agents={
            "test-agent": AgentSpec(
                name="test-agent",
                module="tests.conftest",
                reconcile_interval_sec=0.1,
            ),
        },
    )

    supervisor = Supervisor(config, agent_class_override=DummyAgent)
    supervisor._started_at = 1.0
    await supervisor.reconcile()
    await asyncio.sleep(0.1)

    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
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
        )
    )

    server = AgentBusServer(socket_path, supervisor, registry)
    await server.start()

    yield socket_path

    await server.stop()
    await supervisor.shutdown()


# ── Async Client Tests ───────────────────────────────────────


class TestAsyncClient:
    @pytest.mark.asyncio
    async def test_ping(self, running_server):
        client = AsyncAgentBusClient(running_server)
        assert await client.ping() is True

    @pytest.mark.asyncio
    async def test_ping_no_server(self, tmp_path):
        client = AsyncAgentBusClient(tmp_path / "nonexistent.sock")
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_health(self, running_server):
        client = AsyncAgentBusClient(running_server)
        health = await client.health()
        assert health["status"] in ("healthy", "degraded")
        assert "agent_count" in health

    @pytest.mark.asyncio
    async def test_list_agents(self, running_server):
        client = AsyncAgentBusClient(running_server)
        agents = await client.list_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_agent_status(self, running_server):
        client = AsyncAgentBusClient(running_server)
        status = await client.agent_status("test-agent")
        assert status["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_agent_status_not_found(self, running_server):
        client = AsyncAgentBusClient(running_server)
        with pytest.raises(AgentdError, match="Agent not found"):
            await client.agent_status("nonexistent")

    @pytest.mark.asyncio
    async def test_list_tools(self, running_server):
        client = AsyncAgentBusClient(running_server)
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_tool_schema(self, running_server):
        client = AsyncAgentBusClient(running_server)
        result = await client.tool_schema("echo")
        assert "say" in result["schemas"]

    @pytest.mark.asyncio
    async def test_invoke_tool(self, running_server):
        client = AsyncAgentBusClient(running_server)
        result = await client.invoke_tool("echo", "say", {"message": "hello-from-client"})
        assert result["exit_code"] == 0
        assert "hello-from-client" in result["stdout"]

    @pytest.mark.asyncio
    async def test_daemon_not_running(self, tmp_path):
        client = AsyncAgentBusClient(tmp_path / "nonexistent.sock")
        with pytest.raises(DaemonNotRunning):
            await client.health()

    @pytest.mark.asyncio
    async def test_stop_agent(self, running_server):
        client = AsyncAgentBusClient(running_server)
        result = await client.stop_agent("test-agent")
        assert result["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_restart_agent(self, running_server):
        client = AsyncAgentBusClient(running_server)
        # Stop first, then restart
        await client.stop_agent("test-agent")
        result = await client.restart_agent("test-agent")
        assert result["status"] == "restarted"


# ── Sync Client Tests ────────────────────────────────────────


class TestSyncClient:
    """Test the sync wrapper delegates correctly.

    Full integration is tested via TestAsyncClient. These tests verify
    the sync _run() wrapper correctly bridges sync→async.
    """

    def test_sync_ping_no_server(self, tmp_path):
        client = AgentBusClient(tmp_path / "nonexistent.sock")
        assert client.ping() is False

    def test_sync_health_no_server(self, tmp_path):
        client = AgentBusClient(tmp_path / "nonexistent.sock")
        with pytest.raises(DaemonNotRunning):
            client.health()

    def test_sync_delegates_to_async(self):
        """Verify sync methods map to async counterparts."""
        client = AgentBusClient("/tmp/fake.sock")
        # Check all public methods exist and are callable
        for method in [
            "ping",
            "health",
            "list_agents",
            "agent_status",
            "start_agent",
            "stop_agent",
            "restart_agent",
            "list_tools",
            "tool_schema",
            "invoke_tool",
            "reload_config",
        ]:
            assert callable(getattr(client, method))
