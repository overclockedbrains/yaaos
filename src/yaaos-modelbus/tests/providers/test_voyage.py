"""Tests for the Voyage provider (mocked SDK calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_voyage_module():
    """Patch the voyageai import inside the provider module."""
    mock_mod = MagicMock()
    mock_mod.AsyncClient = MagicMock
    with patch.dict("sys.modules", {"voyageai": mock_mod}):
        yield mock_mod


@pytest.fixture
def voyage_provider(mock_voyage_module):
    import importlib

    import yaaos_modelbus.providers.voyage as voy_mod

    importlib.reload(voy_mod)

    provider = voy_mod.VoyageProvider(api_key="test-key")
    provider._client = AsyncMock()
    return provider


class TestVoyageEmbed:
    @pytest.mark.asyncio
    async def test_embed_returns_vectors(self, voyage_provider):
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_result.total_tokens = 10

        voyage_provider._client.embed = AsyncMock(return_value=mock_result)

        result = await voyage_provider.embed("voyage-3.5", ["hello", "world"])
        assert result.dims == 3
        assert len(result.embeddings) == 2
        assert result.model == "voyage/voyage-3.5"
        assert result.usage["total_tokens"] == 10

    @pytest.mark.asyncio
    async def test_embed_single_text(self, voyage_provider):
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1, 0.2, 0.3, 0.4, 0.5]]
        mock_result.total_tokens = 3

        voyage_provider._client.embed = AsyncMock(return_value=mock_result)

        result = await voyage_provider.embed("voyage-3.5", ["test"])
        assert result.dims == 5
        assert len(result.embeddings) == 1

    @pytest.mark.asyncio
    async def test_embed_passes_correct_params(self, voyage_provider):
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1]]
        mock_result.total_tokens = 1

        voyage_provider._client.embed = AsyncMock(return_value=mock_result)

        await voyage_provider.embed("voyage-code-3", ["code snippet"])

        voyage_provider._client.embed.assert_called_once_with(
            ["code snippet"],
            model="voyage-code-3",
            input_type="document",
        )


class TestVoyageGenerate:
    @pytest.mark.asyncio
    async def test_generate_raises(self, voyage_provider):
        with pytest.raises(NotImplementedError, match="Voyage only supports embeddings"):
            await voyage_provider.generate("any", "prompt")


class TestVoyageChat:
    @pytest.mark.asyncio
    async def test_chat_raises(self, voyage_provider):
        with pytest.raises(NotImplementedError, match="Voyage only supports embeddings"):
            await voyage_provider.chat("any", [])


class TestVoyageListModels:
    @pytest.mark.asyncio
    async def test_returns_embed_models(self, voyage_provider):
        models = await voyage_provider.list_models()
        assert len(models) == 2
        for m in models:
            assert m.capabilities == ["embed"]
            assert m.provider == "voyage"
        ids = [m.id for m in models]
        assert "voyage/voyage-3.5" in ids
        assert "voyage/voyage-code-3" in ids


class TestVoyageHealth:
    @pytest.mark.asyncio
    async def test_healthy(self, voyage_provider):
        voyage_provider._client.embed = AsyncMock(return_value=MagicMock())
        health = await voyage_provider.health()
        assert health.healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, voyage_provider):
        voyage_provider._client.embed = AsyncMock(side_effect=Exception("rate limited"))
        health = await voyage_provider.health()
        assert health.healthy is False
        assert "rate limited" in health.error
