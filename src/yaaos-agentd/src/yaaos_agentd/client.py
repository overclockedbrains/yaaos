"""Agent Bus client — sync + async Python SDK.

Used by systemagentctl CLI and other YAAOS components to talk to SystemAgentd.
Falls back gracefully when the daemon is not running.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import orjson

from yaaos_agentd.errors import AgentdError, DaemonNotRunning

_DEFAULT_SOCKET = Path("/run/yaaos/agentbus.sock")
_FALLBACK_SOCKET = Path("~/.local/run/yaaos/agentbus.sock")

_REQUEST_TIMEOUT = 30.0
_HEALTH_TIMEOUT = 10.0


def _find_socket() -> Path:
    """Find the Agent Bus socket, checking default then fallback."""
    if _DEFAULT_SOCKET.exists():
        return _DEFAULT_SOCKET
    fallback = _FALLBACK_SOCKET.expanduser()
    if fallback.exists():
        return fallback
    return _DEFAULT_SOCKET


class AsyncAgentBusClient:
    """Async client for the Agent Bus daemon."""

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
                f"SystemAgentd not running (socket: {self.socket_path}): {e}"
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
                    exc = AgentdError(
                        err.get("message", "Unknown error"), err.get("data")
                    )
                    exc.code = err.get("code", exc.code)
                    raise exc

                return response.get("result", {})
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

    async def list_agents(self) -> list[dict]:
        """List all agents with status."""
        result = await self._request("agents.list")
        return result.get("agents", [])

    async def agent_status(self, name: str) -> dict:
        """Get detailed status for a specific agent."""
        return await self._request("agents.status", {"name": name})

    async def start_agent(self, name: str) -> dict:
        """Start a stopped/failed agent."""
        return await self._request("agents.start", {"name": name})

    async def stop_agent(self, name: str) -> dict:
        """Gracefully stop an agent."""
        return await self._request("agents.stop", {"name": name})

    async def restart_agent(self, name: str) -> dict:
        """Restart an agent."""
        return await self._request("agents.restart", {"name": name})

    async def list_tools(self) -> list[dict]:
        """List all registered tools."""
        result = await self._request("tools.list")
        return result.get("tools", [])

    async def tool_schema(self, tool_name: str) -> dict:
        """Get JSON Schema for a tool's actions."""
        return await self._request("tools.schema", {"tool": tool_name})

    async def invoke_tool(
        self,
        tool_name: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict:
        """Invoke a tool action."""
        return await self._request(
            "tools.invoke",
            {
                "tool": tool_name,
                "action": action,
                "params": params or {},
                "timeout": timeout,
            },
        )

    async def agent_logs(self, name: str, lines: int = 50) -> dict:
        """Get recent journal log entries for an agent."""
        return await self._request("agents.logs", {"name": name, "lines": lines})

    async def reload_config(self) -> dict:
        """Trigger config reload."""
        return await self._request("config.reload")


class AgentBusClient:
    """Synchronous wrapper around AsyncAgentBusClient.

    Convenience class for non-async code (CLI, scripts).
    """

    def __init__(self, socket_path: Path | str | None = None):
        self._async = AsyncAgentBusClient(socket_path)

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    def ping(self) -> bool:
        return self._run(self._async.ping())

    def health(self) -> dict:
        return self._run(self._async.health())

    def list_agents(self) -> list[dict]:
        return self._run(self._async.list_agents())

    def agent_status(self, name: str) -> dict:
        return self._run(self._async.agent_status(name))

    def start_agent(self, name: str) -> dict:
        return self._run(self._async.start_agent(name))

    def stop_agent(self, name: str) -> dict:
        return self._run(self._async.stop_agent(name))

    def restart_agent(self, name: str) -> dict:
        return self._run(self._async.restart_agent(name))

    def list_tools(self) -> list[dict]:
        return self._run(self._async.list_tools())

    def tool_schema(self, tool_name: str) -> dict:
        return self._run(self._async.tool_schema(tool_name))

    def invoke_tool(
        self,
        tool_name: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict:
        return self._run(self._async.invoke_tool(tool_name, action, params, timeout=timeout))

    def agent_logs(self, name: str, lines: int = 50) -> dict:
        return self._run(self._async.agent_logs(name, lines))

    def reload_config(self) -> dict:
        return self._run(self._async.reload_config())
