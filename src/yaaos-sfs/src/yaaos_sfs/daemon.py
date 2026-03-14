"""File watcher daemon — monitors a directory and auto-indexes files."""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .config import Config
from .db import Database
from .indexer import extract_text, chunk_text
from .providers import EmbeddingProvider
from .providers.local import LocalEmbeddingProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yaaos-sfs")


class SFSHandler(FileSystemEventHandler):
    """Handles file events and triggers indexing."""

    def __init__(self, db: Database, provider: EmbeddingProvider, config: Config):
        self.db = db
        self.provider = provider
        self.config = config

    def _should_index(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name.startswith("."):
            return False
        return path.suffix.lower() in self.config.supported_extensions

    def _index_file(self, path: Path):
        try:
            if not self.db.file_needs_indexing(path):
                return

            text = extract_text(path)
            if not text or not text.strip():
                log.warning(f"No text extracted: {path.name}")
                return

            chunks = chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
            if not chunks:
                return

            embeddings = self.provider.embed(chunks)
            self.db.upsert_file(path, chunks, embeddings)
            log.info(f"Indexed: {path.name} ({len(chunks)} chunks)")
        except Exception as e:
            log.error(f"Failed to index {path.name}: {e}")

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            path = Path(event.src_path)
            if self._should_index(path):
                self._index_file(path)

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            path = Path(event.src_path)
            if self._should_index(path):
                self._index_file(path)

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            path = Path(event.src_path)
            self.db.remove_file(path)
            log.info(f"Removed from index: {path.name}")


def _initial_scan(handler: SFSHandler, watch_dir: Path):
    """Index all existing files on startup."""
    files = [f for f in watch_dir.rglob("*") if handler._should_index(f)]
    if not files:
        log.info("No files to index in initial scan.")
        return

    log.info(f"Initial scan: {len(files)} files to index...")
    for i, f in enumerate(files, 1):
        handler._index_file(f)
        if i % 50 == 0:
            log.info(f"  Progress: {i}/{len(files)}")
    log.info(f"Initial scan complete: {len(files)} files processed.")


def _get_provider(config: Config) -> EmbeddingProvider:
    if config.embedding_provider == "openai":
        from .providers.openai_provider import OpenAIEmbeddingProvider

        if not config.openai_api_key:
            log.error("OpenAI provider selected but no API key configured.")
            sys.exit(1)
        return OpenAIEmbeddingProvider(config.openai_api_key, config.openai_model)
    return LocalEmbeddingProvider(config.embedding_model)


def main():
    """Entry point for the yaaos-sfs daemon."""
    config = Config.load()
    log.info("YAAOS Semantic File System v0.1.0")
    log.info(f"Watching: {config.watch_dir}")
    log.info(f"Database: {config.db_path}")
    log.info(f"Provider: {config.embedding_provider} ({config.embedding_model})")

    provider = _get_provider(config)
    log.info(f"Embedding model loaded ({provider.dims} dims)")

    db = Database(config.db_path, embedding_dims=provider.dims)
    handler = SFSHandler(db, provider, config)

    # Initial scan
    _initial_scan(handler, config.watch_dir)

    # Start watching
    observer = Observer()
    observer.schedule(handler, str(config.watch_dir), recursive=True)
    observer.start()
    log.info("Watching for file changes... (Ctrl+C to stop)")

    def shutdown(sig, frame):
        log.info("Shutting down...")
        observer.stop()
        db.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
