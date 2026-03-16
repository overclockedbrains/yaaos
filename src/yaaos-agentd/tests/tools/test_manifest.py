"""Tests for TOML manifest parsing."""

from __future__ import annotations



from yaaos_agentd.tools.manifest import (
    ToolDefinition,
    ToolSchema,
    _params_list_to_schema,
    load_manifest,
)


class TestLoadManifest:
    def test_load_git_manifest(self, tmp_path):
        toml = b"""
[tool]
name = "git"
description = "Version control"
binary = "/usr/bin/git"
version_cmd = "git --version"

[tool.capabilities]
actions = ["status", "log"]

[tool.schema.status]
description = "Show working tree status"
args_template = "status --porcelain=v2"
output_format = "text"

[tool.schema.log]
description = "Show commit log"
args_template = "log --oneline -n {{ count }}"
output_format = "text"

[tool.permissions]
requires_root = false
"""
        f = tmp_path / "git.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.name == "git"
        assert tool.description == "Version control"
        assert tool.binary == "/usr/bin/git"
        assert tool.version_cmd == "git --version"
        assert "status" in tool.capabilities
        assert "status" in tool.schemas
        assert "log" in tool.schemas
        assert tool.schemas["status"].output_format == "text"
        assert "{{ count }}" in tool.schemas["log"].args_template

    def test_load_with_static_args(self, tmp_path):
        toml = b"""
[tool]
name = "echo"
binary = "echo"

[tool.schema.hello]
description = "Say hello"
args = "hello world"
output_format = "text"
"""
        f = tmp_path / "echo.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.schemas["hello"].args_template == "hello world"

    def test_load_with_args_list(self, tmp_path):
        toml = b"""
[tool]
name = "test"
binary = "test"

[tool.schema.run]
description = "Run test"
args = ["--verbose", "--output", "json"]
"""
        f = tmp_path / "test.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.schemas["run"].args_template == "--verbose --output json"

    def test_load_with_parameters(self, tmp_path):
        toml = b"""
[tool]
name = "docker"
binary = "docker"

[tool.schema.ps]
description = "List containers"
args_template = "ps --format json"

[tool.schema.ps.parameters]
type = "object"

[tool.schema.ps.parameters.properties.all]
type = "boolean"
description = "Show all containers"
"""
        f = tmp_path / "docker.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.schemas["ps"].parameters["type"] == "object"

    def test_load_with_list_parameters(self, tmp_path):
        toml = b"""
[tool]
name = "docker"
binary = "docker"

[[tool.schema.run.parameters]]
name = "image"
type = "string"
required = true
description = "Container image"

[[tool.schema.run.parameters]]
name = "detach"
type = "boolean"
default = true
"""
        f = tmp_path / "docker.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        schema = tool.schemas["run"]
        assert schema.parameters["type"] == "object"
        assert "image" in schema.parameters["properties"]
        assert "image" in schema.parameters["required"]

    def test_load_with_sandbox(self, tmp_path):
        toml = b"""
[tool]
name = "untrusted"
binary = "untrusted"

[tool.sandbox]
enabled = true
allowed_paths = ["/tmp", "/var/data"]
"""
        f = tmp_path / "untrusted.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.sandbox_config is not None
        assert "/tmp" in tool.sandbox_config["allowed_paths"]

    def test_load_no_sandbox(self, tmp_path):
        toml = b"""
[tool]
name = "trusted"
binary = "trusted"
"""
        f = tmp_path / "trusted.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.sandbox_config is None

    def test_name_defaults_to_filename(self, tmp_path):
        toml = b"""
[tool]
binary = "mytool"
"""
        f = tmp_path / "custom-tool.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.name == "custom-tool"

    def test_binary_defaults_to_name(self, tmp_path):
        toml = b"""
[tool]
name = "mytool"
"""
        f = tmp_path / "mytool.toml"
        f.write_bytes(toml)
        tool = load_manifest(f)

        assert tool.binary == "mytool"


class TestParamsListToSchema:
    def test_basic_conversion(self):
        params = [
            {"name": "image", "type": "string", "required": True, "description": "Image name"},
            {"name": "detach", "type": "boolean", "default": True},
        ]
        schema = _params_list_to_schema(params)

        assert schema["type"] == "object"
        assert "image" in schema["properties"]
        assert schema["properties"]["image"]["type"] == "string"
        assert schema["properties"]["image"]["description"] == "Image name"
        assert schema["properties"]["detach"]["default"] is True
        assert schema["required"] == ["image"]

    def test_empty_list(self):
        schema = _params_list_to_schema([])
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_with_enum(self):
        params = [{"name": "format", "type": "string", "enum": ["json", "text"]}]
        schema = _params_list_to_schema(params)
        assert schema["properties"]["format"]["enum"] == ["json", "text"]


class TestToolSchema:
    def test_to_dict(self):
        schema = ToolSchema(
            name="ps",
            description="List processes",
            args_template="ps --format json",
            output_format="json",
        )
        d = schema.to_dict()
        assert d["name"] == "ps"
        assert d["output_format"] == "json"


class TestToolDefinition:
    def test_to_dict(self):
        tool = ToolDefinition(
            name="docker",
            description="Container runtime",
            binary="/usr/bin/docker",
            capabilities=["ps", "run"],
            schemas={"ps": ToolSchema(name="ps")},
        )
        d = tool.to_dict()
        assert d["name"] == "docker"
        assert "ps" in d["schemas"]
