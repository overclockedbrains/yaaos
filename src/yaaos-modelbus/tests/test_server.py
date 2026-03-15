"""Tests for the JSON-RPC server and client — Phase A success criteria.

Success criteria: "Can connect to socket, send health request, get response. Tests pass."
"""

from __future__ import annotations

import asyncio
import sys

import orjson
import pytest

from yaaos_modelbus.server import JsonRpcServer
from yaaos_modelbus.router import Router
from yaaos_modelbus.config import Config, ProviderConfig


# Skip on Windows — Unix sockets not supported
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available")


@pytest.fixture
async def running_server(tmp_path, mock_provider):
    """Start a server with a mock provider, yield the socket path, then stop."""
    socket_path = tmp_path / "test.sock"

    config = Config(
        socket_path=socket_path,
        providers={"mock": ProviderConfig(name="mock", enabled=True)},
    )
    router = Router(config, registry={"mock": mock_provider})

    server = JsonRpcServer(socket_path=socket_path, max_connections=4)
    server.register("health", router.handle_health)
    server.register("embed", router.handle_embed)
    server.register("models.list", router.handle_models_list)
    server.register_stream("generate", router.handle_generate)
    server.register_stream("chat", router.handle_chat)

    await server.start()
    yield socket_path
    await server.stop()


async def _send_request(socket_path, method, params=None, req_id=1):
    """Send a JSON-RPC request to the socket and return the response."""
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

        # Collect response (skip notifications)
        while True:
            line = await reader.readline()
            if not line:
                raise RuntimeError("Connection closed without response")
            response = orjson.loads(line)
            if "id" in response:
                return response
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_streaming_request(socket_path, method, params, req_id=1):
    """Send a request and collect all chunks + final response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id,
        }
        writer.write(orjson.dumps(msg) + b"\n")
        await writer.drain()

        chunks = []
        final = None
        while True:
            line = await reader.readline()
            if not line:
                break
            response = orjson.loads(line)
            if "id" in response:
                final = response
                break
            elif response.get("method") == "chunk":
                chunks.append(response.get("params", {}))
        return chunks, final
    finally:
        writer.close()
        await writer.wait_closed()


class TestServerHealthEndpoint:
    """Phase A success criteria: connect to socket, send health, get response."""

    @pytest.mark.asyncio
    async def test_health_returns_status(self, running_server):
        """CRITICAL: Can connect to socket and get health response."""
        response = await _send_request(running_server, "health")
        assert "result" in response
        result = response["result"]
        assert result["status"] in ("healthy", "degraded")
        assert "providers" in result

    @pytest.mark.asyncio
    async def test_health_shows_mock_provider(self, running_server):
        response = await _send_request(running_server, "health")
        providers = response["result"]["providers"]
        assert "mock" in providers
        assert providers["mock"]["healthy"] is True

    @pytest.mark.asyncio
    async def test_response_has_matching_id(self, running_server):
        response = await _send_request(running_server, "health", req_id=42)
        assert response["id"] == 42
        assert response["jsonrpc"] == "2.0"


class TestServerEmbed:
    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self, running_server):
        response = await _send_request(
            running_server,
            "embed",
            params={"texts": ["hello world"], "model": "mock/test-model"},
        )
        result = response["result"]
        assert "embeddings" in result
        assert len(result["embeddings"]) == 1
        assert result["dims"] == 4
        assert len(result["embeddings"][0]) == 4

    @pytest.mark.asyncio
    async def test_embed_multiple_texts(self, running_server):
        response = await _send_request(
            running_server,
            "embed",
            params={"texts": ["one", "two", "three"], "model": "mock/test-model"},
        )
        assert len(response["result"]["embeddings"]) == 3

    @pytest.mark.asyncio
    async def test_embed_missing_texts(self, running_server):
        response = await _send_request(
            running_server,
            "embed",
            params={"model": "mock/test-model"},
        )
        assert "error" in response
        assert response["error"]["code"] == -32602  # Invalid params


class TestServerGenerate:
    @pytest.mark.asyncio
    async def test_generate_streams_chunks(self, running_server):
        chunks, final = await _send_streaming_request(
            running_server,
            "generate",
            params={"prompt": "test", "model": "mock/test-model", "stream": True},
        )
        # Should receive chunk notifications
        assert len(chunks) >= 1
        tokens = [c["token"] for c in chunks if "token" in c]
        assert len(tokens) > 0

        # Final response should have id
        assert final is not None
        assert "result" in final
        assert final["result"]["done"] is True

    @pytest.mark.asyncio
    async def test_generate_non_streaming(self, running_server):
        """Non-streaming: should get single response with full text."""
        response = await _send_request(
            running_server,
            "generate",
            params={"prompt": "test", "model": "mock/test-model", "stream": False},
        )
        assert "result" in response
        # In non-streaming mode, text is accumulated
        assert "text" in response["result"]


class TestServerChat:
    @pytest.mark.asyncio
    async def test_chat_streams(self, running_server):
        chunks, final = await _send_streaming_request(
            running_server,
            "chat",
            params={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "mock/test-model",
                "stream": True,
            },
        )
        assert len(chunks) >= 1
        assert final is not None


class TestServerModelsList:
    @pytest.mark.asyncio
    async def test_models_list(self, running_server):
        response = await _send_request(running_server, "models.list")
        result = response["result"]
        assert "models" in result
        models = result["models"]
        assert len(models) >= 1
        assert models[0]["provider"] == "mock"


class TestServerErrors:
    @pytest.mark.asyncio
    async def test_unknown_method(self, running_server):
        response = await _send_request(running_server, "nonexistent.method")
        assert "error" in response
        assert response["error"]["code"] == -32601  # Method not found

    @pytest.mark.asyncio
    async def test_invalid_json(self, running_server):
        reader, writer = await asyncio.open_unix_connection(str(running_server))
        try:
            writer.write(b"not valid json\n")
            await writer.drain()
            line = await reader.readline()
            response = orjson.loads(line)
            assert "error" in response
            assert response["error"]["code"] == -32600  # Invalid request
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_missing_method(self, running_server):
        response = await _send_request(running_server, "", req_id=1)
        assert "error" in response


class TestServerMultipleConnections:
    @pytest.mark.asyncio
    async def test_concurrent_requests(self, running_server):
        """Multiple clients can connect and get responses concurrently."""
        tasks = [_send_request(running_server, "health", req_id=i) for i in range(5)]
        results = await asyncio.gather(*tasks)
        for i, r in enumerate(results):
            assert "result" in r
            assert r["id"] == i


class TestServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, tmp_path, mock_provider):
        socket_path = tmp_path / "lifecycle.sock"
        server = JsonRpcServer(socket_path=socket_path, max_connections=2)
        server.register("health", lambda params: asyncio.coroutine(lambda: {"status": "ok"})())

        await server.start()
        assert socket_path.exists()

        await server.stop()
        assert not socket_path.exists()

    @pytest.mark.asyncio
    async def test_uptime(self, tmp_path):
        socket_path = tmp_path / "uptime.sock"
        server = JsonRpcServer(socket_path=socket_path, max_connections=2)
        assert server.uptime_sec == 0.0

        await server.start()
        await asyncio.sleep(0.1)
        assert server.uptime_sec > 0.05

        await server.stop()
