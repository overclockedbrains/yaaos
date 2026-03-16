"""Agent Bus JSON-RPC server — asyncio Unix socket with NDJSON framing.

Exposes supervisor management, agent status, and tool registry operations
over JSON-RPC 2.0, reusing the same wire format as Model Bus.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import orjson
import structlog

from yaaos_agentd.errors import (
    AgentdError,
    InternalError,
    InvalidRequestError,
    MethodNotFoundError,
)

if TYPE_CHECKING:
    from yaaos_agentd.supervisor import Supervisor
    from yaaos_agentd.tools.registry import ToolRegistry

logger = structlog.get_logger()

# Type alias for handler functions
Handler = Callable[[dict], Awaitable[dict]]


class AgentBusServer:
    """JSON-RPC 2.0 server over Unix socket for SystemAgentd.

    Registers handlers for agent management, tool invocation, and health
    reporting. Follows the same NDJSON framing as Model Bus.

    Usage:
        server = AgentBusServer(socket_path, supervisor, registry)
        await server.start()
        ...
        await server.stop()
    """

    def __init__(
        self,
        socket_path: Path,
        supervisor: Supervisor,
        registry: ToolRegistry | None = None,
        *,
        max_connections: int = 8,
        drain_timeout: float = 10.0,
        config_path: Path | None = None,
    ):
        self.socket_path = socket_path
        self._supervisor = supervisor
        self._registry = registry
        self._config_path = config_path
        self._semaphore = asyncio.Semaphore(max_connections)
        self._drain_timeout = drain_timeout
        self._handlers: dict[str, Handler] = {}
        self._server: asyncio.AbstractServer | None = None
        self._active_connections: set[asyncio.Task] = set()
        self._started_at: float = 0.0
        self._request_count: int = 0
        self._in_flight: int = 0
        self._shutting_down: bool = False
        self._log = logger.bind(component="agentbus_server")

        self._register_builtin_handlers()

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
        """Register a custom handler for a JSON-RPC method."""
        self._handlers[method] = handler

    # ── Built-in Handlers ────────────────────────────────────────

    def _register_builtin_handlers(self) -> None:
        """Wire up all built-in JSON-RPC methods."""
        # Health
        self._handlers["health"] = self._handle_health

        # Agent management
        self._handlers["agents.list"] = self._handle_agents_list
        self._handlers["agents.status"] = self._handle_agents_status
        self._handlers["agents.start"] = self._handle_agents_start
        self._handlers["agents.stop"] = self._handle_agents_stop
        self._handlers["agents.restart"] = self._handle_agents_restart
        self._handlers["agents.logs"] = self._handle_agents_logs

        # Tool registry
        self._handlers["tools.list"] = self._handle_tools_list
        self._handlers["tools.schema"] = self._handle_tools_schema
        self._handlers["tools.invoke"] = self._handle_tools_invoke

        # Config
        self._handlers["config.reload"] = self._handle_config_reload

    async def _handle_health(self, params: dict) -> dict:
        health = self._supervisor.get_health()
        result = health.to_dict()
        result["server"] = {
            "uptime_sec": round(self.uptime_sec, 1),
            "request_count": self._request_count,
            "in_flight": self._in_flight,
        }
        return result

    async def _handle_agents_list(self, params: dict) -> dict:
        statuses = self._supervisor.get_all_statuses()
        return {
            "agents": [s.to_dict() for s in statuses.values()],
        }

    async def _handle_agents_status(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: name")

        status = self._supervisor.get_agent_status(name)
        if status is None:
            from yaaos_agentd.errors import AgentNotFoundError
            raise AgentNotFoundError(name)

        return status.to_dict()

    async def _handle_agents_start(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: name")

        await self._supervisor.start_agent(name)
        return {"status": "started", "agent": name}

    async def _handle_agents_stop(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: name")

        await self._supervisor.stop_agent(name)
        return {"status": "stopped", "agent": name}

    async def _handle_agents_restart(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: name")

        await self._supervisor.restart_agent(name)
        return {"status": "restarted", "agent": name}

    async def _handle_agents_logs(self, params: dict) -> dict:
        """Fetch recent journal entries for an agent via journalctl."""
        name = params.get("name")
        if not name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: name")

        status = self._supervisor.get_agent_status(name)
        if status is None:
            from yaaos_agentd.errors import AgentNotFoundError
            raise AgentNotFoundError(name)

        lines = params.get("lines", 50)
        unit = f"systemagentd-agent@{name}.service"

        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            log_text = stdout.decode("utf-8", errors="replace")
            return {"agent": name, "unit": unit, "lines": log_text.splitlines()}
        except FileNotFoundError:
            # journalctl not available (e.g., non-systemd systems)
            return {"agent": name, "unit": unit, "lines": [], "error": "journalctl not found"}
        except asyncio.TimeoutError:
            return {"agent": name, "unit": unit, "lines": [], "error": "journalctl timed out"}

    async def _handle_tools_list(self, params: dict) -> dict:
        if self._registry is None:
            return {"tools": []}
        return {"tools": self._registry.list_tools()}

    async def _handle_tools_schema(self, params: dict) -> dict:
        if self._registry is None:
            from yaaos_agentd.errors import ToolNotFoundError
            raise ToolNotFoundError(params.get("tool", "unknown"))

        tool_name = params.get("tool")
        if not tool_name:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameter: tool")

        tool = self._registry.get_tool(tool_name)
        return {
            "tool": tool_name,
            "schemas": {name: s.to_dict() for name, s in tool.schemas.items()},
        }

    # Maximum allowed timeout for tool invocations (seconds)
    _MAX_TOOL_TIMEOUT: float = 120.0

    async def _handle_tools_invoke(self, params: dict) -> dict:
        if self._registry is None:
            from yaaos_agentd.errors import ToolNotFoundError
            raise ToolNotFoundError(params.get("tool", "unknown"))

        tool_name = params.get("tool")
        action = params.get("action")
        tool_params = params.get("params", {})
        raw_timeout = params.get("timeout", 30.0)

        if not tool_name or not action:
            from yaaos_agentd.errors import InvalidParamsError
            raise InvalidParamsError("Missing required parameters: tool, action")

        # Clamp timeout to [1, 120] to prevent resource exhaustion
        try:
            timeout = min(max(float(raw_timeout), 1.0), self._MAX_TOOL_TIMEOUT)
        except (TypeError, ValueError):
            timeout = 30.0

        # Enforce tool permission policy — block requires_root unless opted in
        tool = self._registry.get_tool(tool_name)
        if tool.permissions.get("requires_root", False):
            allow_root = self._supervisor.config.supervisor.allow_root_tools
            if not allow_root:
                from yaaos_agentd.errors import InvalidParamsError
                raise InvalidParamsError(
                    f"Tool '{tool_name}' requires root privileges and is blocked by policy. "
                    f"Set [supervisor] allow_root_tools = true in config to enable."
                )

        result = await self._registry.invoke(
            tool_name, action, tool_params, timeout=timeout
        )
        return result.to_dict()

    async def _handle_config_reload(self, params: dict) -> dict:
        from yaaos_agentd.config import Config
        new_config = Config.load(self._config_path)
        self._supervisor.config = new_config
        await self._supervisor.reconcile()
        return {"status": "reloaded", "agents": list(new_config.agents.keys())}

    # ── Server Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start listening on the Unix socket."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        if self.socket_path.exists():
            self.socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
            limit=1024 * 1024,
        )
        self._started_at = time.monotonic()
        self._log.info("agentbus.started", socket=str(self.socket_path))

    async def stop(self) -> None:
        """Gracefully stop the server — drain in-flight requests."""
        if self._server is None:
            return

        self._shutting_down = True
        self._log.info(
            "agentbus.stopping",
            active_connections=len(self._active_connections),
            in_flight=self._in_flight,
        )

        self._server.close()
        await self._server.wait_closed()

        if self._active_connections:
            self._log.info("agentbus.draining", count=len(self._active_connections))
            done, pending = await asyncio.wait(
                self._active_connections, timeout=self._drain_timeout
            )
            if pending:
                self._log.warning("agentbus.force_closing", count=len(pending))
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending)

        if self.socket_path.exists():
            self.socket_path.unlink()

        self._log.info("agentbus.stopped", total_requests=self._request_count)

    async def wait_closed(self) -> None:
        """Wait until the server is closed."""
        if self._server:
            await self._server.wait_closed()

    # ── Connection Handling ──────────────────────────────────────

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
                    break
                await self._process_line(line, writer)
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        except Exception:
            self._log.exception("agentbus.connection_error")
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
            await self._send_error(
                writer, request_id, InvalidRequestError("Missing method")
            )
            return

        if self._shutting_down:
            await self._send_error(
                writer, request_id, InternalError("Server is shutting down")
            )
            return

        self._request_count += 1
        self._in_flight += 1
        log = self._log.bind(method=method, request_id=request_id, trace_id=trace_id)

        try:
            async with self._semaphore:
                handler = self._handlers.get(method)
                if handler is None:
                    raise MethodNotFoundError(f"Unknown method: {method}")

                result = await handler(params)
                await self._send_result(writer, request_id, result)

            elapsed = time.monotonic() - start
            log.info("request.completed", elapsed_ms=round(elapsed * 1000, 1))

        except AgentdError as e:
            log.warning("request.error", error=e.message, code=e.code)
            await self._send_error(writer, request_id, e)
        except Exception as e:
            log.exception("request.internal_error")
            await self._send_error(writer, request_id, InternalError(str(e)))
        finally:
            self._in_flight -= 1

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
        self, writer: asyncio.StreamWriter, request_id: Any, error: AgentdError
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
            self._log.debug("agentbus.write_failed", msg="client disconnected")
            raise
