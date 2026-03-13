"""Embedding provider abstraction — swap between local and cloud models."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Base class for all embedding providers."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        ...

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a single search query."""
        ...

    @property
    @abstractmethod
    def dims(self) -> int:
        """Return the dimensionality of embeddings."""
        ...
