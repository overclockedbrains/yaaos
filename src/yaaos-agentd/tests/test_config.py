"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from yaaos_agentd.config import Config, SupervisorConfig, _parse_agent_spec
from yaaos_agentd.errors import ConfigError
from yaaos_agentd.types import RestartPolicy


class TestSupervisorConfig:
    def test_defaults(self):
        cfg = SupervisorConfig()
        assert cfg.reconcile_interval_sec == 10.0
        assert cfg.max_restarts == 5
        assert cfg.max_restart_window_sec == 60.0
        assert cfg.log_level == "info"
        assert cfg.max_connections == 8


class TestConfig:
    def test_defaults(self):
        cfg = Config()
        assert cfg.agents == {}
        assert len(cfg.tool_dirs) >= 1

    def test_load_nonexistent_file(self):
        """Loading from a nonexistent path returns defaults."""
        cfg = Config.load(Path("/nonexistent/path.toml"))
        assert cfg.agents == {}

    def test_from_dict_empty(self):
        cfg = Config._from_dict({})
        assert cfg.agents == {}
        assert cfg.supervisor.reconcile_interval_sec == 10.0

    def test_from_dict_with_agents(self):
        raw = {
            "supervisor": {
                "reconcile_interval_sec": 5.0,
                "log_level": "debug",
            },
            "agents": {
                "log": {
                    "enabled": True,
                    "module": "yaaos_agentd.agents.log_agent",
                    "restart_policy": "permanent",
                    "reconcile_interval_sec": 5.0,
                    "config.units": ["sshd", "docker"],
                    "config.anomaly_threshold": 2.0,
                },
                "crash": {
                    "enabled": True,
                    "module": "yaaos_agentd.agents.crash_agent",
                    "restart_policy": "transient",
                },
            },
        }
        cfg = Config._from_dict(raw)
        assert "log" in cfg.agents
        assert "crash" in cfg.agents
        assert cfg.agents["log"].restart_policy == RestartPolicy.PERMANENT
        assert cfg.agents["crash"].restart_policy == RestartPolicy.TRANSIENT
        assert cfg.agents["log"].config["units"] == ["sshd", "docker"]
        assert cfg.agents["log"].config["anomaly_threshold"] == 2.0

    def test_from_dict_nested_config(self):
        raw = {
            "agents": {
                "resource": {
                    "module": "yaaos_agentd.agents.resource_agent",
                    "config": {
                        "cpu_warn_pct": 85,
                        "memory_warn_pct": 80,
                    },
                },
            },
        }
        cfg = Config._from_dict(raw)
        assert cfg.agents["resource"].config["cpu_warn_pct"] == 85

    def test_from_dict_with_resource_limits(self):
        raw = {
            "agents": {
                "log": {
                    "module": "yaaos_agentd.agents.log_agent",
                    "cpu_quota": "10%",
                    "memory_max": "512M",
                },
            },
        }
        cfg = Config._from_dict(raw)
        assert cfg.agents["log"].resource_limits["cpu_quota"] == "10%"
        assert cfg.agents["log"].resource_limits["memory_max"] == "512M"

    def test_from_dict_tool_dirs(self):
        raw = {
            "tool_dirs": {
                "system": "/custom/tools",
                "user": "~/my-tools",
            },
        }
        cfg = Config._from_dict(raw)
        assert any(str(d).endswith("custom/tools") for d in cfg.tool_dirs)

    def test_from_toml_file(self, tmp_path):
        toml_content = b"""
[supervisor]
reconcile_interval_sec = 3.0
log_level = "debug"

[agents.log]
enabled = true
module = "yaaos_agentd.agents.log_agent"
restart_policy = "permanent"
reconcile_interval_sec = 5.0
"""
        config_file = tmp_path / "agentd.toml"
        config_file.write_bytes(toml_content)
        cfg = Config.load(config_file)
        assert cfg.supervisor.reconcile_interval_sec == 3.0
        assert cfg.supervisor.log_level == "debug"
        assert "log" in cfg.agents
        assert cfg.agents["log"].reconcile_interval_sec == 5.0

    def test_env_override_socket(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YAAOS_AGENTBUS_SOCKET", str(tmp_path / "custom.sock"))
        cfg = Config._from_dict({})
        assert cfg.supervisor.socket_path == tmp_path / "custom.sock"

    def test_env_override_log_level(self, monkeypatch):
        monkeypatch.setenv("YAAOS_AGENTD_LOG_LEVEL", "debug")
        cfg = Config._from_dict({})
        assert cfg.supervisor.log_level == "debug"


class TestParseAgentSpec:
    def test_valid_agent(self):
        sup = SupervisorConfig()
        spec = _parse_agent_spec("log", {"module": "test.module"}, sup)
        assert spec.name == "log"
        assert spec.module == "test.module"
        assert spec.restart_policy == RestartPolicy.PERMANENT

    def test_default_module(self):
        sup = SupervisorConfig()
        spec = _parse_agent_spec("log", {}, sup)
        assert spec.module == "yaaos_agentd.agents.log_agent"

    def test_invalid_name(self):
        sup = SupervisorConfig()
        with pytest.raises(ConfigError, match="Invalid agent name"):
            _parse_agent_spec("Bad-Name", {}, sup)

    def test_invalid_restart_policy(self):
        sup = SupervisorConfig()
        with pytest.raises(ConfigError, match="Invalid restart_policy"):
            _parse_agent_spec("test", {"restart_policy": "invalid"}, sup)

    def test_inherits_supervisor_defaults(self):
        sup = SupervisorConfig(max_restarts=10, max_restart_window_sec=120.0)
        spec = _parse_agent_spec("test", {}, sup)
        assert spec.max_restarts == 10
        assert spec.max_restart_window_sec == 120.0

    def test_agent_overrides_supervisor_defaults(self):
        sup = SupervisorConfig(max_restarts=10)
        spec = _parse_agent_spec("test", {"max_restarts": 3}, sup)
        assert spec.max_restarts == 3
