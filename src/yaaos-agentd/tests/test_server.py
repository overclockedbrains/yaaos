"""Tests for Agent Bus JSON-RPC server."""

from __future__ import annotations

import asyncio
from pathlib import Path

import orjson
import pytest

from yaaos_agentd.config import Config, SupervisorConfig
from yaaos_agentd.server import AgentBusServer
from yaaos_agentd.supervisor import Supervisor
from yaaos_agentd.tools.manifest import ToolDefinition, ToolSchema
from yaaos_agentd.tools.registry import ToolRegistry
from yaaos_agentd.types import AgentSpec

from tests.conftest import DummyAgent


# ── Helpers ──────────────────────────────────────────────────


async def _send_request(
    socket_path: Path, method: str, params: dict | None = None, req_id: int = 1
) -> dict:
    """Send a JSON-RPC request and return the parsed response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id,
        }
        writer.write(orjson.dumps(msg) + b"\n")
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        return orjson.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()


def _make_config(tmp_path: Path) -> Config:
    """Create a test config with a dummy agent."""
    socket_path = tmp_path / "test_agentbus.sock"
    return Config(
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
                max_restarts=3,
                max_restart_window_sec=10.0,
            ),
        },
    )


def _make_registry() -> ToolRegistry:
    """Create a registry with a test tool."""
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
    return registry


# ── Server Lifecycle Tests ───────────────────────────────────


class TestServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)

        await server.start()
        assert config.supervisor.socket_path.exists()

        await server.stop()
        assert not config.supervisor.socket_path.exists()

    @pytest.mark.asyncio
    async def test_uptime_tracks(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)

        assert server.uptime_sec == 0.0
        await server.start()
        await asyncio.sleep(0.05)
        assert server.uptime_sec > 0
        await server.stop()


# ── Health Endpoint ──────────────────────────────────────────


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_status(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "health")
            assert "result" in resp
            result = resp["result"]
            assert result["status"] in ("healthy", "degraded")
            assert "agent_count" in result
            assert "server" in result
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_health_includes_server_stats(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "health")
            server_info = resp["result"]["server"]
            assert "uptime_sec" in server_info
            assert "request_count" in server_info
            assert server_info["request_count"] >= 1
        finally:
            await server.stop()


# ── Agent Management Endpoints ───────────────────────────────


class TestAgentEndpoints:
    @pytest.mark.asyncio
    async def test_agents_list_empty(self, tmp_path):
        config = Config(
            supervisor=SupervisorConfig(
                socket_path=tmp_path / "test.sock",
                reconcile_interval_sec=0.1,
            ),
        )
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "agents.list")
            assert resp["result"]["agents"] == []
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_agents_list_with_agents(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        supervisor._started_at = 1.0
        await supervisor.reconcile()
        await asyncio.sleep(0.1)

        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "agents.list")
            agents = resp["result"]["agents"]
            assert len(agents) == 1
            assert agents[0]["name"] == "test-agent"
        finally:
            await server.stop()
            await supervisor.shutdown()

    @pytest.mark.asyncio
    async def test_agents_status_found(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        supervisor._started_at = 1.0
        await supervisor.reconcile()
        await asyncio.sleep(0.1)

        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "agents.status", {"name": "test-agent"}
            )
            assert "result" in resp
            assert resp["result"]["name"] == "test-agent"
        finally:
            await server.stop()
            await supervisor.shutdown()

    @pytest.mark.asyncio
    async def test_agents_status_not_found(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "agents.status", {"name": "nonexistent"}
            )
            assert "error" in resp
            assert resp["error"]["code"] == -32000  # AGENT_NOT_FOUND
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_agents_status_missing_param(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "agents.status", {}
            )
            assert "error" in resp
            assert resp["error"]["code"] == -32602  # INVALID_PARAMS
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_agents_stop(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        supervisor._started_at = 1.0
        await supervisor.reconcile()
        await asyncio.sleep(0.1)

        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "agents.stop", {"name": "test-agent"}
            )
            assert resp["result"]["status"] == "stopped"
        finally:
            await server.stop()
            await supervisor.shutdown()


# ── Tool Endpoints ───────────────────────────────────────────


class TestToolEndpoints:
    @pytest.mark.asyncio
    async def test_tools_list_no_registry(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor, registry=None)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "tools.list")
            assert resp["result"]["tools"] == []
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_tools_list_with_registry(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        registry = _make_registry()
        server = AgentBusServer(config.supervisor.socket_path, supervisor, registry)
        await server.start()

        try:
            resp = await _send_request(config.supervisor.socket_path, "tools.list")
            tools = resp["result"]["tools"]
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_tools_schema(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        registry = _make_registry()
        server = AgentBusServer(config.supervisor.socket_path, supervisor, registry)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "tools.schema", {"tool": "echo"}
            )
            assert "result" in resp
            assert "say" in resp["result"]["schemas"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_tools_invoke(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        registry = _make_registry()
        server = AgentBusServer(config.supervisor.socket_path, supervisor, registry)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path,
                "tools.invoke",
                {"tool": "echo", "action": "say", "params": {"message": "hello-test"}},
            )
            assert "result" in resp
            result = resp["result"]
            assert result["exit_code"] == 0
            assert "hello-test" in result["stdout"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_tools_invoke_not_found(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        registry = _make_registry()
        server = AgentBusServer(config.supervisor.socket_path, supervisor, registry)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path,
                "tools.invoke",
                {"tool": "nonexistent", "action": "run"},
            )
            assert "error" in resp
        finally:
            await server.stop()


# ── Protocol Edge Cases ──────────────────────────────────────


class TestProtocolEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_json(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(config.supervisor.socket_path)
            )
            writer.write(b"not valid json\n")
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            resp = orjson.loads(line)
            assert "error" in resp
            assert resp["error"]["code"] == -32600  # INVALID_REQUEST
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_unknown_method(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            resp = await _send_request(
                config.supervisor.socket_path, "nonexistent.method"
            )
            assert "error" in resp
            assert resp["error"]["code"] == -32601  # METHOD_NOT_FOUND
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_method_field(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(config.supervisor.socket_path)
            )
            msg = {"jsonrpc": "2.0", "params": {}, "id": 1}
            writer.write(orjson.dumps(msg) + b"\n")
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            resp = orjson.loads(line)
            assert "error" in resp
            assert resp["error"]["code"] == -32600  # INVALID_REQUEST
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_request_count_increments(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            assert server.request_count == 0
            await _send_request(config.supervisor.socket_path, "health")
            assert server.request_count == 1
            await _send_request(config.supervisor.socket_path, "health")
            assert server.request_count == 2
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_multiple_requests_same_connection(self, tmp_path):
        config = _make_config(tmp_path)
        supervisor = Supervisor(config, agent_class_override=DummyAgent)
        server = AgentBusServer(config.supervisor.socket_path, supervisor)
        await server.start()

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(config.supervisor.socket_path)
            )

            for i in range(3):
                msg = {
                    "jsonrpc": "2.0",
                    "method": "health",
                    "params": {},
                    "id": i + 1,
                }
                writer.write(orjson.dumps(msg) + b"\n")
                await writer.drain()

                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                resp = orjson.loads(line)
                assert "result" in resp
                assert resp["id"] == i + 1

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()
