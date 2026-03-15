"""Tests for the Model Bus client."""

from __future__ import annotations

import sys

import pytest

from yaaos_modelbus.client import AsyncModelBusClient
from yaaos_modelbus.config import Config, ProviderConfig
from yaaos_modelbus.errors import DaemonNotRunning
from yaaos_modelbus.router import Router
from yaaos_modelbus.server import JsonRpcServer


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available")


@pytest.fixture
async def server_and_client(tmp_path, mock_provider):
    """Start server with mock provider, return (socket_path, client)."""
    socket_path = tmp_path / "client_test.sock"

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

    client = AsyncModelBusClient(socket_path=socket_path)
    yield client

    await server.stop()


class TestAsyncClient:
    @pytest.mark.asyncio
    async def test_ping(self, server_and_client):
        assert await server_and_client.ping() is True

    @pytest.mark.asyncio
    async def test_health(self, server_and_client):
        result = await server_and_client.health()
        assert result["status"] in ("healthy", "degraded")

    @pytest.mark.asyncio
    async def test_embed(self, server_and_client):
        result = await server_and_client.embed(["hello", "world"], model="mock/test-model")
        assert len(result["embeddings"]) == 2
        assert result["dims"] == 4

    @pytest.mark.asyncio
    async def test_list_models(self, server_and_client):
        models = await server_and_client.list_models()
        assert len(models) >= 1
        assert models[0]["provider"] == "mock"

    @pytest.mark.asyncio
    async def test_generate_streaming(self, server_and_client):
        chunks = []
        async for chunk in server_and_client.generate(
            "test prompt", model="mock/test-model", stream=True
        ):
            chunks.append(chunk)
        # Should have token chunks + final done
        assert len(chunks) >= 2
        assert chunks[-1].get("done") is True

    @pytest.mark.asyncio
    async def test_chat_streaming(self, server_and_client):
        chunks = []
        async for chunk in server_and_client.chat(
            [{"role": "user", "content": "hi"}],
            model="mock/test-model",
            stream=True,
        ):
            chunks.append(chunk)
        assert len(chunks) >= 2
        assert chunks[-1].get("done") is True


class TestClientDaemonNotRunning:
    @pytest.mark.asyncio
    async def test_ping_when_not_running(self, tmp_path):
        client = AsyncModelBusClient(socket_path=tmp_path / "nonexistent.sock")
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_health_raises(self, tmp_path):
        client = AsyncModelBusClient(socket_path=tmp_path / "nonexistent.sock")
        with pytest.raises(DaemonNotRunning):
            await client.health()

    @pytest.mark.asyncio
    async def test_embed_raises(self, tmp_path):
        client = AsyncModelBusClient(socket_path=tmp_path / "nonexistent.sock")
        with pytest.raises(DaemonNotRunning):
            await client.embed(["test"])
