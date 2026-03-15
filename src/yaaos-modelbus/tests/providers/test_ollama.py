"""Tests for the Ollama provider.

Unit tests use httpx MockTransport (no real Ollama needed).
Integration tests require Ollama running and are marked with @pytest.mark.integration.
"""

from __future__ import annotations

import json

import httpx
import pytest

from yaaos_modelbus.providers.ollama import OllamaProvider
from yaaos_modelbus.types import Message


# ── Helpers ──────────────────────────────────────────────────


def _mock_transport(handler):
    """Create a mock httpx transport from a handler function."""
    return httpx.MockTransport(handler)


def _ndjson_response(*lines):
    """Build a streaming NDJSON response."""
    body = "\n".join(json.dumps(line) for line in lines)
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/x-ndjson"}
    )


# ── Unit tests (mocked HTTP) ────────────────────────────────


class TestOllamaEmbed:
    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        def handler(request: httpx.Request):
            data = json.loads(request.content)
            assert data["model"] == "nomic-embed-text"
            assert data["input"] == ["hello"]
            return httpx.Response(
                200,
                json={
                    "embeddings": [[0.1, 0.2, 0.3]],
                    "prompt_eval_count": 1,
                },
            )

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        result = await provider.embed("nomic-embed-text", ["hello"])
        assert result.dims == 3
        assert len(result.embeddings) == 1
        assert result.model == "ollama/nomic-embed-text"
        assert result.usage["prompt_tokens"] == 1

    @pytest.mark.asyncio
    async def test_embed_multiple_texts(self):
        def handler(request: httpx.Request):
            data = json.loads(request.content)
            n = len(data["input"])
            return httpx.Response(
                200,
                json={"embeddings": [[0.1, 0.2]] * n},
            )

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        result = await provider.embed("test", ["a", "b", "c"])
        assert len(result.embeddings) == 3


class TestOllamaGenerate:
    @pytest.mark.asyncio
    async def test_generate_streams(self):
        def handler(request: httpx.Request):
            lines = [
                {"response": "Hello", "done": False},
                {"response": " world", "done": False},
                {"response": "", "done": True, "prompt_eval_count": 5, "eval_count": 2},
            ]
            body = "\n".join(json.dumps(item) for item in lines)
            return httpx.Response(200, content=body.encode())

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        chunks = []
        async for chunk in provider.generate("phi3:mini", "test prompt"):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].token == "Hello"
        assert chunks[1].token == " world"
        assert chunks[2].done is True
        assert chunks[2].usage["prompt_tokens"] == 5
        assert chunks[2].usage["completion_tokens"] == 2

    @pytest.mark.asyncio
    async def test_generate_with_system(self):
        def handler(request: httpx.Request):
            data = json.loads(request.content)
            assert data["system"] == "You are helpful."
            return httpx.Response(
                200,
                content=json.dumps({"response": "", "done": True}).encode(),
            )

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        chunks = [c async for c in provider.generate("phi3:mini", "hi", system="You are helpful.")]
        assert any(c.done for c in chunks)


class TestOllamaChat:
    @pytest.mark.asyncio
    async def test_chat_streams(self):
        def handler(request: httpx.Request):
            data = json.loads(request.content)
            assert len(data["messages"]) == 1
            assert data["messages"][0]["role"] == "user"
            lines = [
                {"message": {"content": "Hi"}, "done": False},
                {"message": {"content": "!"}, "done": False},
                {"done": True, "prompt_eval_count": 3, "eval_count": 2},
            ]
            body = "\n".join(json.dumps(item) for item in lines)
            return httpx.Response(200, content=body.encode())

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        chunks = []
        async for chunk in provider.chat("phi3:mini", [Message(role="user", content="hello")]):
            chunks.append(chunk)

        assert chunks[0].token == "Hi"
        assert chunks[1].token == "!"
        assert chunks[2].done is True


