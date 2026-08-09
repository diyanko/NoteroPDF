from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock as AdvisoryFileLock
from filelock import Timeout


@dataclass(frozen=True)
class StateRecord:
    zotero_item_key: str
    notion_page_id: str
    pdf_absolute_path: str
    pdf_size: int
    pdf_mtime_ns: int
    pdf_sha256: str
    last_sync_time: str
    last_status: str
    last_error_code: str | None
    remote_file_name: str | None = None
    remote_file_type: str | None = None
    remote_file_identity: str | None = None


class FileLock:
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._lock = AdvisoryFileLock(lock_path, mode=0o600)
        self._acquire_lock()

    @classmethod
    def for_state_db(cls, db_path: Path) -> FileLock:
        return cls(Path(f"{db_path}.lock"))

    @property
    def path(self) -> Path:
        return self._lock_path

    def _acquire_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            raise RuntimeError(
                f"Another NoteroPDF run is already active: {self._lock_path}"
            ) from exc

    def close(self) -> None:
        if self._lock.is_locked:
            self._lock.release()


class StateStore:
    def __init__(self, db_path: Path, *, acquire_lock: bool = True):
        self._db_path = db_path
        self._lock = FileLock.for_state_db(db_path) if acquire_lock else None
        self._conn: sqlite3.Connection | None = None
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path))
            try:
                db_path.chmod(0o600)
            except OSError:
                # Windows does not expose POSIX owner-only mode bits.
                pass
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._init_schema()
        except Exception:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            raise

    @property
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("The local state database is closed.")
        return self._conn

    def _init_schema(self) -> None:
        conn = self._connection
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                zotero_item_key TEXT PRIMARY KEY,
                notion_page_id TEXT NOT NULL,
                pdf_absolute_path TEXT NOT NULL,
                pdf_size INTEGER NOT NULL,
                pdf_mtime_ns INTEGER NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                last_sync_time TEXT NOT NULL,
                last_status TEXT NOT NULL,
                last_error_code TEXT,
                remote_file_name TEXT,
                remote_file_type TEXT,
                remote_file_identity TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_state_page ON sync_state(notion_page_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def get_settings(self) -> dict[str, str]:
        rows = self._connection.execute("SELECT key, value FROM settings").fetchall()
        return {str(key): str(value) for key, value in rows}

    def update_settings(self, values: Mapping[str, str | None]) -> None:
        """Atomically update settings; ``None`` deletes a key."""
        conn = self._connection
        with conn:
            for key, value in values.items():
                if not key:
                    raise ValueError("Setting keys cannot be empty.")
                if value is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    conn.execute(
                        """
                        INSERT INTO settings (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (key, value),
                    )

    def get(self, zotero_item_key: str) -> StateRecord | None:
        cur = self._connection.execute(
            """
            SELECT zotero_item_key, notion_page_id, pdf_absolute_path, pdf_size,
                   pdf_mtime_ns, pdf_sha256, last_sync_time, last_status,
                   last_error_code, remote_file_name, remote_file_type,
                   remote_file_identity
            FROM sync_state WHERE zotero_item_key = ?
            """,
            (zotero_item_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return StateRecord(*row)

    def upsert(self, rec: StateRecord) -> None:
        conn = self._connection
        conn.execute(
            """
            INSERT INTO sync_state (
                zotero_item_key, notion_page_id, pdf_absolute_path, pdf_size,
                pdf_mtime_ns, pdf_sha256, last_sync_time, last_status,
                last_error_code, remote_file_name, remote_file_type,
                remote_file_identity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(zotero_item_key) DO UPDATE SET
                notion_page_id = excluded.notion_page_id,
                pdf_absolute_path = excluded.pdf_absolute_path,
                pdf_size = excluded.pdf_size,
                pdf_mtime_ns = excluded.pdf_mtime_ns,
                pdf_sha256 = excluded.pdf_sha256,
                last_sync_time = excluded.last_sync_time,
                last_status = excluded.last_status,
                last_error_code = excluded.last_error_code,
                remote_file_name = excluded.remote_file_name,
                remote_file_type = excluded.remote_file_type,
                remote_file_identity = excluded.remote_file_identity
            """,
            (
                rec.zotero_item_key,
                rec.notion_page_id,
                rec.pdf_absolute_path,
                rec.pdf_size,
                rec.pdf_mtime_ns,
                rec.pdf_sha256,
                rec.last_sync_time,
                rec.last_status,
                rec.last_error_code,
                rec.remote_file_name,
                rec.remote_file_type,
                rec.remote_file_identity,
            ),
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
