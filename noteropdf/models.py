from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoteroLibrary:
    group_id: int | None = None
    name: str = "My Library"

    def __post_init__(self) -> None:
        if self.group_id is not None and (
            type(self.group_id) is not int or self.group_id <= 0
        ):
            raise ValueError("Zotero group ID must be a positive integer.")

    @property
    def api_path(self) -> str:
        return f"/groups/{self.group_id}" if self.group_id is not None else "/users/0"

    @property
    def select_path(self) -> str:
        return f"groups/{self.group_id}" if self.group_id is not None else "library"


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
    library: ZoteroLibrary = ZoteroLibrary()

    @property
    def sync_key(self) -> str:
        # Keep existing personal-library history; group item keys are only unique
        # within their own library and must never inherit another library's state.
        if self.library.group_id is None:
            return self.key
        return f"groups/{self.library.group_id}/{self.key}"


@dataclass(frozen=True)
class PdfSelection:
    attachment_key: str
    path_raw: str


@dataclass(frozen=True)
class CandidatePdf:
    absolute_path: str
    size: int
    mtime_ns: int
    attachment_key: str = ""


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
