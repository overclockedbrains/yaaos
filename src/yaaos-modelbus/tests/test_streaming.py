"""Tests for streaming utilities."""

from __future__ import annotations

import pytest

from yaaos_modelbus.streaming import collect_stream, limit_stream, stream_to_dicts
from yaaos_modelbus.types import Chunk


async def _make_stream(*chunks):
    for c in chunks:
        yield c


class TestCollectStream:
    @pytest.mark.asyncio
    async def test_collects_text(self):
        stream = _make_stream(
            Chunk(token="Hello"),
            Chunk(token=" world"),
            Chunk(token="", done=True, usage={"tokens": 2}),
        )
        text, usage = await collect_stream(stream)
        assert text == "Hello world"
        assert usage == {"tokens": 2}

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        stream = _make_stream(Chunk(token="", done=True))
        text, usage = await collect_stream(stream)
        assert text == ""

    @pytest.mark.asyncio
    async def test_no_done_chunk(self):
        stream = _make_stream(Chunk(token="partial"))
        text, usage = await collect_stream(stream)
        assert text == "partial"
        assert usage is None


class TestStreamToDicts:
    @pytest.mark.asyncio
    async def test_converts_chunks(self):
        stream = _make_stream(
            Chunk(token="a"),
            Chunk(token="", done=True),
        )
        dicts = [d async for d in stream_to_dicts(stream)]
        assert len(dicts) == 2
        assert dicts[0] == {"token": "a", "done": False}
        assert dicts[1]["done"] is True


class TestLimitStream:
    @pytest.mark.asyncio
    async def test_limits_tokens(self):
        stream = _make_stream(
            Chunk(token="a"),
            Chunk(token="b"),
            Chunk(token="c"),
            Chunk(token="d"),
            Chunk(token="e"),
        )
        chunks = [c async for c in limit_stream(stream, max_tokens=3)]
        assert len(chunks) == 4  # 3 tokens + truncation done
        assert chunks[-1].done is True
        assert chunks[-1].usage == {"truncated": True}

    @pytest.mark.asyncio
    async def test_respects_natural_done(self):
        stream = _make_stream(
            Chunk(token="a"),
            Chunk(token="", done=True, usage={"tokens": 1}),
        )
        chunks = [c async for c in limit_stream(stream, max_tokens=100)]
        assert len(chunks) == 2
        assert chunks[-1].done is True
        assert chunks[-1].usage == {"tokens": 1}
