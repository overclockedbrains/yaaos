"""Tests for the Anthropic provider (mocked SDK calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_anthropic_module():
    """Patch the anthropic import inside the provider module."""
    mock_mod = MagicMock()
    mock_mod.AsyncAnthropic = MagicMock
    with patch.dict("sys.modules", {"anthropic": mock_mod}):
        yield mock_mod


@pytest.fixture
def anthropic_provider(mock_anthropic_module):
    import importlib

    import yaaos_modelbus.providers.anthropic as anth_mod

    importlib.reload(anth_mod)

    provider = anth_mod.AnthropicProvider(api_key="test-key")
    provider._client = AsyncMock()
    return provider


class TestAnthropicEmbed:
    @pytest.mark.asyncio
    async def test_embed_raises(self, anthropic_provider):
        with pytest.raises(NotImplementedError, match="Anthropic does not support embeddings"):
            await anthropic_provider.embed("some-model", ["hello"])


class TestAnthropicGenerate:
    @pytest.mark.asyncio
    async def test_generate_streams(self, anthropic_provider):
        # Mock the streaming context manager
        mock_final = MagicMock()
        mock_final.usage.input_tokens = 5
        mock_final.usage.output_tokens = 3

        mock_stream_ctx = AsyncMock()

        async def mock_text_stream():
            for text in ["Hello", " world"]:
                yield text

        mock_stream_ctx.text_stream = mock_text_stream()
        mock_stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

        # Make the context manager return our mock
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        anthropic_provider._client.messages.stream = MagicMock(return_value=mock_cm)

        chunks = []
        async for chunk in anthropic_provider.generate("claude-sonnet-4-20250514", "test"):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].token == "Hello"
        assert chunks[1].token == " world"
        assert chunks[2].done is True
        assert chunks[2].usage["prompt_tokens"] == 5
        assert chunks[2].usage["completion_tokens"] == 3

    @pytest.mark.asyncio
    async def test_generate_with_system(self, anthropic_provider):
        mock_final = MagicMock()
        mock_final.usage.input_tokens = 3
        mock_final.usage.output_tokens = 1

        mock_stream_ctx = AsyncMock()

        async def mock_text_stream():
            yield "OK"

        mock_stream_ctx.text_stream = mock_text_stream()
        mock_stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        anthropic_provider._client.messages.stream = MagicMock(return_value=mock_cm)

        # Consume the generator to trigger the stream call
        [
            c
            async for c in anthropic_provider.generate(
                "claude-sonnet-4-20250514", "hi", system="Be helpful"
            )
        ]
        # Verify system was passed to the stream call
        call_kwargs = anthropic_provider._client.messages.stream.call_args[1]
        assert call_kwargs["system"] == "Be helpful"


class TestAnthropicChat:
    @pytest.mark.asyncio
    async def test_chat_separates_system_message(self, anthropic_provider):
        from yaaos_modelbus.types import Message

        mock_final = MagicMock()
        mock_final.usage.input_tokens = 8
        mock_final.usage.output_tokens = 2

        mock_stream_ctx = AsyncMock()

        async def mock_text_stream():
            yield "Sure"

        mock_stream_ctx.text_stream = mock_text_stream()
        mock_stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        anthropic_provider._client.messages.stream = MagicMock(return_value=mock_cm)

        messages = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="hello"),
        ]
        [c async for c in anthropic_provider.chat("claude-sonnet-4-20250514", messages)]

        # System message should be separated
        call_kwargs = anthropic_provider._client.messages.stream.call_args[1]
        assert call_kwargs["system"] == "You are helpful"
        # Only user message in the messages list
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"


class TestAnthropicListModels:
    @pytest.mark.asyncio
    async def test_returns_static_list(self, anthropic_provider):
        models = await anthropic_provider.list_models()
        assert len(models) == 2
        ids = [m.id for m in models]
        assert "anthropic/claude-sonnet-4-20250514" in ids
        assert "anthropic/claude-haiku-4-5-20251001" in ids
        for m in models:
            assert "generate" in m.capabilities
            assert "chat" in m.capabilities


class TestAnthropicHealth:
    @pytest.mark.asyncio
    async def test_healthy(self, anthropic_provider):
        anthropic_provider._client.messages.count_tokens = AsyncMock(return_value=MagicMock())
        health = await anthropic_provider.health()
        assert health.healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, anthropic_provider):
        anthropic_provider._client.messages.count_tokens = AsyncMock(
            side_effect=Exception("invalid api key")
        )
        health = await anthropic_provider.health()
        assert health.healthy is False
        assert "invalid api key" in health.error
