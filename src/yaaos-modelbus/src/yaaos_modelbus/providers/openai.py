"""OpenAI provider — cloud inference via OpenAI SDK.

Requires: pip install yaaos-modelbus[openai]
API key via env: OPENAI_API_KEY
"""

from __future__ import annotations

from typing import AsyncIterator

from yaaos_modelbus.types import Chunk, EmbedResult, Message, ModelInfo, ProviderHealth

try:
    import openai

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


class OpenAIProvider:
    """OpenAI API provider for cloud inference."""

    name = "openai"

    def __init__(self, api_key: str):
        if not _HAS_OPENAI:
            raise ImportError("openai package not installed. Run: pip install openai")
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        response = await self._client.embeddings.create(
            model=model or "text-embedding-3-small",
            input=texts,
        )
        embeddings = [item.embedding for item in response.data]
        dims = len(embeddings[0]) if embeddings else 0

        return EmbedResult(
            embeddings=embeddings,
            model=f"openai/{model}",
            dims=dims,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async for chunk in self._stream_chat(model, messages, temperature, max_tokens, stop):
            yield chunk

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]:
        raw_messages = [m.to_dict() for m in messages]
        async for chunk in self._stream_chat(model, raw_messages, temperature, max_tokens, stop):
            yield chunk

    async def _stream_chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
    ) -> AsyncIterator[Chunk]:
        stream = await self._client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            stream=True,
        )
        async for event in stream:
            choice = event.choices[0] if event.choices else None
            if choice and choice.delta and choice.delta.content:
                yield Chunk(token=choice.delta.content)
            if choice and choice.finish_reason:
                yield Chunk(
                    token="",
                    done=True,
                    usage={"model": f"openai/{model}", "finish_reason": choice.finish_reason},
                )
                return

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="openai/gpt-4o",
                provider="openai",
                name="gpt-4o",
                capabilities=["generate", "chat"],
            ),
            ModelInfo(
                id="openai/gpt-4o-mini",
                provider="openai",
                name="gpt-4o-mini",
                capabilities=["generate", "chat"],
            ),
            ModelInfo(
                id="openai/text-embedding-3-small",
                provider="openai",
                name="text-embedding-3-small",
                capabilities=["embed"],
                embedding_dims=1536,
            ),
            ModelInfo(
                id="openai/text-embedding-3-large",
                provider="openai",
                name="text-embedding-3-large",
                capabilities=["embed"],
                embedding_dims=3072,
            ),
        ]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    async def health(self) -> ProviderHealth:
        try:
            await self._client.models.list()
            return ProviderHealth(name="openai", healthy=True)
        except Exception as e:
            return ProviderHealth(name="openai", healthy=False, error=str(e))
