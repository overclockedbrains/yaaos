"""Shared test fixtures for Model Bus tests."""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from yaaos_modelbus.config import Config, ProviderConfig
from yaaos_modelbus.types import Chunk, EmbedResult, ModelInfo, ProviderHealth


class MockProvider:
    """A mock provider for testing the server/router stack without real AI backends."""

    name = "mock"

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        # Return deterministic fake embeddings
        dims = 4
        embeddings = [[float(i + j) / 10 for j in range(dims)] for i in range(len(texts))]
        return EmbedResult(
            embeddings=embeddings,
            model=f"mock/{model}",
            dims=dims,
            usage={"total_tokens": sum(len(t.split()) for t in texts)},
        )

    async def generate(
        self, model, prompt, *, system=None, temperature=0.7, max_tokens=2048, stop=None
    ) -> AsyncIterator[Chunk]:
        # Yield a few mock tokens then done
        for word in ["Hello", " from", " mock"]:
            yield Chunk(token=word)
        yield Chunk(token="", done=True, usage={"prompt_tokens": 5, "completion_tokens": 3})

    async def chat(
        self, model, messages, *, temperature=0.7, max_tokens=2048, stop=None
    ) -> AsyncIterator[Chunk]:
        for word in ["Mock", " chat", " response"]:
            yield Chunk(token=word)
        yield Chunk(token="", done=True, usage={"prompt_tokens": 10, "completion_tokens": 3})

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="mock/test-model",
                provider="mock",
                name="test-model",
                capabilities=["generate", "chat", "embed"],
                embedding_dims=4,
            ),
        ]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(name="mock", healthy=True, models_loaded=["test-model"])


@pytest.fixture
def mock_provider():
    return MockProvider()


@pytest.fixture
def tmp_socket(tmp_path):
    """Return a temporary Unix socket path."""
    return tmp_path / "test_modelbus.sock"


@pytest.fixture
def test_config(tmp_socket):
    """Return a Config pointing to the temp socket with mock provider."""
    return Config(
        socket_path=tmp_socket,
        log_level="debug",
        max_concurrent_requests=4,
        providers={"mock": ProviderConfig(name="mock", enabled=True)},
    )
