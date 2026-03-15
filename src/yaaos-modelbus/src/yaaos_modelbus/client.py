"""Model Bus client — sync + async Python SDK.

Used by SFS, CLI, and other YAAOS components to talk to the Model Bus daemon.
Falls back gracefully when the daemon is not running.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import orjson

from yaaos_modelbus.errors import DaemonNotRunning, ModelBusError

_DEFAULT_SOCKET = Path("/run/yaaos/modelbus.sock")
_FALLBACK_SOCKET = Path("~/.local/run/yaaos/modelbus.sock").expanduser()

# Default timeout for waiting on server responses (seconds)
_REQUEST_TIMEOUT = 120.0
_HEALTH_TIMEOUT = 10.0


def _find_socket() -> Path:
    """Find the Model Bus socket, checking default then fallback."""
    if _DEFAULT_SOCKET.exists():
        return _DEFAULT_SOCKET
    if _FALLBACK_SOCKET.exists():
        return _FALLBACK_SOCKET
    return _DEFAULT_SOCKET  # will fail on connect with clear error


class AsyncModelBusClient:
    """Async client for the Model Bus daemon."""

    def __init__(self, socket_path: Path | str | None = None):
        self.socket_path = Path(socket_path) if socket_path else _find_socket()
        self._id_counter = 0

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a connection to the daemon socket."""
        try:
            return await asyncio.open_unix_connection(str(self.socket_path))
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            raise DaemonNotRunning(
                f"Model Bus daemon not running (socket: {self.socket_path}): {e}"
            ) from e

    async def _request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and return the result."""
        reader, writer = await self._connect()
        timeout = _HEALTH_TIMEOUT if method == "health" else _REQUEST_TIMEOUT
        try:
            req_id = self._next_id()
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": req_id,
            }
            writer.write(orjson.dumps(msg) + b"\n")
            await writer.drain()

            # Read response — skip any chunk notifications
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
                except TimeoutError:
                    raise DaemonNotRunning(
                        f"Server did not respond within {timeout}s for '{method}'"
                    )
                if not line:
                    raise DaemonNotRunning("Connection closed unexpectedly")

                response = orjson.loads(line)

                # Skip notifications (no id)
                if "id" not in response:
                    continue

                if "error" in response:
                    err = response["error"]
                    raise ModelBusError(err.get("message", "Unknown error"), err.get("data"))

                return response.get("result", {})
        finally:
            writer.close()
            await writer.wait_closed()

    async def _stream_request(self, method: str, params: dict) -> AsyncIterator[dict]:
        """Send a JSON-RPC request and yield streaming chunks + final result."""
        reader, writer = await self._connect()
        got_final = False
        try:
            req_id = self._next_id()
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": req_id,
            }
            writer.write(orjson.dumps(msg) + b"\n")
            await writer.drain()

            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=_REQUEST_TIMEOUT)
                except TimeoutError:
                    raise DaemonNotRunning(f"Server stopped responding during streaming '{method}'")
                if not line:
                    break

                response = orjson.loads(line)

                if "error" in response:
                    err = response["error"]
                    raise ModelBusError(err.get("message", "Unknown error"), err.get("data"))

                # Notification (chunk)
                if "id" not in response and response.get("method") == "chunk":
                    yield response.get("params", {})
                    continue

                # Final response
                if "id" in response:
                    got_final = True
                    yield {"done": True, **(response.get("result", {}))}
                    break

            # Stream ended without a final response — server likely crashed
            if not got_final:
                yield {"done": True, "text": "", "error": "stream_truncated"}
        finally:
            writer.close()
            await writer.wait_closed()

    # ── Public API ──────────────────────────────────────────────

    async def ping(self) -> bool:
        """Check if the daemon is reachable."""
        try:
            result = await self._request("health")
            return result.get("status") in ("healthy", "degraded")
        except (DaemonNotRunning, Exception):
            return False

    async def health(self) -> dict:
        """Get full health status."""
        return await self._request("health")

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> dict:
        """Embed texts and return vectors."""
        params: dict = {"texts": texts}
        if model:
            params["model"] = model
        return await self._request("embed", params)

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = True,
    ) -> AsyncIterator[dict]:
        """Generate text, streaming chunks."""
        params: dict = {
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if model:
            params["model"] = model
        if system:
            params["system"] = system

        async for chunk in self._stream_request("generate", params):
            yield chunk

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = True,
    ) -> AsyncIterator[dict]:
        """Chat completion, streaming chunks."""
        params: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if model:
            params["model"] = model

        async for chunk in self._stream_request("chat", params):
            yield chunk

    async def list_models(self) -> list[dict]:
        """List available models across all providers."""
        result = await self._request("models.list")
        return result.get("models", [])


class ModelBusClient:
    """Synchronous wrapper around AsyncModelBusClient.

    Convenience class for non-async code (CLI, SFS integration).
    """

    def __init__(self, socket_path: Path | str | None = None):
        self._async = AsyncModelBusClient(socket_path)

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an event loop — create a new thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    def ping(self) -> bool:
        return self._run(self._async.ping())

    def health(self) -> dict:
        return self._run(self._async.health())

    def embed(self, texts: list[str], model: str | None = None) -> dict:
        return self._run(self._async.embed(texts, model))

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        **kwargs,
    ) -> str:
        """Generate text (non-streaming, returns full text)."""

        async def _collect():
            text_parts = []
            async for chunk in self._async.generate(prompt, model, stream=False, **kwargs):
                if chunk.get("done"):
                    return chunk.get("text", "".join(text_parts))
                if "token" in chunk:
                    text_parts.append(chunk["token"])
            return "".join(text_parts)

        return self._run(_collect())

    def list_models(self) -> list[dict]:
        return self._run(self._async.list_models())
