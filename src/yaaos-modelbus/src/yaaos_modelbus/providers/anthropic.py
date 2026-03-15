"""Anthropic provider — cloud inference via Anthropic SDK.

Requires: pip install yaaos-modelbus[anthropic]
API key via env: ANTHROPIC_API_KEY
"""

from __future__ import annotations

from typing import AsyncIterator

from yaaos_modelbus.types import Chunk, EmbedResult, Message, ModelInfo, ProviderHealth

try:
    import anthropic

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


class AnthropicProvider:
    """Anthropic API provider for cloud inference."""

    name = "anthropic"

    def __init__(self, api_key: str):
        if not _HAS_ANTHROPIC:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        raise NotImplementedError("Anthropic does not support embeddings. Use Voyage or Ollama.")

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
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": model or "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if stop:
            kwargs["stop_sequences"] = stop

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield Chunk(token=text)

            final = await stream.get_final_message()
            yield Chunk(
                token="",
                done=True,
                usage={
                    "prompt_tokens": final.usage.input_tokens,
                    "completion_tokens": final.usage.output_tokens,
                },
            )

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stop: list[str] | None = None,
    ) -> AsyncIterator[Chunk]:
        # Separate system message from conversation
        system_msg = None
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                chat_messages.append(m.to_dict())

        kwargs: dict = {
            "model": model or "claude-sonnet-4-20250514",
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if stop:
            kwargs["stop_sequences"] = stop

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield Chunk(token=text)

            final = await stream.get_final_message()
            yield Chunk(
                token="",
                done=True,
                usage={
                    "prompt_tokens": final.usage.input_tokens,
                    "completion_tokens": final.usage.output_tokens,
                },
            )

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="anthropic/claude-sonnet-4-20250514",
                provider="anthropic",
                name="claude-sonnet-4-20250514",
                capabilities=["generate", "chat"],
                context_length=200000,
            ),
            ModelInfo(
                id="anthropic/claude-haiku-4-5-20251001",
                provider="anthropic",
                name="claude-haiku-4-5-20251001",
                capabilities=["generate", "chat"],
                context_length=200000,
            ),
        ]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    async def health(self) -> ProviderHealth:
        try:
            # Quick ping — count tokens for minimal cost
            await self._client.messages.count_tokens(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "hi"}],
            )
            return ProviderHealth(name="anthropic", healthy=True)
        except Exception as e:
            return ProviderHealth(name="anthropic", healthy=False, error=str(e))