class TestOllamaListModels:
    @pytest.mark.asyncio
    async def test_list_models(self):
        def handler(request: httpx.Request):
            if "/api/tags" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "phi3:mini",
                                "details": {
                                    "family": "phi3",
                                    "quantization_level": "Q4_K_M",
                                },
                            },
                            {
                                "name": "nomic-embed-text:latest",
                                "details": {"family": "nomic-bert"},
                            },
                        ]
                    },
                )
            return httpx.Response(200)

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        models = await provider.list_models()
        assert len(models) == 2

        phi = models[0]
        assert phi.id == "ollama/phi3:mini"
        assert phi.capabilities == ["generate", "chat"]
        assert phi.params_billions == 3.8
        assert phi.quantization == "Q4_K_M"

        nomic = models[1]
        assert "embed" in nomic.capabilities

    @pytest.mark.asyncio
    async def test_list_models_ollama_down(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("Connection refused")

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        models = await provider.list_models()
        assert models == []


class TestOllamaHealth:
    @pytest.mark.asyncio
    async def test_healthy(self):
        def handler(request: httpx.Request):
            if "/api/ps" in str(request.url):
                return httpx.Response(200, json={"models": [{"name": "phi3:mini"}]})
            return httpx.Response(200, text="Ollama is running")

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        health = await provider.health()
        assert health.healthy is True
        assert "phi3:mini" in health.models_loaded

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("Connection refused")

        provider = OllamaProvider(base_url="http://test")
        provider._client = httpx.AsyncClient(
            base_url="http://test", transport=_mock_transport(handler)
        )

        health = await provider.health()
        assert health.healthy is False
        assert health.error is not None


# ── Integration tests (require real Ollama) ──────────────────


def _ollama_available():
    """Check if Ollama is reachable at localhost:11434."""
    try:
        resp = httpx.get("http://localhost:11434/", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark_integration = pytest.mark.skipif(
    not _ollama_available(),
    reason="Ollama not running at localhost:11434",
)


@pytest.mark.integration
@pytestmark_integration
class TestOllamaIntegrationHealth:
    @pytest.mark.asyncio
    async def test_health_real(self):
        provider = OllamaProvider()
        health = await provider.health()
        assert health.healthy is True
        await provider.close()

    @pytest.mark.asyncio
    async def test_list_models_real(self):
        provider = OllamaProvider()
        models = await provider.list_models()
        assert isinstance(models, list)
        # At least one model should be available if Ollama is running
        await provider.close()


@pytest.mark.integration
@pytestmark_integration
class TestOllamaIntegrationEmbed:
    @pytest.mark.asyncio
    async def test_embed_real(self):
        provider = OllamaProvider()
        # Try with whatever embedding model is available
        models = await provider.list_models()
        embed_models = [m for m in models if "embed" in m.capabilities]
        if not embed_models:
            pytest.skip("No embedding model available in Ollama")

        model_name = embed_models[0].name
        result = await provider.embed(model_name, ["hello world"])
        assert len(result.embeddings) == 1
        assert result.dims > 0
        assert len(result.embeddings[0]) == result.dims
        await provider.close()


@pytest.mark.integration
@pytestmark_integration
class TestOllamaIntegrationGenerate:
    @pytest.mark.asyncio
    async def test_generate_real(self):
        provider = OllamaProvider()
        models = await provider.list_models()
        gen_models = [m for m in models if "generate" in m.capabilities]
        if not gen_models:
            pytest.skip("No generation model available in Ollama")

        model_name = gen_models[0].name
        chunks = []
        async for chunk in provider.generate(model_name, "Say hello in one word.", max_tokens=10):
            chunks.append(chunk)

        assert len(chunks) >= 2  # at least one token + done
        assert any(c.done for c in chunks)
        tokens = [c.token for c in chunks if c.token and not c.done]
        assert len(tokens) > 0
        await provider.close()


@pytest.mark.integration
@pytestmark_integration
class TestOllamaIntegrationChat:
    @pytest.mark.asyncio
    async def test_chat_real(self):
        provider = OllamaProvider()
        models = await provider.list_models()
        chat_models = [m for m in models if "chat" in m.capabilities]
        if not chat_models:
            pytest.skip("No chat model available in Ollama")

        model_name = chat_models[0].name
        chunks = []
        async for chunk in provider.chat(
            model_name,
            [Message(role="user", content="Say hi in one word.")],
            max_tokens=10,
        ):
            chunks.append(chunk)

        assert len(chunks) >= 2
        assert any(c.done for c in chunks)
        await provider.close()
