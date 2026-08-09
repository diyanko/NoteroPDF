from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoteroAttachment:
    parent_item_id: int
    parent_key: str
    attachment_key: str
    path_raw: str | None
    content_type: str | None
    title: str | None


@dataclass(frozen=True)
class ZoteroItem:
    item_id: int
    key: str
    title: str | None
    zotero_uri: str
    notero_page_url: str | None


@dataclass(frozen=True)
class CandidatePdf:
    absolute_path: str
    size: int
    mtime_ns: int


@dataclass
class SyncRow:
    zotero_item_key: str
    title: str | None
    zotero_uri: str | None
    notion_page_id: str | None
    notion_page_url: str | None
    local_pdf_path: str | None
    action_taken: str
    final_status: str
    error_message: str | None
