"""Tests for config loading."""

from __future__ import annotations

from yaaos_sfs.config import Config


def test_default_config():
    """Config with defaults should have sane values."""
    config = Config()
    assert config.embedding_provider == "local"
    assert config.embedding_model == "all-MiniLM-L6-v2"
    assert config.embedding_dims == 384
    assert config.chunk_size == 512
    assert config.chunk_overlap == 50
    assert ".py" in config.supported_extensions
    assert ".md" in config.supported_extensions
    assert ".pdf" in config.supported_extensions


def test_load_missing_config(tmp_path):
    """Loading a nonexistent config file should return defaults."""
    config = Config.load(tmp_path / "nonexistent.toml")
    assert config.embedding_provider == "local"


def test_load_toml_config(tmp_path):
    """Loading a valid TOML config should override defaults."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sfs]
watch_dir = "~/test_semantic"
chunk_size = 256

[embedding]
provider = "openai"
model = "text-embedding-3-small"
dims = 1536
""")
    config = Config.load(config_file)
    assert config.chunk_size == 256
    assert config.embedding_provider == "openai"
    assert config.embedding_model == "text-embedding-3-small"
    assert config.embedding_dims == 1536


def test_load_config_creates_dirs(tmp_path):
    """Config.load should create watch_dir and db_path parent if missing."""
    config_file = tmp_path / "config.toml"
    watch = tmp_path / "deep" / "nested" / "watch"
    db = tmp_path / "deep" / "nested" / "db" / "sfs.db"

    config_file.write_text(f"""
[sfs]
watch_dir = "{watch}"
db_path = "{db}"
""")
    config = Config.load(config_file)
    assert config.watch_dir.exists()
    assert config.db_path.parent.exists()


def test_supported_extensions_override(tmp_path):
    """Custom extensions list should replace defaults."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[sfs]
extensions = [".txt", ".md"]
""")
    config = Config.load(config_file)
    assert config.supported_extensions == [".txt", ".md"]
    assert ".py" not in config.supported_extensions


def test_openai_api_key_from_env(tmp_path, monkeypatch):
    """OpenAI API key can be loaded from environment variable."""
    monkeypatch.setenv("MY_OPENAI_KEY", "sk-test-123")
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[openai]
api_key_env = "MY_OPENAI_KEY"
""")
    config = Config.load(config_file)
    assert config.openai_api_key == "sk-test-123"
