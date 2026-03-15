"""Tests for the OpenAI provider (mocked SDK calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We mock the openai module itself so tests run without the SDK installed.


@pytest.fixture
def mock_openai_module():
    """Patch the openai import inside the provider module."""
    mock_mod = MagicMock()
    mock_mod.AsyncOpenAI = MagicMock
    with patch.dict("sys.modules", {"openai": mock_mod}):
        yield mock_mod


@pytest.fixture
def openai_provider(mock_openai_module):
    """Create an OpenAIProvider with a mocked SDK client."""
    # Need to re-import after patching so it picks up the mock
    import importlib

    import yaaos_modelbus.providers.openai as oai_mod

    importlib.reload(oai_mod)

    provider = oai_mod.OpenAIProvider(api_key="test-key")
    provider._client = AsyncMock()
    return provider


class TestOpenAIEmbed:
    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self, openai_provider):
        # Mock the embeddings.create response
        mock_item_1 = MagicMock()
        mock_item_1.embedding = [0.1, 0.2, 0.3]
        mock_item_2 = MagicMock()
        mock_item_2.embedding = [0.4, 0.5, 0.6]

        mock_response = MagicMock()
        mock_response.data = [mock_item_1, mock_item_2]
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.total_tokens = 5

        openai_provider._client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await openai_provider.embed("text-embedding-3-small", ["hello", "world"])
        assert result.dims == 3
        assert len(result.embeddings) == 2
        assert result.model == "openai/text-embedding-3-small"
        assert result.usage["prompt_tokens"] == 5

    @pytest.mark.asyncio
    async def test_embed_single_text(self, openai_provider):
        mock_item = MagicMock()
        mock_item.embedding = [0.1, 0.2, 0.3, 0.4]

        mock_response = MagicMock()
        mock_response.data = [mock_item]
        mock_response.usage.prompt_tokens = 2
        mock_response.usage.total_tokens = 2

        openai_provider._client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await openai_provider.embed("text-embedding-3-small", ["hello"])
        assert result.dims == 4
        assert len(result.embeddings) == 1


class TestOpenAIGenerate:
    @pytest.mark.asyncio
    async def test_generate_streams_chunks(self, openai_provider):
        # Build mock stream events
        event1 = MagicMock()
        event1.choices = [MagicMock()]
        event1.choices[0].delta.content = "Hello"
        event1.choices[0].finish_reason = None

        event2 = MagicMock()
        event2.choices = [MagicMock()]
        event2.choices[0].delta.content = " world"
        event2.choices[0].finish_reason = None

        event3 = MagicMock()
        event3.choices = [MagicMock()]
        event3.choices[0].delta.content = None
        event3.choices[0].finish_reason = "stop"

        async def mock_stream():
            for e in [event1, event2, event3]:
                yield e

        openai_provider._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        chunks = []
        async for chunk in openai_provider.generate("gpt-4o-mini", "Say hello"):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].token == "Hello"
        assert chunks[1].token == " world"
        assert chunks[2].done is True

    @pytest.mark.asyncio
    async def test_generate_with_system(self, openai_provider):
        event = MagicMock()
        event.choices = [MagicMock()]
        event.choices[0].delta.content = None
        event.choices[0].finish_reason = "stop"

        async def mock_stream():
            yield event

        openai_provider._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        chunks = [
            c async for c in openai_provider.generate("gpt-4o-mini", "hi", system="Be helpful")
        ]
        assert any(c.done for c in chunks)


class TestOpenAIChat:
    @pytest.mark.asyncio
    async def test_chat_streams(self, openai_provider):
        from yaaos_modelbus.types import Message

        event1 = MagicMock()
        event1.choices = [MagicMock()]
        event1.choices[0].delta.content = "Hi!"
        event1.choices[0].finish_reason = None

        event2 = MagicMock()
        event2.choices = [MagicMock()]
        event2.choices[0].delta.content = None
        event2.choices[0].finish_reason = "stop"

        async def mock_stream():
            for e in [event1, event2]:
                yield e

        openai_provider._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        chunks = []
        async for chunk in openai_provider.chat(
            "gpt-4o-mini",
            [Message(role="user", content="hello")],
        ):
            chunks.append(chunk)

        assert chunks[0].token == "Hi!"
        assert chunks[1].done is True


class TestOpenAIListModels:
    @pytest.mark.asyncio
    async def test_returns_static_list(self, openai_provider):
        models = await openai_provider.list_models()
        assert len(models) == 4
        ids = [m.id for m in models]
        assert "openai/gpt-4o" in ids
        assert "openai/text-embedding-3-small" in ids


class TestOpenAIHealth:
    @pytest.mark.asyncio
    async def test_healthy(self, openai_provider):
        openai_provider._client.models.list = AsyncMock(return_value=[])
        health = await openai_provider.health()
        assert health.healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, openai_provider):
        openai_provider._client.models.list = AsyncMock(side_effect=Exception("auth error"))
        health = await openai_provider.health()
        assert health.healthy is False
        assert "auth error" in health.error
