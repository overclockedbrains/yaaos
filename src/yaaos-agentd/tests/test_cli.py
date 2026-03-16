"""Tests for systemagentctl CLI."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from yaaos_agentd.cli import main, _format_duration, _state_color, _state_icon


# ── Helper Formatting Tests ──────────────────────────────────


class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        assert _format_duration(125) == "2m 5s"

    def test_hours(self):
        assert _format_duration(7380) == "2h 3m"

    def test_zero(self):
        assert _format_duration(0) == "0s"


class TestStateColor:
    def test_running(self):
        assert _state_color("running") == "green"

    def test_failed(self):
        assert _state_color("failed") == "red"

    def test_unknown(self):
        assert _state_color("unknown_state") == "white"


class TestStateIcon:
    def test_running(self):
        assert "●" in _state_icon("running")

    def test_failed(self):
        assert "✗" in _state_icon("failed")

    def test_unknown(self):
        assert _state_icon("something_else") == "?"


# ── CLI Command Tests (mocked client) ───────────────────────


class TestStatusCommand:
    def test_status_daemon_not_running(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            from yaaos_agentd.errors import DaemonNotRunning
            MockClient.return_value.health.side_effect = DaemonNotRunning("not running")
            result = runner.invoke(main, ["status"])
            assert result.exit_code == 1
            assert "not running" in result.output

    def test_status_healthy(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.health.return_value = {
                "status": "healthy",
                "uptime_sec": 3661,
                "agent_count": 2,
                "agents_running": 2,
                "agents_failed": 0,
                "agents_degraded": 0,
            }
            mock_client.list_agents.return_value = [
                {
                    "name": "log",
                    "state": "running",
                    "cycle_count": 100,
                    "error_count": 0,
                    "last_cycle_ago_sec": 5.0,
                },
                {
                    "name": "resource",
                    "state": "running",
                    "cycle_count": 50,
                    "error_count": 0,
                },
            ]
            result = runner.invoke(main, ["status"])
            assert result.exit_code == 0
            assert "healthy" in result.output
            assert "2/2 running" in result.output
            assert "log" in result.output
            assert "resource" in result.output

    def test_status_with_failed_agents(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.health.return_value = {
                "status": "degraded",
                "uptime_sec": 100,
                "agent_count": 2,
                "agents_running": 1,
                "agents_failed": 1,
                "agents_degraded": 0,
            }
            mock_client.list_agents.return_value = [
                {"name": "log", "state": "running", "cycle_count": 10, "error_count": 0},
                {"name": "crash", "state": "failed", "cycle_count": 0, "error_count": 3},
            ]
            result = runner.invoke(main, ["status"])
            assert result.exit_code == 0
            assert "Failed: 1" in result.output


class TestAgentCommand:
    def test_agent_detail(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.agent_status.return_value = {
                "name": "log",
                "state": "running",
                "pid": 1234,
                "uptime_sec": 600,
                "cycle_count": 100,
                "error_count": 0,
                "last_cycle_ago_sec": 2.0,
                "memory_mb": 48.5,
                "cpu_percent": 1.2,
            }
            result = runner.invoke(main, ["agent", "log"])
            assert result.exit_code == 0
            assert "log" in result.output
            assert "1234" in result.output
            assert "48.5" in result.output

    def test_agent_not_found(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            from yaaos_agentd.errors import AgentdError
            MockClient.return_value.agent_status.side_effect = AgentdError("Agent not found: x")
            result = runner.invoke(main, ["agent", "x"])
            assert result.exit_code == 1
            assert "Error" in result.output


class TestControlCommands:
    def test_start_agent(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.start_agent.return_value = {"status": "started"}
            result = runner.invoke(main, ["start", "log"])
            assert result.exit_code == 0
            assert "Started" in result.output

    def test_stop_agent(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.stop_agent.return_value = {"status": "stopped"}
            result = runner.invoke(main, ["stop", "log"])
            assert result.exit_code == 0
            assert "Stopped" in result.output

    def test_restart_agent(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.restart_agent.return_value = {"status": "restarted"}
            result = runner.invoke(main, ["restart", "log"])
            assert result.exit_code == 0
            assert "Restarted" in result.output


class TestToolsCommands:
    def test_tools_list(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.list_tools.return_value = [
                {
                    "name": "docker",
                    "description": "Container runtime",
                    "actions": ["ps", "run"],
                    "binary": "/usr/bin/docker",
                },
                {
                    "name": "git",
                    "description": "Version control",
                    "actions": ["status", "log"],
                    "binary": "/usr/bin/git",
                },
            ]
            result = runner.invoke(main, ["tools", "list"])
            assert result.exit_code == 0
            assert "docker" in result.output
            assert "git" in result.output

    def test_tools_list_empty(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.list_tools.return_value = []
            result = runner.invoke(main, ["tools", "list"])
            assert result.exit_code == 0
            assert "No tools" in result.output

    def test_tools_schema(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.tool_schema.return_value = {
                "tool": "echo",
                "schemas": {
                    "say": {
                        "name": "say",
                        "description": "Echo a message",
                        "parameters": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                        },
                    },
                },
            }
            result = runner.invoke(main, ["tools", "schema", "echo"])
            assert result.exit_code == 0
            assert "say" in result.output
            assert "Echo a message" in result.output

    def test_tools_invoke_success(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.invoke_tool.return_value = {
                "exit_code": 0,
                "stdout": "hello world\n",
                "stderr": "",
                "duration_ms": 5.2,
                "is_error": False,
            }
            result = runner.invoke(
                main, ["tools", "invoke", "echo", "say", "-p", "message=hello world"]
            )
            assert result.exit_code == 0
            assert "hello world" in result.output

    def test_tools_invoke_error(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.invoke_tool.return_value = {
                "exit_code": 1,
                "stdout": "",
                "stderr": "command not found",
                "duration_ms": 2.0,
                "is_error": True,
            }
            result = runner.invoke(main, ["tools", "invoke", "bad", "run"])
            assert result.exit_code == 0  # CLI itself doesn't fail
            assert "Exit code 1" in result.output

    def test_tools_invoke_bad_param_format(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient"):
            result = runner.invoke(
                main, ["tools", "invoke", "echo", "say", "-p", "badparam"]
            )
            assert result.exit_code == 1
            assert "Invalid param format" in result.output


class TestReloadCommand:
    def test_reload_success(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            MockClient.return_value.reload_config.return_value = {
                "status": "reloaded",
                "agents": ["log", "resource"],
            }
            result = runner.invoke(main, ["reload"])
            assert result.exit_code == 0
            assert "reloaded" in result.output
            assert "log" in result.output

    def test_reload_daemon_not_running(self):
        runner = CliRunner()
        with patch("yaaos_agentd.cli.AgentBusClient") as MockClient:
            from yaaos_agentd.errors import DaemonNotRunning
            MockClient.return_value.reload_config.side_effect = DaemonNotRunning("not running")
            result = runner.invoke(main, ["reload"])
            assert result.exit_code == 1
            assert "not running" in result.output
