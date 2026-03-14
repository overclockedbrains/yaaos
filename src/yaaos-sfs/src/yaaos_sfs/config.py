"""Configuration loading for YAAOS SFS."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib as tomli  # Python 3.11+ stdlib
except ImportError:
    import tomli  # Fallback for older Python


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
    supported_extensions: list[str] = field(
        default_factory=lambda: [
            # Code
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".rs",
            ".go",
            ".c",
            ".h",
            ".cpp",
            ".hpp",
            ".cc",
            ".java",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".scala",
            ".cs",
            ".lua",
            ".dart",
            # Shell
            ".sh",
            ".bash",
            ".zsh",
            # Markup & prose
            ".md",
            ".txt",
            ".rst",
            ".org",
            ".tex",
            # Web
            ".html",
            ".htm",
            ".xml",
            ".css",
            ".scss",
            # Config & data
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".env",
            ".csv",
            ".tsv",
            # Documents (Tier 2)
            ".pdf",
            ".docx",
            ".pptx",
            ".xlsx",
            ".epub",
            ".rtf",
            # Media metadata (Tier 3)
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            ".mp3",
            ".wav",
            ".flac",
            ".m4a",
            ".ogg",
            ".aac",
            ".mp4",
            ".mkv",
            ".avi",
            ".webm",
            ".mov",
            # Other
            ".sql",
            ".graphql",
            ".proto",
        ]
    )

    # SFS v2 new settings
    batch_size: int = 50
    debounce_ms: int = 1500
    max_file_size_mb: float = 5.0
    rescan_interval_min: int = 10
    query_port: int = 9749

    # OpenAI settings (optional)
    openai_api_key: str | None = None
    openai_model: str = "text-embedding-3-small"

    # Voyage settings (optional)
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-code-3"

    # Device settings (auto-detected if not set)
    device: str | None = None  # "cuda", "mps", "cpu", or None for auto-detect

    # Ollama settings (optional)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"

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
            if "batch_size" in sfs:
                config.batch_size = sfs["batch_size"]
            if "debounce_ms" in sfs:
                config.debounce_ms = sfs["debounce_ms"]
            if "max_file_size_mb" in sfs:
                config.max_file_size_mb = sfs["max_file_size_mb"]
            if "rescan_interval_min" in sfs:
                config.rescan_interval_min = sfs["rescan_interval_min"]
            if "query_port" in sfs:
                config.query_port = sfs["query_port"]

            embedding = data.get("embedding", {})
            if "provider" in embedding:
                config.embedding_provider = embedding["provider"]
            if "model" in embedding:
                config.embedding_model = embedding["model"]
            if "dims" in embedding:
                config.embedding_dims = embedding["dims"]
            if "device" in embedding:
                config.device = embedding["device"]

            # Provider-specific config sections
            openai = data.get("providers", {}).get("openai", data.get("openai", {}))
            if "api_key" in openai:
                config.openai_api_key = openai["api_key"]
            elif "api_key_env" in openai:
                config.openai_api_key = os.environ.get(openai["api_key_env"])
            if "model" in openai:
                config.openai_model = openai["model"]

            voyage = data.get("providers", {}).get("voyage", {})
            if "api_key" in voyage:
                config.voyage_api_key = voyage["api_key"]
            elif "api_key_env" in voyage:
                config.voyage_api_key = os.environ.get(voyage["api_key_env"])
            if "model" in voyage:
                config.voyage_model = voyage["model"]

            ollama = data.get("providers", {}).get("ollama", {})
            if "base_url" in ollama:
                config.ollama_base_url = ollama["base_url"]
            if "model" in ollama:
                config.ollama_model = ollama["model"]

        # Ensure directories exist
        config.watch_dir.mkdir(parents=True, exist_ok=True)
        config.db_path.parent.mkdir(parents=True, exist_ok=True)

        return config
