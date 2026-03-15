"""Tests for the request router."""

from __future__ import annotations

import pytest

from yaaos_modelbus.config import Config, ProviderConfig
from yaaos_modelbus.errors import InvalidParamsError, ProviderUnavailableError
from yaaos_modelbus.router import Router


@pytest.fixture
def router_with_mock(mock_provider):
    config = Config(
        providers={"mock": ProviderConfig(name="mock", enabled=True)},
        default_embedding="mock/test-model",
        default_generation="mock/test-model",
        default_chat="mock/test-model",
    )
    return Router(config, registry={"mock": mock_provider})


class TestResolveModel:
    def test_explicit_provider(self, router_with_mock):
        p, m = router_with_mock.resolve_model("mock/test-model", "embed")
        assert p == "mock"
        assert m == "test-model"

    def test_no_model_uses_default(self, router_with_mock):
        p, m = router_with_mock.resolve_model(None, "embed")
        assert p == "mock"
        assert m == "test-model"

    def test_no_prefix_uses_default_provider(self, router_with_mock):
        p, m = router_with_mock.resolve_model("some-model", "generate")
        assert p == "mock"
        assert m == "some-model"


class TestGetProvider:
    def test_registered_provider(self, router_with_mock, mock_provider):
        provider = router_with_mock.get_provider("mock")
        assert provider is mock_provider

    def test_unregistered_provider(self, router_with_mock):
        with pytest.raises(ProviderUnavailableError):
            router_with_mock.get_provider("nonexistent")

    def test_disabled_provider(self, mock_provider):
        config = Config(
            providers={"mock": ProviderConfig(name="mock", enabled=False)},
        )
        router = Router(config, registry={"mock": mock_provider})
        with pytest.raises(ProviderUnavailableError):
            router.get_provider("mock")


class TestHandleEmbed:
    @pytest.mark.asyncio
    async def test_embed_success(self, router_with_mock):
        result = await router_with_mock.handle_embed(
            {
                "texts": ["hello"],
                "model": "mock/test-model",
            }
        )
        assert "embeddings" in result
        assert result["dims"] == 4

    @pytest.mark.asyncio
    async def test_embed_missing_texts(self, router_with_mock):
        with pytest.raises(InvalidParamsError):
            await router_with_mock.handle_embed({"model": "mock/test-model"})

    @pytest.mark.asyncio
    async def test_embed_empty_texts(self, router_with_mock):
        with pytest.raises(InvalidParamsError):
            await router_with_mock.handle_embed({"texts": [], "model": "mock/test-model"})


class TestHandleGenerate:
    @pytest.mark.asyncio
    async def test_generate_yields_chunks(self, router_with_mock):
        chunks = []
        async for chunk in router_with_mock.handle_generate(
            {
                "prompt": "test",
                "model": "mock/test-model",
            }
        ):
            chunks.append(chunk)
        # Should have token chunks + final done
        assert any(c.get("done") for c in chunks)
        tokens = [c["token"] for c in chunks if "token" in c and not c.get("done")]
        assert len(tokens) > 0

    @pytest.mark.asyncio
    async def test_generate_missing_prompt(self, router_with_mock):
        with pytest.raises(InvalidParamsError):
            async for _ in router_with_mock.handle_generate({"model": "mock/test-model"}):
                pass


class TestHandleChat:
    @pytest.mark.asyncio
    async def test_chat_yields_chunks(self, router_with_mock):
        chunks = []
        async for chunk in router_with_mock.handle_chat(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "model": "mock/test-model",
            }
        ):
            chunks.append(chunk)
        assert any(c.get("done") for c in chunks)

    @pytest.mark.asyncio
    async def test_chat_missing_messages(self, router_with_mock):
        with pytest.raises(InvalidParamsError):
            async for _ in router_with_mock.handle_chat({"model": "mock/test-model"}):
                pass


class TestHandleModelsList:
    @pytest.mark.asyncio
    async def test_lists_mock_models(self, router_with_mock):
        result = await router_with_mock.handle_models_list({})
        assert "models" in result
        assert len(result["models"]) >= 1


class TestHandleHealth:
    @pytest.mark.asyncio
    async def test_health_shows_providers(self, router_with_mock):
        result = await router_with_mock.handle_health({})
        assert result["status"] == "healthy"
        assert "mock" in result["providers"]
        assert result["providers"]["mock"]["healthy"] is True
