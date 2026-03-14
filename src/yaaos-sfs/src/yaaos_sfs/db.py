"""SQLite + sqlite-vec database layer for semantic file indexing."""

from __future__ import annotations

import sqlite3
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path

import xxhash
import sqlite_vec


def _serialize_vector(vec: list[float]) -> bytes:
    """Serialize a float vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_vector(data: bytes) -> list[float]:
    """Deserialize bytes back to float vector."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class Database:
    def __init__(self, db_path: Path, embedding_dims: int = 384):
        self.db_path = db_path
        self.embedding_dims = embedding_dims
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                extension TEXT,
                size_bytes INTEGER,
                modified_at TEXT,
                mtime_ns INTEGER,
                indexed_at TEXT,
                content_hash TEXT,
                chunk_count INTEGER DEFAULT 0
            );
        """)

        # Safely migrate existing databases to add mtime_ns
        try:
            self.conn.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists

        self.conn.executescript(f"""

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
                chunk_index INTEGER,
                chunk_text TEXT,
                token_count INTEGER
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_text,
                content='chunks',
                content_rowid='id'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                id INTEGER PRIMARY KEY,
                embedding float[{self.embedding_dims}]
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) VALUES('delete', old.id, old.chunk_text);
            END;
        """)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.commit()

    def file_needs_indexing(self, path: Path) -> bool:
        """Check if file needs (re)indexing using stat-first, falling back to xxHash128."""
        try:
            stat = path.stat()
            current_mtime_ns = stat.st_mtime_ns
            current_size = stat.st_size
        except OSError:
            return True

        with self._lock:
            row = self.conn.execute(
                "SELECT mtime_ns, size_bytes, content_hash FROM files WHERE path = ?", (str(path),)
            ).fetchone()

        if row is None:
            return True

        if row["mtime_ns"] == current_mtime_ns and row["size_bytes"] == current_size:
            return False

        current_hash = self._xxhash_file(path)
        return row["content_hash"] != current_hash

    def upsert_file(
        self,
        path: Path,
        chunks: list[str],
        embeddings: list[list[float]],
    ):
        """Insert or update a file with its chunks and embeddings."""
        stat = path.stat()
        mtime_ns = stat.st_mtime_ns
        content_hash = self._xxhash_file(path)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # Delete old data if exists
            old = self.conn.execute(
                "SELECT id FROM files WHERE path = ?", (str(path),)
            ).fetchone()
            if old:
                file_id = old["id"]
                # Get old chunk IDs to remove from vec table
                old_chunks = self.conn.execute(
                    "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                ).fetchall()
                for chunk in old_chunks:
                    self.conn.execute("DELETE FROM chunks_vec WHERE id = ?", (chunk["id"],))
                self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                self.conn.execute(
                    """UPDATE files SET filename=?, extension=?, size_bytes=?, mtime_ns=?,
                       modified_at=?, indexed_at=?, content_hash=?, chunk_count=?
                       WHERE id=?""",
                    (
                        path.name,
                        path.suffix,
                        stat.st_size,
                        mtime_ns,
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        now,
                        content_hash,
                        len(chunks),
                        file_id,
                    ),
                )
            else:
                cursor = self.conn.execute(
                    """INSERT INTO files (path, filename, extension, size_bytes, mtime_ns,
                       modified_at, indexed_at, content_hash, chunk_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(path),
                        path.name,
                        path.suffix,
                        stat.st_size,
                        mtime_ns,
                        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        now,
                        content_hash,
                        len(chunks),
                    ),
                )
                file_id = cursor.lastrowid

            # Insert new chunks
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                cursor = self.conn.execute(
                    """INSERT INTO chunks (file_id, chunk_index, chunk_text, token_count)
                       VALUES (?, ?, ?, ?)""",
                    (file_id, i, chunk_text, len(chunk_text.split())),
                )
                chunk_id = cursor.lastrowid
                self.conn.execute(
                    "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
                    (chunk_id, _serialize_vector(embedding)),
                )

            self.conn.commit()

    def remove_file(self, path: Path):
        """Remove a file and all its chunks from the index."""
        with self._lock:
            old = self.conn.execute(
                "SELECT id FROM files WHERE path = ?", (str(path),)
            ).fetchone()
            if old:
                file_id = old["id"]
                old_chunks = self.conn.execute(
                    "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                ).fetchall()
                for chunk in old_chunks:
                    self.conn.execute("DELETE FROM chunks_vec WHERE id = ?", (chunk["id"],))
                self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
                self.conn.commit()

    def remove_files_batch(self, paths: list[Path]):
        """Efficiently remove multiple files in a single transaction."""
        if not paths:
            return
        with self._lock:
            # Get all file IDs first
            placeholders = ",".join(["?"] * len(paths))
            rows = self.conn.execute(
                f"SELECT id FROM files WHERE path IN ({placeholders})", [str(p) for p in paths]
            ).fetchall()
            file_ids = [r["id"] for r in rows]

            if not file_ids:
                return

            ids_placeholders = ",".join(["?"] * len(file_ids))
            
            # Get all chunk IDs for these files
            chunk_rows = self.conn.execute(
                f"SELECT id FROM chunks WHERE file_id IN ({ids_placeholders})", file_ids
            ).fetchall()
            chunk_ids = [r["id"] for r in chunk_rows]

            # 1. Delete associated vectors
            if chunk_ids:
                c_placeholders = ",".join(["?"] * len(chunk_ids))
                self.conn.execute(f"DELETE FROM chunks_vec WHERE id IN ({c_placeholders})", chunk_ids)

            # 2. Delete chunks (cascades to FTS) and files
            self.conn.execute(f"DELETE FROM chunks WHERE file_id IN ({ids_placeholders})", file_ids)
            self.conn.execute(f"DELETE FROM files WHERE id IN ({ids_placeholders})", file_ids)
            self.conn.commit()

    def get_all_indexed_paths(self) -> set[Path]:
        """Get a set of all currently indexed absolute paths."""
        with self._lock:
            rows = self.conn.execute("SELECT path FROM files").fetchall()
        return {Path(r["path"]) for r in rows}

    def search_vector(self, query_embedding: list[float], top_k: int = 20) -> list[dict]:
        """Search by vector similarity."""
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT v.id, v.distance,
                       c.chunk_text, c.chunk_index, c.file_id,
                       f.path, f.filename
                FROM chunks_vec v
                JOIN chunks c ON c.id = v.id
                JOIN files f ON f.id = c.file_id
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
                """,
                (_serialize_vector(query_embedding), top_k),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_fts(self, query: str, top_k: int = 20) -> list[dict]:
        """Search by keyword (FTS5 BM25)."""
        # Escape special FTS5 characters
        safe_query = query.replace('"', '""')
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT c.id, rank AS score,
                       c.chunk_text, c.chunk_index, c.file_id,
                       f.path, f.filename
                FROM chunks_fts fts
                JOIN chunks c ON c.id = fts.rowid
                JOIN files f ON f.id = c.file_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{safe_query}"', top_k),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get index statistics."""
        with self._lock:
            files = self.conn.execute("SELECT COUNT(*) as n FROM files").fetchone()["n"]
            chunks = self.conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()["n"]
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "files": files,
            "chunks": chunks,
            "db_size_mb": round(db_size / (1024 * 1024), 1),
        }

    def close(self):
        with self._lock:
            self.conn.close()

    @staticmethod
    def _xxhash_file(path: Path) -> str:
        h = xxhash.xxh128()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(81920), b""):
                h.update(block)
        return h.hexdigest()
