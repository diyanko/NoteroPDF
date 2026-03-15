from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ZoteroAttachment:
    parent_item_id: int
    parent_key: str
    attachment_key: str
    path_raw: Optional[str]
    content_type: Optional[str]
    title: Optional[str]


@dataclass(frozen=True)
class ZoteroItem:
    item_id: int
    key: str
    library_id: Optional[int]
    title: Optional[str]
    doi: Optional[str]
    zotero_uri: str
    zotero_web_uri: Optional[str]
    notero_page_url: Optional[str]


@dataclass(frozen=True)
class CandidatePdf:
    absolute_path: str
    size: int
    mtime_ns: int


@dataclass
class SyncRow:
    zotero_item_key: str
    title: Optional[str]
    zotero_uri: Optional[str]
    notion_page_id: Optional[str]
    notion_page_url: Optional[str]
    local_pdf_path: Optional[str]
    action_taken: str
    final_status: str
    error_message: Optional[str]
