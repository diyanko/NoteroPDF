from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import ZoteroLibrary
from .profiles import validate_credential_id
from .state_store import StateStore

SETTINGS_SCHEMA_VERSION = 2

_SCHEMA_VERSION = "settings_schema_version"
_ZOTERO_DATA_DIR = "zotero_data_dir"
_ZOTERO_GROUP_ID = "zotero_group_id"
_NOTION_DATA_SOURCE_ID = "notion_data_source_id"
_PDF_PROPERTY_ID = "pdf_property_id"
_NOTION_CREDENTIAL_ID = "notion_credential_id"

_MANAGED_KEYS = (
    _SCHEMA_VERSION,
    _ZOTERO_DATA_DIR,
    _ZOTERO_GROUP_ID,
    _NOTION_DATA_SOURCE_ID,
    _PDF_PROPERTY_ID,
    _NOTION_CREDENTIAL_ID,
)


@dataclass(frozen=True)
class LocalSettings:
    """Non-secret settings resolved by the guided setup flow."""

    schema_version: int = SETTINGS_SCHEMA_VERSION
    zotero_data_dir: Path | None = None
    notion_data_source_id: str | None = None
    pdf_property_id: str | None = None
    zotero_group_id: int | None = None
    notion_credential_id: str | None = None

    def __post_init__(self) -> None:
        ZoteroLibrary(self.zotero_group_id)
        if self.notion_credential_id is not None:
            validate_credential_id(self.notion_credential_id)


def load_settings(store: StateStore) -> LocalSettings:
    values = store.get_settings()
    raw_version = values.get(_SCHEMA_VERSION)
    try:
        schema_version = (
            int(raw_version) if raw_version is not None else SETTINGS_SCHEMA_VERSION
        )
    except ValueError as exc:
        raise ValueError("Stored settings schema version is invalid.") from exc
    if schema_version not in {1, SETTINGS_SCHEMA_VERSION}:
        raise ValueError(
            "The stored settings format is not supported by this NoteroPDF version. "
            "Run `noteropdf connect` to create fresh settings."
        )

    raw_zotero_dir = values.get(_ZOTERO_DATA_DIR)
    raw_group_id = values.get(_ZOTERO_GROUP_ID)
    try:
        group_id = int(raw_group_id) if raw_group_id is not None else None
    except ValueError as exc:
        raise ValueError("Stored Zotero group ID is invalid.") from exc
    return LocalSettings(
        schema_version=SETTINGS_SCHEMA_VERSION,
        zotero_data_dir=Path(raw_zotero_dir) if raw_zotero_dir else None,
        notion_data_source_id=values.get(_NOTION_DATA_SOURCE_ID),
        pdf_property_id=values.get(_PDF_PROPERTY_ID),
        zotero_group_id=group_id,
        notion_credential_id=values.get(_NOTION_CREDENTIAL_ID),
    )


def save_settings(store: StateStore, settings: LocalSettings) -> None:
    if settings.schema_version != SETTINGS_SCHEMA_VERSION:
        raise ValueError(
            f"Cannot save settings schema version {settings.schema_version}; "
            f"this version supports {SETTINGS_SCHEMA_VERSION}."
        )

    values: dict[str, str | None] = {
        _SCHEMA_VERSION: str(settings.schema_version),
        _ZOTERO_DATA_DIR: (
            str(settings.zotero_data_dir.expanduser().resolve())
            if settings.zotero_data_dir is not None
            else None
        ),
        _NOTION_DATA_SOURCE_ID: settings.notion_data_source_id,
        _PDF_PROPERTY_ID: settings.pdf_property_id,
        _NOTION_CREDENTIAL_ID: settings.notion_credential_id,
        _ZOTERO_GROUP_ID: (
            str(settings.zotero_group_id)
            if settings.zotero_group_id is not None
            else None
        ),
    }
    store.update_settings({key: values[key] for key in _MANAGED_KEYS})
