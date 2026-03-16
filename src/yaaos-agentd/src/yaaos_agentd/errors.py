"""Custom exceptions with JSON-RPC 2.0 error codes for SystemAgentd."""

from __future__ import annotations

# Standard JSON-RPC 2.0 error codes
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# SystemAgentd custom error codes (-32000 to -32099)
AGENT_NOT_FOUND = -32000
AGENT_ALREADY_RUNNING = -32001
AGENT_CRASH_LOOP = -32002
TOOL_NOT_FOUND = -32003
TOOL_INVOCATION_FAILED = -32004
TOOL_VALIDATION_FAILED = -32005
SUPERVISOR_ERROR = -32006
CONFIG_ERROR = -32007


class AgentdError(Exception):
    """Base exception for all SystemAgentd errors."""

    code: int = INTERNAL_ERROR
    message: str = "Internal error"

    def __init__(self, message: str | None = None, data: dict | None = None):
        self.message = message or self.__class__.message
        self.data = data
        super().__init__(self.message)

    def to_jsonrpc_error(self) -> dict:
        """Convert to JSON-RPC 2.0 error object."""
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class InvalidRequestError(AgentdError):
    code = INVALID_REQUEST
    message = "Invalid request"


class MethodNotFoundError(AgentdError):
    code = METHOD_NOT_FOUND
    message = "Method not found"


class InvalidParamsError(AgentdError):
    code = INVALID_PARAMS
    message = "Invalid params"


class InternalError(AgentdError):
    code = INTERNAL_ERROR
    message = "Internal error"


class AgentNotFoundError(AgentdError):
    code = AGENT_NOT_FOUND
    message = "Agent not found"

    def __init__(self, agent_name: str):
        super().__init__(
            f"Agent not found: {agent_name}",
            data={"agent": agent_name},
        )


class AgentAlreadyRunningError(AgentdError):
    code = AGENT_ALREADY_RUNNING
    message = "Agent already running"

    def __init__(self, agent_name: str):
        super().__init__(
            f"Agent already running: {agent_name}",
            data={"agent": agent_name},
        )


class AgentCrashLoopError(AgentdError):
    code = AGENT_CRASH_LOOP
    message = "Agent in crash loop"

    def __init__(self, agent_name: str, restarts: int, window_sec: float):
        super().__init__(
            f"Agent {agent_name} exceeded restart limit ({restarts} restarts in {window_sec}s)",
            data={"agent": agent_name, "restarts": restarts, "window_sec": window_sec},
        )


class ToolNotFoundError(AgentdError):
    code = TOOL_NOT_FOUND
    message = "Tool not found"

    def __init__(self, tool_name: str):
        super().__init__(
            f"Tool not found: {tool_name}",
            data={"tool": tool_name},
        )


class ToolInvocationError(AgentdError):
    code = TOOL_INVOCATION_FAILED
    message = "Tool invocation failed"


class ToolValidationError(AgentdError):
    code = TOOL_VALIDATION_FAILED
    message = "Tool input validation failed"


class SupervisorError(AgentdError):
    code = SUPERVISOR_ERROR
    message = "Supervisor error"


class ConfigError(AgentdError):
    code = CONFIG_ERROR
    message = "Configuration error"


class DaemonNotRunning(Exception):
    """SystemAgentd daemon is not running or socket is unreachable."""
