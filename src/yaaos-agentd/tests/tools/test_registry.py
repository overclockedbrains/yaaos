"""Tests for Tool Registry — discovery, validation, invocation."""

from __future__ import annotations


import pytest

from yaaos_agentd.errors import ToolInvocationError, ToolNotFoundError, ToolValidationError
from yaaos_agentd.tools.manifest import ToolDefinition, ToolSchema
from yaaos_agentd.tools.registry import ToolRegistry


def _echo_tool() -> ToolDefinition:
    """Create a tool definition that invokes 'echo' (available everywhere)."""
    return ToolDefinition(
        name="echo",
        description="Echo text to stdout",
        binary="echo",
        capabilities=["text"],
        schemas={
            "say": ToolSchema(
                name="say",
                description="Echo a message",
                args_template="{{ message }}",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
                output_format="text",
            ),
            "hello": ToolSchema(
                name="hello",
                description="Say hello",
                args_template="hello",
                output_format="text",
            ),
        },
    )


def _false_tool() -> ToolDefinition:
    """Create a tool that always exits with code 1."""
    return ToolDefinition(
        name="false",
        description="Always fails",
        binary="false",
        schemas={
            "run": ToolSchema(
                name="run",
                description="Run and fail",
                output_format="exitcode",
            ),
        },
    )


class TestToolRegistryDiscovery:
    def test_register_and_find(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        assert len(registry.tools) == 1

    def test_find_by_name(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        results = registry.find_tools(name="echo")
        assert len(results) == 1
        assert results[0].name == "echo"

    def test_find_by_capability(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        results = registry.find_tools(capability="text")
        assert len(results) == 1

    def test_find_no_match(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        results = registry.find_tools(capability="container")
        assert len(results) == 0

    def test_get_tool(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        tool = registry.get_tool("echo")
        assert tool.name == "echo"

    def test_get_tool_not_found(self):
        registry = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            registry.get_tool("nonexistent")

    def test_list_tools(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        registry.register_tool(_false_tool())
        listing = registry.list_tools()
        assert len(listing) == 2
        names = [t["name"] for t in listing]
        assert "echo" in names
        assert "false" in names

    def test_load_from_directory(self, tmp_path):
        toml = b"""
[tool]
name = "echo"
description = "Echo tool"
binary = "echo"

[tool.schema.hello]
description = "Say hello"
args_template = "hello"
"""
        (tmp_path / "echo.toml").write_bytes(toml)
        registry = ToolRegistry([tmp_path])
        assert "echo" in registry.tools


class TestToolRegistryInvocation:
    @pytest.mark.asyncio
    async def test_invoke_echo(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        result = await registry.invoke("echo", "say", {"message": "test-output"})
        assert result.exit_code == 0
        assert result.is_error is False
        assert "test-output" in result.stdout

    @pytest.mark.asyncio
    async def test_invoke_static_args(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        result = await registry.invoke("echo", "hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_invoke_nonexistent_tool(self):
        registry = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            await registry.invoke("nonexistent", "action")

    @pytest.mark.asyncio
    async def test_invoke_nonexistent_action(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        with pytest.raises(ToolNotFoundError, match="echo.nonexistent"):
            await registry.invoke("echo", "nonexistent")

    @pytest.mark.asyncio
    async def test_invoke_validation_error(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        with pytest.raises(ToolValidationError):
            await registry.invoke("echo", "say", {"wrong_param": "value"})

    @pytest.mark.asyncio
    async def test_invoke_nonzero_exit(self):
        registry = ToolRegistry()
        registry.register_tool(_false_tool())
        result = await registry.invoke("false", "run")
        assert result.exit_code == 1
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_invoke_timeout(self):
        """Tool that exceeds timeout gets killed."""
        sleep_tool = ToolDefinition(
            name="sleep",
            description="Sleep tool",
            binary="sleep",
            schemas={
                "wait": ToolSchema(
                    name="wait",
                    description="Sleep for seconds",
                    args_template="10",
                    output_format="text",
                ),
            },
        )
        registry = ToolRegistry()
        registry.register_tool(sleep_tool)
        result = await registry.invoke("sleep", "wait", timeout=0.5)
        assert result.is_error is True
        assert "Timed out" in result.stderr

    @pytest.mark.asyncio
    async def test_invoke_empty_params(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        result = await registry.invoke("echo", "hello", {})
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_invoke_binary_not_found(self):
        bad_tool = ToolDefinition(
            name="nonexistent-binary",
            description="Tool with missing binary",
            binary="/nonexistent/binary/path",
            schemas={
                "run": ToolSchema(name="run", args_template="arg"),
            },
        )
        registry = ToolRegistry()
        registry.register_tool(bad_tool)
        with pytest.raises(ToolInvocationError, match="Binary not found"):
            await registry.invoke("nonexistent-binary", "run")


class TestToolRegistryValidation:
    def test_validate_params_success(self):
        registry = ToolRegistry()
        registry.register_tool(_echo_tool())
        schema = _echo_tool().schemas["say"]
        # Should not raise
        registry._validate_params({"message": "hello"}, schema)

    def test_validate_params_missing_required(self):
        registry = ToolRegistry()
        schema = _echo_tool().schemas["say"]
        with pytest.raises(ToolValidationError):
            registry._validate_params({}, schema)

    def test_validate_params_wrong_type(self):
        registry = ToolRegistry()
        schema = _echo_tool().schemas["say"]
        with pytest.raises(ToolValidationError):
            registry._validate_params({"message": 123}, schema)

    def test_validate_empty_schema(self):
        registry = ToolRegistry()
        schema = ToolSchema(name="noop")
        # No schema = no validation = should pass
        registry._validate_params({"anything": "goes"}, schema)


class TestArgsTemplateRendering:
    def test_simple_render(self):
        registry = ToolRegistry()
        args = registry._render_args("status --porcelain", {})
        assert args == ["status", "--porcelain"]

    def test_render_with_variable(self):
        registry = ToolRegistry()
        args = registry._render_args("log -n {{ count }}", {"count": 5})
        assert args == ["log", "-n", "5"]

    def test_render_with_conditional(self):
        registry = ToolRegistry()
        template = "ps {% if all %}--all{% endif %} --format json"
        args = registry._render_args(template, {"all": True})
        assert "--all" in args
        args = registry._render_args(template, {"all": False})
        assert "--all" not in args

    def test_render_empty_template(self):
        registry = ToolRegistry()
        args = registry._render_args("", {})
        assert args == []

    def test_render_with_default(self):
        registry = ToolRegistry()
        args = registry._render_args("log -n {{ count | default(10) }}", {})
        assert args == ["log", "-n", "10"]
