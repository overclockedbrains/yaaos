"""Asyncio Unix socket server with NDJSON framing and JSON-RPC 2.0 dispatch.

Wire format: each message is a single JSON line terminated by \\n (NDJSON).
Streaming: chunks sent as JSON-RPC notifications (no id), final result as response (with id).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

import orjson
import structlog

from yaaos_modelbus.errors import (
    InvalidRequestError,
    MethodNotFoundError,
    ModelBusError,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

# Type alias for handler functions
# Regular handler: returns a dict result
Handler = Callable[[dict], Awaitable[dict]]
# Streaming handler: yields Chunk dicts, then returns final result
StreamHandler = Callable[[dict], AsyncIterator[dict]]


class JsonRpcServer:
    """Asyncio Unix socket server implementing JSON-RPC 2.0 over NDJSON.

    Usage:
        server = JsonRpcServer(socket_path, max_connections=8)
        server.register("health", handle_health)
        server.register_stream("generate", handle_generate)
        await server.start()
        await server.wait_closed()
    """

    def __init__(
        self,
        socket_path: Path,
        max_connections: int = 8,
        drain_timeout: float = 10.0,
    ):
        self.socket_path = socket_path
        self._semaphore = asyncio.Semaphore(max_connections)
        self._drain_timeout = drain_timeout
        self._handlers: dict[str, Handler] = {}
        self._stream_handlers: dict[str, StreamHandler] = {}
        self._server: asyncio.AbstractServer | None = None
        self._active_connections: set[asyncio.Task] = set()
        self._started_at: float = 0.0
        self._request_count: int = 0
        self._in_flight: int = 0
        self._shutting_down: bool = False

    @property
    def uptime_sec(self) -> float:
        if self._started_at == 0.0:
            return 0.0
        return time.monotonic() - self._started_at

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def register(self, method: str, handler: Handler) -> None:
        """Register a handler for a JSON-RPC method (request/response)."""
        self._handlers[method] = handler

    def register_stream(self, method: str, handler: StreamHandler) -> None:
        """Register a streaming handler for a JSON-RPC method.

        Streaming handlers are async generators that yield chunk dicts.
        The last yielded value is sent as the final JSON-RPC response.
        All prior values are sent as JSON-RPC notifications with method='chunk'.
        """
        self._stream_handlers[method] = handler

    async def start(self) -> None:
        """Start listening on the Unix socket."""
        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
            limit=1024 * 1024,  # 1 MB max line size to prevent memory abuse
        )
        self._started_at = time.monotonic()
        logger.info("server.started", socket=str(self.socket_path))

    async def stop(self) -> None:
        """Gracefully stop the server — drain in-flight requests before shutdown."""
        if self._server is None:
            return

        self._shutting_down = True
        logger.info(
            "server.stopping",
            active_connections=len(self._active_connections),
            in_flight=self._in_flight,
        )

        # Stop accepting new connections
        self._server.close()
        await self._server.wait_closed()

        # Wait for in-flight requests to complete (with configurable timeout)
        if self._active_connections:
            logger.info("server.draining", count=len(self._active_connections))
            done, pending = await asyncio.wait(
                self._active_connections, timeout=self._drain_timeout
            )
            if pending:
                logger.warning("server.force_closing", count=len(pending))
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending)

        # Clean up socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        logger.info("server.stopped", total_requests=self._request_count)

    async def wait_closed(self) -> None:
        """Wait until the server is closed."""
        if self._server:
            await self._server.wait_closed()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection (may receive multiple requests)."""
        task = asyncio.current_task()
        if task:
            self._active_connections.add(task)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # Client disconnected

                await self._process_line(line, writer)
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        except Exception:
            logger.exception("server.connection_error")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if task:
                self._active_connections.discard(task)

    async def _process_line(
        self,
        line: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse and dispatch a single JSON-RPC request."""
        request_id = None
        trace_id = uuid.uuid4().hex[:12]
        start = time.monotonic()

        try:
            msg = orjson.loads(line)
        except (orjson.JSONDecodeError, ValueError):
            await self._send_error(writer, None, InvalidRequestError("Parse error"))
            return

        request_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if not method or not isinstance(method, str):
            await self._send_error(writer, request_id, InvalidRequestError("Missing method"))
            return

        if self._shutting_down:
            from yaaos_modelbus.errors import InternalError

            await self._send_error(writer, request_id, InternalError("Server is shutting down"))
            return

        self._request_count += 1
        self._in_flight += 1
        log = logger.bind(method=method, request_id=request_id, trace_id=trace_id)

        try:
            async with self._semaphore:
                if method in self._stream_handlers:
                    await self._handle_stream(writer, request_id, method, params, log)
                elif method in self._handlers:
                    result = await self._handlers[method](params)
                    await self._send_result(writer, request_id, result)
                else:
                    raise MethodNotFoundError(f"Unknown method: {method}")

            elapsed = time.monotonic() - start
            log.info("request.completed", elapsed_ms=round(elapsed * 1000, 1))

        except ModelBusError as e:
            log.warning("request.error", error=e.message, code=e.code)
            await self._send_error(writer, request_id, e)
        except Exception as e:
            log.exception("request.internal_error")
            from yaaos_modelbus.errors import InternalError

            await self._send_error(writer, request_id, InternalError(str(e)))
        finally:
            self._in_flight -= 1

    async def _handle_stream(
        self,
        writer: asyncio.StreamWriter,
        request_id: Any,
        method: str,
        params: dict,
        log: Any,
    ) -> None:
        """Handle a streaming request — yield chunks as notifications, then final response."""
        stream = params.get("stream", True)
        handler = self._stream_handlers[method]

        chunks: list[str] = []

        async for chunk_data in handler(params):
            if chunk_data.get("done", False):
                # Final result — send as response with id
                await self._send_result(writer, request_id, chunk_data)
                return

            if stream:
                # Send chunk as JSON-RPC notification (no id)
                notification = {
                    "jsonrpc": "2.0",
                    "method": "chunk",
                    "params": {
                        "request_id": request_id,
                        **chunk_data,
                    },
                }
                await self._write_line(writer, notification)
            else:
                # Non-streaming: accumulate tokens
                if "token" in chunk_data:
                    chunks.append(chunk_data["token"])

        # If we didn't get a done=True chunk, synthesize final response
        if not stream and chunks:
            result = {"text": "".join(chunks), "model": params.get("model", "")}
            await self._send_result(writer, request_id, result)
        elif stream:
            # Stream ended without done=True — send empty final response so client doesn't hang
            await self._send_result(
                writer, request_id, {"text": "", "model": params.get("model", ""), "done": True}
            )

    async def _send_result(
        self, writer: asyncio.StreamWriter, request_id: Any, result: dict
    ) -> None:
        """Send a JSON-RPC success response."""
        response = {
            "jsonrpc": "2.0",
            "result": result,
            "id": request_id,
        }
        await self._write_line(writer, response)

    async def _send_error(
        self, writer: asyncio.StreamWriter, request_id: Any, error: ModelBusError
    ) -> None:
        """Send a JSON-RPC error response."""
        response = {
            "jsonrpc": "2.0",
            "error": error.to_jsonrpc_error(),
            "id": request_id,
        }
        await self._write_line(writer, response)

    async def _write_line(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        """Write a single NDJSON line to the client."""
        data = orjson.dumps(msg) + b"\n"
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
            logger.debug("server.write_failed", msg="client disconnected during write")
            raise
