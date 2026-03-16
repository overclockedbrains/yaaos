"""TOML manifest parsing for tool definitions.

Each tool is a .toml file in a tools.d/ directory that declares
the tool's binary, capabilities, action schemas, and permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib as tomli
except ImportError:
    import tomli  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """Schema for a single tool action (e.g., docker.ps, git.status)."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema object
    args_template: str = ""                                     # Jinja2 template → CLI args
    output_format: str = "text"                                 # json | text | exitcode

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "args_template": self.args_template,
            "output_format": self.output_format,
        }


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A registered tool with all metadata, loaded from a TOML manifest."""

    name: str
    description: str
    binary: str
    capabilities: list[str] = field(default_factory=list)
    schemas: dict[str, ToolSchema] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    sandbox_config: dict[str, Any] | None = None
    version_cmd: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "binary": self.binary,
            "capabilities": self.capabilities,
            "schemas": {k: v.to_dict() for k, v in self.schemas.items()},
            "permissions": self.permissions,
        }


def load_manifest(path: Path) -> ToolDefinition:
    """Load a tool definition from a TOML manifest file.

    Expected format:
        [tool]
        name = "docker"
        description = "Container runtime"
        binary = "/usr/bin/docker"
        version_cmd = "docker --version"

        [tool.capabilities]
        actions = ["ps", "run", "stop"]

        [tool.schema.ps]
        description = "List running containers"
        args = "ps --format json"
        output_format = "json"

        [tool.schema.run]
        description = "Run a container"
        parameters = { ... }  # JSON Schema
        args_template = "run {%if detach%}-d{%endif%} {{image}}"

        [tool.permissions]
        requires_root = false
        network_access = true

        [tool.sandbox]
        enabled = true
        allowed_paths = ["/var/run/docker.sock"]
    """
    with open(path, "rb") as f:
        raw = tomli.load(f)

    tool_raw = raw.get("tool", {})
    name = tool_raw.get("name", path.stem)
    description = tool_raw.get("description", "")
    binary = tool_raw.get("binary", name)
    version_cmd = tool_raw.get("version_cmd")

    # Parse capabilities
    caps_raw = tool_raw.get("capabilities", {})
    capabilities = caps_raw.get("actions", []) if isinstance(caps_raw, dict) else []

    # Parse action schemas
    schemas: dict[str, ToolSchema] = {}
    schema_raw = tool_raw.get("schema", {})
    for action_name, action_raw in schema_raw.items():
        if not isinstance(action_raw, dict):
            continue

        # Support both "args" (static) and "args_template" (dynamic)
        args_template = action_raw.get("args_template", "")
        if not args_template:
            # Static args as a string
            static_args = action_raw.get("args")
            if static_args:
                if isinstance(static_args, list):
                    args_template = " ".join(static_args)
                else:
                    args_template = str(static_args)

        # Parse parameters (JSON Schema)
        params = action_raw.get("parameters", {})
        if isinstance(params, list):
            # Convert list-of-dicts shorthand to proper JSON Schema
            params = _params_list_to_schema(params)

        schemas[action_name] = ToolSchema(
            name=action_name,
            description=action_raw.get("description", ""),
            parameters=params,
            args_template=args_template,
            output_format=action_raw.get("output_format", "text"),
        )

    # Parse permissions
    permissions = tool_raw.get("permissions", {})

    # Parse sandbox config
    sandbox_raw = tool_raw.get("sandbox", {})
    sandbox_config = sandbox_raw if sandbox_raw.get("enabled") else None

    return ToolDefinition(
        name=name,
        description=description,
        binary=binary,
        capabilities=capabilities,
        schemas=schemas,
        permissions=permissions,
        sandbox_config=sandbox_config,
        version_cmd=version_cmd,
    )


def _params_list_to_schema(params_list: list[dict]) -> dict:
    """Convert a list-of-param-dicts shorthand into a JSON Schema object.

    Input:
        [
            {"name": "image", "type": "string", "required": true, "description": "Container image"},
            {"name": "detach", "type": "boolean", "default": true},
        ]

    Output:
        {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Container image"},
                "detach": {"type": "boolean", "default": true},
            },
            "required": ["image"],
        }
    """
    properties = {}
    required = []

    for param in params_list:
        name = param.get("name", "")
        if not name:
            continue

        prop: dict = {"type": param.get("type", "string")}
        if "description" in param:
            prop["description"] = param["description"]
        if "default" in param:
            prop["default"] = param["default"]
        if "enum" in param:
            prop["enum"] = param["enum"]
        if "items" in param:
            prop["items"] = {"type": param["items"]}

        properties[name] = prop

        if param.get("required", False):
            required.append(name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
