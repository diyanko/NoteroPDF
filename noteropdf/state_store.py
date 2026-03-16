from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
    last_error_code: Optional[str]


class StateStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock_path = Path(f"{db_path}.lock")
        self._lock_fd: int | None = None
        self._acquire_lock()
        try:
            self._conn = sqlite3.connect(str(db_path))
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._init_schema()
        except Exception:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _acquire_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Create lock file with owner-only permissions (0o600)
            self._lock_fd = os.open(
                str(self._lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.write(self._lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError as exc:
            raise RuntimeError(
                f"Sync state lock exists: {self._lock_path}. "
                "Another run may already be active. If not, remove this lock file and retry."
            ) from exc

    def _init_schema(self) -> None:
        self._conn.execute(
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
                last_error_code TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_state_page ON sync_state(notion_page_id)"
        )
        self._conn.commit()

    def get(self, zotero_item_key: str) -> StateRecord | None:
        cur = self._conn.execute(
            """
            SELECT zotero_item_key, notion_page_id, pdf_absolute_path, pdf_size,
                   pdf_mtime_ns, pdf_sha256, last_sync_time, last_status, last_error_code
            FROM sync_state WHERE zotero_item_key = ?
            """,
            (zotero_item_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return StateRecord(*row)

    def upsert(self, rec: StateRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO sync_state (
                zotero_item_key, notion_page_id, pdf_absolute_path, pdf_size,
                pdf_mtime_ns, pdf_sha256, last_sync_time, last_status, last_error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(zotero_item_key) DO UPDATE SET
                notion_page_id = excluded.notion_page_id,
                pdf_absolute_path = excluded.pdf_absolute_path,
                pdf_size = excluded.pdf_size,
                pdf_mtime_ns = excluded.pdf_mtime_ns,
                pdf_sha256 = excluded.pdf_sha256,
                last_sync_time = excluded.last_sync_time,
                last_status = excluded.last_status,
                last_error_code = excluded.last_error_code
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
            ),
        )
        self._conn.commit()

    def list_known_page_ids(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT notion_page_id FROM sync_state WHERE notion_page_id <> ''"
        )
        return [row[0] for row in cur.fetchall()]

    def clear_all(self) -> None:
        self._conn.execute("DELETE FROM sync_state")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass
