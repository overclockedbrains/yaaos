"""Full-stack integration tests — socket → router → Ollama → response.

These tests verify Phase B success criteria:
"socat embed through socket returns embeddings"

Tests require Ollama running at localhost:11434 with at least one model.
Skipped automatically if Ollama is not available.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import orjson
import pytest

from yaaos_modelbus.config import Config, ProviderConfig
from yaaos_modelbus.providers.ollama import OllamaProvider
from yaaos_modelbus.router import Router
from yaaos_modelbus.server import JsonRpcServer

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available"),
]


def _ollama_available():
    try:
        resp = httpx.get("http://localhost:11434/", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


skip_no_ollama = pytest.mark.skipif(
    not _ollama_available(),
    reason="Ollama not running at localhost:11434",
)


async def _send_request(socket_path, method, params=None, req_id=1):
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": req_id}
        writer.write(orjson.dumps(msg) + b"\n")
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                raise RuntimeError("Connection closed")
            response = orjson.loads(line)
            if "id" in response:
                return response
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_streaming(socket_path, method, params, req_id=1):
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        msg = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
        writer.write(orjson.dumps(msg) + b"\n")
        await writer.drain()
        chunks = []
        final = None
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30.0)
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


@pytest.fixture
async def ollama_server(tmp_path):
    """Start a real server with the Ollama provider."""
    socket_path = tmp_path / "integration.sock"

    provider = OllamaProvider(base_url="http://localhost:11434")
    config = Config(
        socket_path=socket_path,
        providers={
            "ollama": ProviderConfig(name="ollama", enabled=True, base_url="http://localhost:11434")
        },
        default_embedding="ollama/nomic-embed-text",
        default_generation="ollama/phi3:mini",
        default_chat="ollama/phi3:mini",
    )
    router = Router(config, registry={"ollama": provider})

    server = JsonRpcServer(socket_path=socket_path, max_connections=4)
    server.register("health", router.handle_health)
    server.register("embed", router.handle_embed)
    server.register("models.list", router.handle_models_list)
    server.register_stream("generate", router.handle_generate)
    server.register_stream("chat", router.handle_chat)

    await server.start()
    yield socket_path
    await server.stop()
    await provider.close()


@pytest.mark.integration
@skip_no_ollama
class TestFullStackHealth:
    @pytest.mark.asyncio
    async def test_health_through_socket(self, ollama_server):
        """Connect to socket → health → Ollama health check → response."""
        response = await _send_request(ollama_server, "health")
        assert "result" in response
        result = response["result"]
        assert result["status"] in ("healthy", "degraded")
        assert "ollama" in result["providers"]
        assert result["providers"]["ollama"]["healthy"] is True


@pytest.mark.integration
@skip_no_ollama
class TestFullStackModelsList:
    @pytest.mark.asyncio
    async def test_models_list_through_socket(self, ollama_server):
        response = await _send_request(ollama_server, "models.list")
        result = response["result"]
        assert "models" in result
        assert len(result["models"]) > 0
        # All models should have ollama/ prefix
        for m in result["models"]:
            assert m["id"].startswith("ollama/")


@pytest.mark.integration
@skip_no_ollama
class TestFullStackEmbed:
    @pytest.mark.asyncio
    async def test_embed_through_socket(self, ollama_server):
        """Phase B success criteria: embed through socket returns vectors."""
        # First find an available embed model
        models_resp = await _send_request(ollama_server, "models.list")
        embed_models = [
            m for m in models_resp["result"]["models"] if "embed" in m.get("capabilities", [])
        ]
        if not embed_models:
            pytest.skip("No embedding model in Ollama")

        model_id = embed_models[0]["id"]

        response = await _send_request(
            ollama_server,
            "embed",
            params={"texts": ["hello world"], "model": model_id},
        )
        assert "result" in response, f"Got error: {response.get('error')}"
        result = response["result"]
        assert "embeddings" in result
        assert len(result["embeddings"]) == 1
        assert result["dims"] > 0
        assert len(result["embeddings"][0]) == result["dims"]


@pytest.mark.integration
@skip_no_ollama
class TestFullStackGenerate:
    @pytest.mark.asyncio
    async def test_generate_streaming_through_socket(self, ollama_server):
        """Generate streams tokens through the socket."""
        models_resp = await _send_request(ollama_server, "models.list")
        gen_models = [
            m for m in models_resp["result"]["models"] if "generate" in m.get("capabilities", [])
        ]
        if not gen_models:
            pytest.skip("No generation model in Ollama")

        model_id = gen_models[0]["id"]

        chunks, final = await _send_streaming(
            ollama_server,
            "generate",
            params={
                "prompt": "Say hello in one word.",
                "model": model_id,
                "max_tokens": 10,
                "stream": True,
            },
        )

        assert len(chunks) >= 1, "Should receive at least one chunk"
        assert final is not None, "Should receive final response"
        assert "result" in final


@pytest.mark.integration
@skip_no_ollama
class TestFullStackChat:
    @pytest.mark.asyncio
    async def test_chat_streaming_through_socket(self, ollama_server):
        models_resp = await _send_request(ollama_server, "models.list")
        chat_models = [
            m for m in models_resp["result"]["models"] if "chat" in m.get("capabilities", [])
        ]
        if not chat_models:
            pytest.skip("No chat model in Ollama")

        model_id = chat_models[0]["id"]

        chunks, final = await _send_streaming(
            ollama_server,
            "chat",
            params={
                "messages": [{"role": "user", "content": "Say hi in one word."}],
                "model": model_id,
                "max_tokens": 10,
                "stream": True,
            },
        )

        assert len(chunks) >= 1
        assert final is not None
