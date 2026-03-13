"""Configuration loading for YAAOS SFS."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomli
except ImportError:
    import tomllib as tomli  # Python 3.11+ stdlib


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "yaaos" / "config.toml"
DEFAULT_WATCH_DIR = Path.home() / "semantic"
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "yaaos" / "sfs.db"


@dataclass
class Config:
    watch_dir: Path = field(default_factory=lambda: DEFAULT_WATCH_DIR)
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    embedding_provider: str = "local"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dims: int = 384
    chunk_size: int = 512
    chunk_overlap: int = 50
    supported_extensions: list[str] = field(default_factory=lambda: [
        ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx",
        ".json", ".yaml", ".yml", ".toml",
        ".sh", ".bash", ".zsh",
        ".rs", ".go", ".c", ".h", ".cpp", ".hpp",
        ".java", ".rb", ".php",
        ".css", ".html", ".xml",
        ".pdf",
    ])

    # OpenAI settings (optional)
    openai_api_key: str | None = None
    openai_model: str = "text-embedding-3-small"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        config_path = path or DEFAULT_CONFIG_PATH
        config = cls()

        if config_path.exists():
            with open(config_path, "rb") as f:
                data = tomli.load(f)

            sfs = data.get("sfs", {})
            if "watch_dir" in sfs:
                config.watch_dir = Path(sfs["watch_dir"]).expanduser()
            if "db_path" in sfs:
                config.db_path = Path(sfs["db_path"]).expanduser()
            if "chunk_size" in sfs:
                config.chunk_size = sfs["chunk_size"]
            if "chunk_overlap" in sfs:
                config.chunk_overlap = sfs["chunk_overlap"]
            if "extensions" in sfs:
                config.supported_extensions = sfs["extensions"]

            embedding = data.get("embedding", {})
            if "provider" in embedding:
                config.embedding_provider = embedding["provider"]
            if "model" in embedding:
                config.embedding_model = embedding["model"]
            if "dims" in embedding:
                config.embedding_dims = embedding["dims"]

            openai = data.get("openai", {})
            if "api_key" in openai:
                config.openai_api_key = openai["api_key"]
            elif "api_key_env" in openai:
                config.openai_api_key = os.environ.get(openai["api_key_env"])
            if "model" in openai:
                config.openai_model = openai["model"]

        # Ensure directories exist
        config.watch_dir.mkdir(parents=True, exist_ok=True)
        config.db_path.parent.mkdir(parents=True, exist_ok=True)

        return config
