"""Tests for error types."""

from __future__ import annotations

from yaaos_agentd.errors import (
    AGENT_CRASH_LOOP,
    AGENT_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    TOOL_NOT_FOUND,
    AgentAlreadyRunningError,
    AgentCrashLoopError,
    AgentdError,
    AgentNotFoundError,
    ConfigError,
    DaemonNotRunning,
    InvalidRequestError,
    MethodNotFoundError,
    ToolNotFoundError,
    ToolValidationError,
)


class TestAgentdError:
    def test_base_error(self):
        err = AgentdError("something broke")
        assert err.message == "something broke"
        assert err.code == INTERNAL_ERROR
        assert err.data is None

    def test_to_jsonrpc_error(self):
        err = AgentdError("test error", data={"detail": "more info"})
        rpc = err.to_jsonrpc_error()
        assert rpc["code"] == INTERNAL_ERROR
        assert rpc["message"] == "test error"
        assert rpc["data"] == {"detail": "more info"}

    def test_to_jsonrpc_error_no_data(self):
        err = AgentdError("test")
        rpc = err.to_jsonrpc_error()
        assert "data" not in rpc


class TestSpecificErrors:
    def test_invalid_request(self):
        err = InvalidRequestError()
        assert err.code == INVALID_REQUEST
        assert "Invalid request" in err.message

    def test_method_not_found(self):
        err = MethodNotFoundError()
        assert err.code == METHOD_NOT_FOUND

    def test_agent_not_found(self):
        err = AgentNotFoundError("my-agent")
        assert err.code == AGENT_NOT_FOUND
        assert "my-agent" in err.message
        assert err.data == {"agent": "my-agent"}

    def test_agent_already_running(self):
        err = AgentAlreadyRunningError("log")
        assert "log" in err.message

    def test_agent_crash_loop(self):
        err = AgentCrashLoopError("net", restarts=5, window_sec=60.0)
        assert err.code == AGENT_CRASH_LOOP
        assert err.data["restarts"] == 5
        assert "5 restarts" in err.message

    def test_tool_not_found(self):
        err = ToolNotFoundError("nonexistent")
        assert err.code == TOOL_NOT_FOUND
        assert err.data == {"tool": "nonexistent"}

    def test_tool_validation_error(self):
        err = ToolValidationError("bad input")
        assert "bad input" in err.message

    def test_config_error(self):
        err = ConfigError("invalid config")
        assert "invalid config" in err.message


class TestDaemonNotRunning:
    def test_is_separate_exception(self):
        """DaemonNotRunning is not an AgentdError — it's for client-side use."""
        err = DaemonNotRunning("socket not found")
        assert not isinstance(err, AgentdError)
        assert str(err) == "socket not found"
