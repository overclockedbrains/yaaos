"""Tests for the yaaos-bus CLI commands."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from yaaos_modelbus.cli import _apply_config_value, _config_to_flat, _write_config_toml, main
from yaaos_modelbus.config import Config, ProviderConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sample_config():
    return Config(
        default_embedding="ollama/nomic-embed-text",
        default_generation="ollama/phi3:mini",
        default_chat="ollama/phi3:mini",
        providers={
            "ollama": ProviderConfig(name="ollama", enabled=True, base_url="http://localhost:11434")
        },
    )


class TestConfigToFlat:
    def test_includes_defaults(self, sample_config):
        flat = _config_to_flat(sample_config)
        assert flat["defaults.embedding"] == "ollama/nomic-embed-text"
        assert flat["defaults.generation"] == "ollama/phi3:mini"
        assert flat["defaults.chat"] == "ollama/phi3:mini"

    def test_includes_daemon_settings(self, sample_config):
        flat = _config_to_flat(sample_config)
        assert "daemon.socket_path" in flat
        assert "daemon.log_level" in flat
        assert "daemon.max_concurrent_requests" in flat

    def test_includes_provider_entries(self, sample_config):
        flat = _config_to_flat(sample_config)
        assert flat["providers.ollama.enabled"] == "true"
        assert flat["providers.ollama.base_url"] == "http://localhost:11434"

    def test_includes_resource_settings(self, sample_config):
        flat = _config_to_flat(sample_config)
        assert "resources.max_vram_usage_pct" in flat
        assert "resources.model_idle_timeout_sec" in flat


class TestApplyConfigValue:
    def test_set_default_embedding(self, sample_config):
        _apply_config_value(sample_config, "defaults.embedding", "openai/text-embedding-3-small")
        assert sample_config.default_embedding == "openai/text-embedding-3-small"

    def test_set_default_generation(self, sample_config):
        _apply_config_value(sample_config, "defaults.generation", "openai/gpt-4o")
        assert sample_config.default_generation == "openai/gpt-4o"

    def test_set_default_chat(self, sample_config):
        _apply_config_value(sample_config, "defaults.chat", "anthropic/claude-sonnet-4-20250514")
        assert sample_config.default_chat == "anthropic/claude-sonnet-4-20250514"

    def test_set_log_level(self, sample_config):
        _apply_config_value(sample_config, "daemon.log_level", "debug")
        assert sample_config.log_level == "debug"

    def test_set_max_concurrent(self, sample_config):
        _apply_config_value(sample_config, "daemon.max_concurrent_requests", "16")
        assert sample_config.max_concurrent_requests == 16

    def test_set_provider_enabled(self, sample_config):
        _apply_config_value(sample_config, "providers.ollama.enabled", "false")
        assert sample_config.providers["ollama"].enabled is False

    def test_set_resource_vram(self, sample_config):
        _apply_config_value(sample_config, "resources.max_vram_usage_pct", "90")
        assert sample_config.resources.max_vram_usage_pct == 90


class TestWriteConfigToml:
    def test_writes_valid_toml(self, sample_config, tmp_path):
        path = tmp_path / "test.toml"
        _write_config_toml(sample_config, path)

        assert path.exists()
        content = path.read_text()
        assert "[daemon]" in content
        assert "[defaults]" in content
        assert "[resources]" in content
        assert 'embedding = "ollama/nomic-embed-text"' in content

    def test_roundtrip(self, sample_config, tmp_path):
        path = tmp_path / "test.toml"
        _write_config_toml(sample_config, path)

        reloaded = Config.load(path)
        assert reloaded.default_embedding == sample_config.default_embedding
        assert reloaded.default_generation == sample_config.default_generation
        assert reloaded.log_level == sample_config.log_level


class TestConfigGetCommand:
    def test_get_specific_key(self, runner, tmp_path, monkeypatch):
        config = Config(default_generation="openai/gpt-4o")
        config_path = tmp_path / "modelbus.toml"
        _write_config_toml(config, config_path)

        # Load the config before patching to avoid recursion
        loaded = Config.load(config_path)
        monkeypatch.setattr(Config, "load", classmethod(lambda cls, path=None: loaded))

        result = runner.invoke(main, ["config", "get", "defaults.generation"])
        assert result.exit_code == 0
        assert "openai/gpt-4o" in result.output

    def test_get_unknown_key(self, runner, monkeypatch):
        monkeypatch.setattr(Config, "load", classmethod(lambda cls, path=None: Config()))

        result = runner.invoke(main, ["config", "get", "nonexistent.key"])
        assert result.exit_code != 0

    def test_get_all(self, runner, monkeypatch):
        monkeypatch.setattr(Config, "load", classmethod(lambda cls, path=None: Config()))

        result = runner.invoke(main, ["config", "get"])
        assert result.exit_code == 0
        assert "defaults.embedding" in result.output


class TestConfigSetCommand:
    def test_set_value(self, runner, tmp_path, monkeypatch):
        config_path = tmp_path / "modelbus.toml"

        monkeypatch.setattr(Config, "load", classmethod(lambda cls, path=None: Config()))
        # Patch the default config path used in config_set
        import yaaos_modelbus.config as config_mod

        monkeypatch.setattr(config_mod, "_DEFAULT_CONFIG_PATH", str(config_path))

        result = runner.invoke(main, ["config", "set", "defaults.generation", "openai/gpt-4o"])
        assert result.exit_code == 0
        assert "Set" in result.output
        assert config_path.exists()

        # Verify the file was written correctly — load directly from path
        reloaded = Config._from_dict({})
        import tomli

        with open(config_path, "rb") as f:
            raw = tomli.load(f)
        reloaded = Config._from_dict(raw)
        assert reloaded.default_generation == "openai/gpt-4o"

    def test_set_unknown_key(self, runner, monkeypatch):
        monkeypatch.setattr(Config, "load", classmethod(lambda cls, path=None: Config()))

        result = runner.invoke(main, ["config", "set", "bad.key", "value"])
        assert result.exit_code != 0
