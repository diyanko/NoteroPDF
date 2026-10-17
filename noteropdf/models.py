from __future__ import annotations

import re
from dataclasses import dataclass


def validate_zotero_key(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9]{8}", value):
        raise ValueError("Zotero keys must be exactly 8 uppercase letters or digits.")
    return value


@dataclass(frozen=True)
class SyncScope:
    """An explicit per-run selection within one complete library snapshot."""

    collection_keys: tuple[str, ...] = ()
    item_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for key in (*self.collection_keys, *self.item_keys):
            validate_zotero_key(key)

    def select(self, items: list[ZoteroItem]) -> list[ZoteroItem]:
        requested_items = set(self.item_keys)
        requested_collections = set(self.collection_keys)
        available_items = {item.key for item in items}
        if requested_items - available_items:
            raise ValueError(
                "A selected item was not found among the selected library's parent items. "
                "Check the item key and library."
            )
        available_collections = {key for item in items for key in item.collection_keys}
        if requested_collections - available_collections:
            raise ValueError(
                "A selected collection has no readable parent items in this library. "
                "Check the collection key and library."
            )
        return [
            item
            for item in items
            if (not requested_items or item.key in requested_items)
            and (
                not requested_collections
                or not requested_collections.isdisjoint(item.collection_keys)
            )
        ]


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
    collection_keys: tuple[str, ...] = ()

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


@dataclass(frozen=True)
class SourceFile:
    attachment_key: str
    absolute_path: str
    content_type: str
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
