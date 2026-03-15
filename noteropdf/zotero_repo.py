from __future__ import annotations

from pathlib import Path
from typing import Iterable
import sqlite3
import urllib.parse

from .models import CandidatePdf, ZoteroAttachment, ZoteroItem
from .util import parse_notion_page_id_from_url


class ZoteroRepository:
    def __init__(self, sqlite_path: Path, storage_dir: Path, data_dir: Path):
        # Open in immutable read-only mode to guarantee no writes.
        self._uri = f"file:{sqlite_path}?mode=ro&immutable=1"
        self._conn = sqlite3.connect(self._uri, uri=True)
        self._conn.row_factory = sqlite3.Row
        self._storage_dir = storage_dir
        self._data_dir = data_dir

    def close(self) -> None:
        self._conn.close()

    def _execute_readonly(self, sql: str, params: tuple | None = None) -> sqlite3.Cursor:
        prefix = sql.lstrip().upper()
        if not (prefix.startswith("SELECT") or prefix.startswith("WITH") or prefix.startswith("PRAGMA")):
            raise RuntimeError("Internal safety check failed: attempted non-readonly Zotero SQL")
        if params is None:
            return self._conn.execute(sql)
        return self._conn.execute(sql, params)

    def read_only_guarantees(self) -> dict[str, bool]:
        return {
            "uri_mode_ro": "mode=ro" in self._uri,
            "uri_immutable": "immutable=1" in self._uri,
            "query_guard_enabled": True,
        }

    def get_library_group_map(self) -> dict[int, int]:
        cur = self._execute_readonly("SELECT libraryID, groupID FROM groups")
        result: dict[int, int] = {}
        for row in cur.fetchall():
            result[int(row["libraryID"])] = int(row["groupID"])
        return result

    def get_local_username(self) -> str | None:
        # Zotero stores account settings as key/value rows.
        cur = self._execute_readonly(
            "SELECT value FROM settings WHERE setting = 'account' AND key = 'username' LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        value = (row["value"] or "").strip()
        return value or None

    def list_parent_items(self) -> list[ZoteroItem]:
        group_map = self.get_library_group_map()
        username = self.get_local_username()
        cur = self._execute_readonly(
            """
            WITH f_title AS (
                SELECT itemData.itemID, itemDataValues.value AS title
                FROM itemData
                JOIN fields ON fields.fieldID = itemData.fieldID
                JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
                WHERE fields.fieldName = 'title'
            ),
            f_doi AS (
                SELECT itemData.itemID, itemDataValues.value AS doi
                FROM itemData
                JOIN fields ON fields.fieldID = itemData.fieldID
                JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
                WHERE fields.fieldName = 'DOI'
            )
            SELECT items.itemID, items.key, items.libraryID, f_title.title, f_doi.doi
            FROM items
            LEFT JOIN f_title ON f_title.itemID = items.itemID
            LEFT JOIN f_doi ON f_doi.itemID = items.itemID
            LEFT JOIN itemAttachments child ON child.itemID = items.itemID
            LEFT JOIN deletedItems di ON di.itemID = items.itemID
            WHERE child.itemID IS NULL
              AND di.itemID IS NULL
            ORDER BY items.itemID ASC
            """
        )
        out: list[ZoteroItem] = []
        for row in cur.fetchall():
            lib_id = row["libraryID"]
            key = str(row["key"])
            # v1 scope: personal library only; skip group libraries entirely.
            if lib_id is not None and int(lib_id) in group_map:
                continue

            zotero_uri = f"zotero://select/library/items/{key}"

            zotero_web_uri = None
            if username:
                zotero_web_uri = f"https://zotero.org/{username}/items/{key}"

            notero_url = self.find_notero_page_link_for_parent(int(row["itemID"]))
            out.append(
                ZoteroItem(
                    item_id=int(row["itemID"]),
                    key=key,
                    library_id=int(lib_id) if lib_id is not None else None,
                    title=row["title"],
                    doi=row["doi"],
                    zotero_uri=zotero_uri,
                    zotero_web_uri=zotero_web_uri,
                    notero_page_url=notero_url,
                )
            )
        return out

    def list_child_attachments(self, parent_item_id: int, parent_key: str) -> list[ZoteroAttachment]:
        cur = self._execute_readonly(
            """
            WITH f_title AS (
                SELECT itemData.itemID, itemDataValues.value AS title
                FROM itemData
                JOIN fields ON fields.fieldID = itemData.fieldID
                JOIN itemDataValues ON itemDataValues.valueID = itemData.valueID
                WHERE fields.fieldName = 'title'
            )
            SELECT ia.parentItemID, child.key AS attachmentKey, ia.path, ia.contentType, f_title.title
            FROM itemAttachments ia
            JOIN items child ON child.itemID = ia.itemID
            LEFT JOIN f_title ON f_title.itemID = child.itemID
            LEFT JOIN deletedItems di ON di.itemID = child.itemID
            WHERE ia.parentItemID = ?
              AND di.itemID IS NULL
            ORDER BY child.itemID ASC
            """,
            (parent_item_id,),
        )
        out: list[ZoteroAttachment] = []
        for row in cur.fetchall():
            out.append(
                ZoteroAttachment(
                    parent_item_id=int(row["parentItemID"]),
                    parent_key=parent_key,
                    attachment_key=str(row["attachmentKey"]),
                    path_raw=row["path"],
                    content_type=row["contentType"],
                    title=row["title"],
                )
            )
        return out

    def find_notero_page_link_for_parent(self, parent_item_id: int) -> str | None:
        for att in self.list_child_attachments(parent_item_id, parent_key=""):
            raw = (att.path_raw or "").strip()
            if not raw:
                continue
            lowered = raw.lower()
            if "notion.so" in lowered and lowered.startswith("http"):
                return raw
        return None

    def resolve_attachment_path(self, att: ZoteroAttachment) -> Path | None:
        raw = (att.path_raw or "").strip()
        if not raw:
            return None

        if raw.startswith("storage:"):
            suffix = raw.split(":", 1)[1].lstrip("/")
            return (self._storage_dir / att.attachment_key / suffix).resolve()

        if raw.startswith("file://"):
            parsed = urllib.parse.urlparse(raw)
            return Path(urllib.parse.unquote(parsed.path)).expanduser().resolve()

        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()

        return (self._data_dir / p).resolve()

    def select_candidate_pdf(self, parent: ZoteroItem) -> tuple[str, CandidatePdf | None, str | None]:
        attachments = self.list_child_attachments(parent.item_id, parent.key)

        valid: list[CandidatePdf] = []
        broken_found = False
        for att in attachments:
            path = self.resolve_attachment_path(att)
            if path is None:
                continue
            if not path.exists() or not path.is_file():
                broken_found = True
                continue

            is_pdf = (att.content_type or "").lower() == "application/pdf" or path.suffix.lower() == ".pdf"
            if not is_pdf:
                continue

            stat = path.stat()
            valid.append(CandidatePdf(absolute_path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns))

        if len(valid) == 0 and broken_found:
            return ("BROKEN_ATTACHMENT_PATH", None, "Attachment path is missing or not readable")
        if len(valid) == 0:
            return ("NO_PDF", None, "No valid PDF attachment found")
        if len(valid) > 1:
            return ("MULTIPLE_PDFS", None, "Multiple valid PDF attachments found")
        return ("OK", valid[0], None)

    def extract_notero_page_id(self, item: ZoteroItem) -> str | None:
        if not item.notero_page_url:
            return None
        return parse_notion_page_id_from_url(item.notero_page_url)

    def all_items(self) -> Iterable[ZoteroItem]:
        return self.list_parent_items()
