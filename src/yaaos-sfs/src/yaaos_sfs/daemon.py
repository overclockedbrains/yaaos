"""File watcher daemon — monitors a directory and auto-indexes files (v2)."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

try:
    from tqdm import tqdm
except ImportError:
    # If tqdm isn't available, fallback to a dummy
    class tqdm:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            pass


from .config import Config
from .db import Database
from .filter import FileFilter
from .extractors import extract_text
from .chunkers import chunk_text
from .providers import EmbeddingProvider
from .providers.local import LocalEmbeddingProvider
from .server import QueryServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yaaos-sfs")


class SFSHandler(FileSystemEventHandler):
    """Handles file events with debouncing and batch processing."""

    def __init__(self, db: Database, provider: EmbeddingProvider, config: Config):
        self.db = db
        self.provider = provider
        self.config = config
        self.file_filter = FileFilter(
            config.watch_dir, config.supported_extensions, config.max_file_size_mb
        )

        self.pending_events: dict[Path, float] = {}
        self.lock = threading.Lock()

        # Start debounce worker
        self.worker = threading.Thread(target=self._debounce_worker, daemon=True)
        self.worker.start()

    def _debounce_worker(self):
        """Background thread that consumes files that have settled."""
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            to_process = []

            with self.lock:
                for path, timestamp in list(self.pending_events.items()):
                    if now - timestamp >= (self.config.debounce_ms / 1000.0):
                        to_process.append(path)
                        del self.pending_events[path]

            if to_process:
                self._process_batch(to_process)

    def _process_batch(self, paths: list[Path]):
        """Process a batch of files linearly for real-time updates."""
        files_to_embed = []
        chunks_to_embed = []

        for path in paths:
            try:
                # Re-check existence as it could have been deleted during debounce
                if not path.exists():
                    continue

                if not self.db.file_needs_indexing(path):
                    continue

                text = extract_text(path)
                if not text or not text.strip():
                    continue

                chunks = chunk_text(
                    text,
                    path=path,
                    chunk_size=self.config.chunk_size,
                    chunk_overlap=self.config.chunk_overlap,
                )
                if not chunks:
                    continue

                files_to_embed.append((path, chunks))
                chunks_to_embed.extend(chunks)

                # If batch size reached, flush
                if len(chunks_to_embed) >= self.config.batch_size:
                    self._embed_and_upsert(files_to_embed, chunks_to_embed)
                    files_to_embed.clear()
                    chunks_to_embed.clear()

            except Exception as e:
                log.error(f"Failed to process {path.name}: {e}")

        # Flush remainder
        if files_to_embed:
            self._embed_and_upsert(files_to_embed, chunks_to_embed)

    def _embed_and_upsert(self, files_batch, chunks_batch):
        try:
            embeddings = self.provider.embed(chunks_batch)
            offset = 0
            for path, file_chunks in files_batch:
                n = len(file_chunks)
                file_embs = embeddings[offset : offset + n]
                offset += n
                self.db.upsert_file(path, file_chunks, file_embs)
                log.info(f"Indexed: {path.name} ({n} chunks)")
        except Exception as e:
            log.error(f"Batch embedding failed: {e}")

    def _record_event(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self.file_filter.should_index(path):
            with self.lock:
                self.pending_events[path] = time.monotonic()

    def on_created(self, event: FileSystemEvent):
        self._record_event(event)

    def on_modified(self, event: FileSystemEvent):
        self._record_event(event)

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        with self.lock:
            if path in self.pending_events:
                del self.pending_events[path]
        try:
            self.db.remove_file(path)
            log.info(f"Removed from index: {path.name}")
        except Exception:
            pass


def _initial_scan(handler: SFSHandler, watch_dir: Path, config: Config, quiet: bool = False):
    """Index all existing files using ThreadPoolExecutor + batching.

    When quiet=True (periodic re-scan), only logs if new files are found.
    """
    file_filter = handler.file_filter
    if not quiet:
        log.info(f"Scanning directory: {watch_dir}")

    files_to_check = []
    for root, dirs, filenames in os.walk(watch_dir):
        # 1. Very fast dir pruning
        dirs[:] = [d for d in dirs if file_filter.is_dir_allowed(Path(os.path.join(root, d)))]

        # 2. File filtering
        for f in filenames:
            path = Path(root) / f
            if file_filter.should_index(path):
                files_to_check.append(path)

    # Cleanup: find files in DB that are no longer on disk or allowed
    valid_paths = set(files_to_check)
    db_paths = handler.db.get_all_indexed_paths()
    orphans = db_paths - valid_paths

    if orphans:
        log.info(f"Cleaning up {len(orphans)} orphaned/deleted files from index...")
        handler.db.remove_files_batch(list(orphans))

    if not files_to_check:
        if not quiet:
            log.info("No files to index.")
        return

    if not quiet:
        log.info(f"Found {len(files_to_check)} indexable files. Checking for changes...")
    to_index = [f for f in files_to_check if handler.db.file_needs_indexing(f)]

    if not to_index:
        if not quiet:
            log.info("All files are up to date. Indexing caught up.")
        return

    log.info(f"{'Re-scan' if quiet else 'Initial scan'}: {len(to_index)} files need indexing.")

    def process_text(path: Path):
        try:
            text = extract_text(path)
            if not text or not text.strip():
                return None
            chunks = chunk_text(
                text,
                path=path,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
            )
            if not chunks:
                return None
            return (path, chunks)
        except Exception as e:
            log.debug(f"Failed to extract {path.name}: {e}")
            return None

    current_batch_files = []
    current_batch_chunks = []

    # Use ThreadPoolExecutor just for I/O + text extraction + local chunking
    # Embedding happens sequentially in batches
    workers = min(32, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_text, p) for p in to_index]

        with tqdm(total=len(to_index), desc="Indexing") as pbar:
            for fut in as_completed(futures):
                pbar.update(1)
                res = fut.result()
                if not res:
                    continue

                path, chunks = res
                current_batch_files.append((path, chunks))
                current_batch_chunks.extend(chunks)

                if len(current_batch_chunks) >= config.batch_size:
                    handler._embed_and_upsert(current_batch_files, current_batch_chunks)
                    current_batch_files.clear()
                    current_batch_chunks.clear()

            # Flush remaining
            if current_batch_files:
                handler._embed_and_upsert(current_batch_files, current_batch_chunks)

    log.info(f"{'Re-scan' if quiet else 'Initial scan'} complete.")


def _get_provider(config: Config) -> EmbeddingProvider:
    provider = config.embedding_provider

    if provider == "openai":
        from .providers.openai_provider import OpenAIEmbeddingProvider

        if not config.openai_api_key:
            log.error("OpenAI provider selected but no API key configured.")
            sys.exit(1)
        return OpenAIEmbeddingProvider(config.openai_api_key, config.openai_model)

    if provider == "voyage":
        from .providers.voyage_provider import VoyageEmbeddingProvider

        return VoyageEmbeddingProvider(config.voyage_api_key, config.voyage_model)

    if provider == "ollama":
        from .providers.ollama_provider import OllamaEmbeddingProvider

        return OllamaEmbeddingProvider(config.ollama_model, config.ollama_base_url)

    # Default: local sentence-transformers (with GPU auto-detection)
    return LocalEmbeddingProvider(config.embedding_model, device=config.device)


def main():
    """Entry point for the yaaos-sfs daemon."""
    config = Config.load()
    log.info("YAAOS Semantic File System v0.2.0")
    log.info(f"Watching: {config.watch_dir}")
    log.info(f"Database: {config.db_path}")
    log.info(f"Provider: {config.embedding_provider} ({config.embedding_model})")

    provider = _get_provider(config)
    log.info(f"Embedding model loaded ({provider.dims} dims)")

    db = Database(config.db_path, embedding_dims=provider.dims)
    handler = SFSHandler(db, provider, config)

    # Start query server
    query_server = QueryServer(db, provider, config)
    query_server.start_background()

    # Initial scan
    _initial_scan(handler, config.watch_dir, config)

    # Start periodic re-scan thread
    rescan_stop = threading.Event()

    def _periodic_rescan():
        interval = config.rescan_interval_min * 60
        while not rescan_stop.wait(interval):
            try:
                _initial_scan(handler, config.watch_dir, config, quiet=True)
            except Exception as e:
                log.error(f"Periodic re-scan failed: {e}")

    rescan_thread = threading.Thread(target=_periodic_rescan, daemon=True)
    rescan_thread.start()
    log.info(f"Periodic re-scan every {config.rescan_interval_min} minutes")

    # Start watching
    observer = Observer()
    observer.schedule(handler, str(config.watch_dir), recursive=True)
    observer.start()
    log.info("Watching for file changes... (Ctrl+C to stop)")

    def shutdown(sig, frame):
        log.info("Shutting down...")
        rescan_stop.set()
        query_server.shutdown()
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
