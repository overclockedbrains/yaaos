"""Provider registry and Protocol definition.

All providers implement the InferenceProvider protocol via structural subtyping.
Third-party providers can be discovered via entry_points.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from yaaos_modelbus.types import Chunk, EmbedResult, ModelInfo, ProviderHealth


@runtime_checkable
class InferenceProvider(Protocol):
    """Interface every Model Bus provider must implement.

    Uses typing.Protocol (structural subtyping) so third-party providers
    don't need to import or inherit from this package.
    """

    name: str

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def chat(
        self,
        model: str,
        messages: list,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def embed(
        self,
        model: str,
        texts: list[str],
    ) -> EmbedResult: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def health(self) -> ProviderHealth: ...


def discover_entry_point_providers() -> dict[str, type]:
    """Discover providers registered via entry_points."""
    from importlib.metadata import entry_points

    discovered = {}
    try:
        eps = entry_points(group="yaaos.modelbus.providers")
        for ep in eps:
            try:
                provider_cls = ep.load()
                discovered[ep.name] = provider_cls
            except Exception:
                pass
    except Exception:
        pass
    return discovered
