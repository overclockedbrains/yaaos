"""Stream proxy utilities.

Handles converting provider-specific async iterators into the JSON-RPC
notification/response protocol used by the Model Bus wire format.

The server.py module uses these utilities internally, and they can also
be used by clients that need to consume streaming responses.
"""

from __future__ import annotations

from typing import AsyncIterator

from yaaos_modelbus.types import Chunk


async def collect_stream(stream: AsyncIterator[Chunk]) -> tuple[str, dict | None]:
    """Consume a streaming provider response and return (full_text, usage).

    Useful for non-streaming mode where we need to accumulate all chunks
    into a single response.
    """
    parts: list[str] = []
    usage = None

    async for chunk in stream:
        if chunk.done:
            usage = chunk.usage
            break
        parts.append(chunk.token)

    return "".join(parts), usage


async def stream_to_dicts(stream: AsyncIterator[Chunk]) -> AsyncIterator[dict]:
    """Convert a provider Chunk stream to dicts suitable for JSON-RPC.

    Yields chunk dicts with token/done fields. The server uses these
    to construct JSON-RPC notifications (chunks) and the final response.
    """
    async for chunk in stream:
        yield chunk.to_dict()


async def limit_stream(stream: AsyncIterator[Chunk], max_tokens: int) -> AsyncIterator[Chunk]:
    """Wrap a stream to enforce a maximum token count.

    Stops yielding after max_tokens non-empty chunks and synthesizes
    a done chunk. Useful as a safety limit on runaway generation.
    """
    count = 0
    async for chunk in stream:
        if chunk.done:
            yield chunk
            return
        count += 1
        yield chunk
        if count >= max_tokens:
            yield Chunk(token="", done=True, usage={"truncated": True})
            return
