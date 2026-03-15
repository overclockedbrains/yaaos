"""Configuration loading for Model Bus.

Config priority: defaults < TOML file < environment variables.
API keys are NEVER stored in config files — always via env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomli


_DEFAULT_CONFIG_PATH = Path("~/.config/yaaos/modelbus.toml")
_DEFAULT_SOCKET_PATH = "/run/yaaos/modelbus.sock"
_FALLBACK_SOCKET_DIR = Path("~/.local/run/yaaos")


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""

    name: str
    enabled: bool = True
    base_url: str | None = None
    default_model: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        """Load API key from environment. Never from config file."""
        env_var = f"{self.name.upper()}_API_KEY"
        return os.environ.get(env_var)


@dataclass
class ResourceConfig:
    """Resource management configuration."""

    max_vram_usage_pct: int = 85
    model_idle_timeout_sec: int = 300
    max_ram_usage_pct: int = 80


@dataclass
class Config:
    """Model Bus configuration."""

    # Daemon settings
    socket_path: Path = field(default_factory=lambda: Path(_DEFAULT_SOCKET_PATH))
    log_level: str = "info"
    max_concurrent_requests: int = 8

    # Default models per capability
    default_embedding: str = "ollama/nomic-embed-text"
    default_generation: str = "ollama/phi3:mini"
    default_chat: str = "ollama/phi3:mini"

    # Provider configs
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    # Resource management
    resources: ResourceConfig = field(default_factory=ResourceConfig)

    def __post_init__(self):
        # Ensure default providers exist
        if "ollama" not in self.providers:
            self.providers["ollama"] = ProviderConfig(
                name="ollama",
                enabled=True,
                base_url="http://localhost:11434",
            )
        if "local" not in self.providers:
            self.providers["local"] = ProviderConfig(name="local", enabled=True)

    def get_default_model(self, capability: str) -> str:
        """Get the default model for a capability (embed/generate/chat)."""
        if capability in ("embed", "embedding"):
            return self.default_embedding
        elif capability == "generate":
            return self.default_generation
        elif capability == "chat":
            return self.default_chat
        return self.default_generation

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config from TOML file with env var overrides.

        Falls back to defaults if no config file exists.
        """
        config_path = (path or _DEFAULT_CONFIG_PATH).expanduser()

        if config_path.exists():
            with open(config_path, "rb") as f:
                raw = tomli.load(f)
        else:
            raw = {}

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict) -> Config:
        """Build Config from a parsed TOML dict."""
        daemon = raw.get("daemon", {})
        defaults = raw.get("defaults", {})
        resources_raw = raw.get("resources", {})
        providers_raw = raw.get("providers", {})

        # Resolve socket path — prefer /run/yaaos/ on Linux, fallback for non-root
        socket_str = daemon.get("socket_path", _DEFAULT_SOCKET_PATH)
        socket_path = Path(socket_str)
        if not _can_create_socket(socket_path):
            socket_path = _FALLBACK_SOCKET_DIR.expanduser() / "modelbus.sock"

        # Parse providers
        providers = {}
        for name, prov_raw in providers_raw.items():
            providers[name] = ProviderConfig(
                name=name,
                enabled=prov_raw.get("enabled", True),
                base_url=prov_raw.get("base_url"),
                default_model=prov_raw.get("default_model"),
                extra={
                    k: v
                    for k, v in prov_raw.items()
                    if k not in ("enabled", "base_url", "default_model")
                },
            )

        return cls(
            socket_path=socket_path,
            log_level=daemon.get("log_level", "info"),
            max_concurrent_requests=daemon.get("max_concurrent_requests", 8),
            default_embedding=defaults.get("embedding", "ollama/nomic-embed-text"),
            default_generation=defaults.get("generation", "ollama/phi3:mini"),
            default_chat=defaults.get("chat", "ollama/phi3:mini"),
            providers=providers,
            resources=ResourceConfig(
                max_vram_usage_pct=resources_raw.get("max_vram_usage_pct", 85),
                model_idle_timeout_sec=resources_raw.get("model_idle_timeout_sec", 300),
                max_ram_usage_pct=resources_raw.get("max_ram_usage_pct", 80),
            ),
        )


def _can_create_socket(path: Path) -> bool:
    """Check if we can create a socket at the given path."""
    parent = path.parent
    try:
        if parent.exists():
            return os.access(parent, os.W_OK)
        # Check if grandparent is writable (for mkdir)
        return os.access(parent.parent, os.W_OK) if parent.parent.exists() else False
    except OSError:
        return False
