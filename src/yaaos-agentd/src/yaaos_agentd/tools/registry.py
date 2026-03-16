"""Tool Registry — discovers, validates, and invokes CLI tools for agents.

Tools are defined as TOML manifests in tools.d/ directories. The registry
loads manifests, validates tool inputs against JSON Schema, renders CLI
arguments from Jinja2 templates, and executes tools with optional sandboxing.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

import jsonschema
import orjson
import structlog
from jinja2 import BaseLoader
from jinja2.sandbox import SandboxedEnvironment

from yaaos_agentd.errors import ToolInvocationError, ToolNotFoundError, ToolValidationError
from yaaos_agentd.tools.manifest import ToolDefinition, ToolSchema, load_manifest
from yaaos_agentd.tools.sandbox import sandbox_from_config
from yaaos_agentd.types import ToolResult

logger = structlog.get_logger()

# Jinja2 environment for args_template rendering — no filesystem access
_jinja_env = SandboxedEnvironment(loader=BaseLoader(), autoescape=False)


class ToolRegistry:
    """Discovers, validates, and invokes CLI tools for agents.

    Usage:
        registry = ToolRegistry([Path("/etc/yaaos/tools.d"), Path("~/.config/yaaos/tools.d")])
        tools = registry.find_tools(capability="container.list")
        result = await registry.invoke("docker", "ps", {})
    """

    def __init__(self, tool_dirs: list[Path] | None = None):
        self._tools: dict[str, ToolDefinition] = {}
        self._log = logger.bind(component="tool_registry")
        if tool_dirs:
            self.load_tools(tool_dirs)

    @property
    def tools(self) -> dict[str, ToolDefinition]:
        return dict(self._tools)

    def load_tools(self, dirs: list[Path]) -> None:
        """Scan tool directories for .toml manifests, validate, and register."""
        for d in dirs:
            d = d.expanduser()
            if not d.is_dir():
                self._log.debug("tool_registry.dir_not_found", path=str(d))
                continue

            for toml_file in sorted(d.glob("*.toml")):
                try:
                    tool = load_manifest(toml_file)
                    if self._validate_tool(tool):
                        self._tools[tool.name] = tool
                        self._log.info(
                            "tool_registry.loaded",
                            tool=tool.name,
                            actions=list(tool.schemas.keys()),
                        )
                except Exception as e:
                    self._log.warning(
                        "tool_registry.load_failed",
                        file=str(toml_file),
                        error=str(e),
                    )

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a tool definition directly (for testing or programmatic use)."""
        self._tools[tool.name] = tool

    def _validate_tool(self, tool: ToolDefinition) -> bool:
        """Validate a tool's binary exists and is executable."""
        binary = shutil.which(tool.binary)
        if binary is None:
            self._log.debug(
                "tool_registry.binary_not_found",
                tool=tool.name,
                binary=tool.binary,
            )
            return False
        return True

    # ── Discovery ───────────────────────────────────────────────

    def find_tools(
        self,
        *,
        capability: str | None = None,
        name: str | None = None,
    ) -> list[ToolDefinition]:
        """Find tools by capability or name pattern."""
        results = list(self._tools.values())

        if name is not None:
            results = [t for t in results if name in t.name]

        if capability is not None:
            results = [
                t for t in results
                if capability in t.capabilities
            ]

        return results

    def get_tool(self, name: str) -> ToolDefinition:
        """Get a tool by exact name, or raise ToolNotFoundError."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        return tool

    def list_tools(self) -> list[dict]:
        """List all tools with summary info."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "actions": list(t.schemas.keys()),
                "binary": t.binary,
                "capabilities": t.capabilities,
            }
            for t in self._tools.values()
        ]

    # ── Invocation ──────────────────────────────────────────────

    async def invoke(
        self,
        tool_name: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        sandbox: bool = True,
    ) -> ToolResult:
        """Invoke a tool action with validated parameters.

        1. Validate params against JSON Schema
        2. Render args_template with params
        3. Execute binary with args (optionally in sandbox)
        4. Parse output according to output_format
        5. Return structured result
        """
        params = params or {}
        tool = self.get_tool(tool_name)
        schema = tool.schemas.get(action)
        if schema is None:
            raise ToolNotFoundError(
                f"{tool_name}.{action} (available: {list(tool.schemas.keys())})"
            )

        # Validate params against JSON Schema
        self._validate_params(params, schema)

        # Build command
        args = self._render_args(schema.args_template, params)
        cmd = [tool.binary] + args

        # Optionally wrap in bubblewrap sandbox
        if sandbox and tool.sandbox_config:
            policy = sandbox_from_config(tool.sandbox_config)
            bwrap_args = policy.to_bwrap_args()
            if bwrap_args:
                cmd = bwrap_args + cmd

        # Execute
        log = self._log.bind(tool=tool_name, action=action)
        log.debug("tool_registry.invoking", cmd=cmd, sandboxed=bool(sandbox and tool.sandbox_config))

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000
            exit_code = proc.returncode or 0
            is_error = exit_code != 0

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")

            # Post-process output based on output_format
            stdout_str = self._postprocess_output(stdout_str, schema.output_format, log)

            result = ToolResult(
                exit_code=exit_code,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_ms=duration_ms,
                is_error=is_error,
            )

            log.info(
                "tool_registry.completed",
                exit_code=exit_code,
                duration_ms=round(duration_ms, 1),
            )
            return result

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            log.warning("tool_registry.timeout", timeout=timeout)
            try:
                proc.kill()  # type: ignore
                await proc.wait()  # Reap zombie process
            except ProcessLookupError:
                pass
            return ToolResult(
                exit_code=-1,
                stdout="",
                stderr=f"Timed out after {timeout}s",
                duration_ms=duration_ms,
                is_error=True,
            )

        except FileNotFoundError:
            raise ToolInvocationError(
                f"Binary not found: {tool.binary}",
                data={"tool": tool_name, "binary": tool.binary},
            )

        except Exception as e:
            raise ToolInvocationError(
                f"Failed to invoke {tool_name}.{action}: {e}",
                data={"tool": tool_name, "action": action, "error": str(e)},
            )

    def _validate_params(self, params: dict, schema: ToolSchema) -> None:
        """Validate params against the tool action's JSON Schema.

        Coerces string values to declared types before validation,
        since CLI and JSON-RPC callers often pass all values as strings.
        """
        if not schema.parameters:
            return

        self._coerce_param_types(params, schema.parameters)

        try:
            jsonschema.validate(instance=params, schema=schema.parameters)
        except jsonschema.ValidationError as e:
            raise ToolValidationError(
                f"Invalid params for {schema.name}: {e.message}",
                data={"schema": schema.name, "error": e.message},
            )

    @staticmethod
    def _coerce_param_types(params: dict, schema: dict) -> None:
        """Coerce string param values to match their declared JSON Schema types.

        Handles integer, number, and boolean types. Leaves values unchanged
        if coercion fails (jsonschema will then report the type error).
        """
        properties = schema.get("properties", {})
        for key, value in params.items():
            if not isinstance(value, str) or key not in properties:
                continue
            declared_type = properties[key].get("type")
            try:
                if declared_type == "integer":
                    params[key] = int(value)
                elif declared_type == "number":
                    params[key] = float(value)
                elif declared_type == "boolean":
                    params[key] = value.lower() in ("true", "1", "yes")
            except (ValueError, AttributeError):
                pass  # Leave as string — jsonschema will report the error

    def _render_args(self, template: str, params: dict) -> list[str]:
        """Render a Jinja2 args_template into a list of CLI arguments."""
        if not template:
            return []

        try:
            rendered = _jinja_env.from_string(template).render(**params)
            # Shell-aware split to correctly handle quoted args (e.g. "my file.txt")
            return shlex.split(rendered)
        except Exception as e:
            raise ToolInvocationError(
                f"Failed to render args template: {e}",
                data={"template": template, "error": str(e)},
            )

    @staticmethod
    def _postprocess_output(stdout: str, output_format: str, log: Any) -> str:
        """Post-process tool stdout based on the schema's output_format.

        - "json":     Validate stdout is valid JSON; return compact form.
        - "exitcode": Output is irrelevant; return empty string.
        - "text":     Return as-is (default).
        """
        if output_format == "exitcode":
            return ""

        if output_format == "json":
            stripped = stdout.strip()
            if not stripped:
                return "{}"
            try:
                parsed = orjson.loads(stripped)
                return orjson.dumps(parsed).decode()
            except (orjson.JSONDecodeError, ValueError):
                log.warning("tool_registry.json_parse_failed", raw_length=len(stripped))
                return stdout

        # "text" or any unknown format — return as-is
        return stdout
