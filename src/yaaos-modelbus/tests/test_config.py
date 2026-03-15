"""Tests for yaaos_modelbus.config."""

from yaaos_modelbus.config import Config, ProviderConfig, ResourceConfig


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.log_level == "info"
        assert config.max_concurrent_requests == 8
        assert config.default_embedding == "ollama/nomic-embed-text"
        assert config.default_generation == "ollama/phi3:mini"
        assert "ollama" in config.providers
        assert "local" in config.providers

    def test_get_default_model(self):
        config = Config()
        assert config.get_default_model("embed") == "ollama/nomic-embed-text"
        assert config.get_default_model("embedding") == "ollama/nomic-embed-text"
        assert config.get_default_model("generate") == "ollama/phi3:mini"
        assert config.get_default_model("chat") == "ollama/phi3:mini"

    def test_load_nonexistent(self, tmp_path):
        """Loading a nonexistent config file should return defaults."""
        config = Config.load(tmp_path / "nonexistent.toml")
        assert config.log_level == "info"
        assert "ollama" in config.providers

    def test_load_from_toml(self, tmp_path):
        """Load a valid TOML config."""
        config_file = tmp_path / "test.toml"
        config_file.write_text("""
[daemon]
log_level = "debug"
max_concurrent_requests = 16

[defaults]
embedding = "voyage/voyage-3.5"
generation = "openai/gpt-4o"

[providers.ollama]
enabled = true
base_url = "http://localhost:11434"

[providers.openai]
enabled = true

[resources]
max_vram_usage_pct = 90
model_idle_timeout_sec = 600
""")
        config = Config.load(config_file)
        assert config.log_level == "debug"
        assert config.max_concurrent_requests == 16
        assert config.default_embedding == "voyage/voyage-3.5"
        assert config.default_generation == "openai/gpt-4o"
        assert config.providers["ollama"].base_url == "http://localhost:11434"
        assert config.providers["openai"].enabled is True
        assert config.resources.max_vram_usage_pct == 90
        assert config.resources.model_idle_timeout_sec == 600


class TestProviderConfig:
    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        prov = ProviderConfig(name="openai")
        assert prov.api_key == "sk-test-123"

    def test_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        prov = ProviderConfig(name="openai")
        assert prov.api_key is None


class TestResourceConfig:
    def test_defaults(self):
        rc = ResourceConfig()
        assert rc.max_vram_usage_pct == 85
        assert rc.model_idle_timeout_sec == 300
        assert rc.max_ram_usage_pct == 80
