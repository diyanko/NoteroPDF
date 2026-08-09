from pathlib import Path

import pytest

from noteropdf.settings import LocalSettings, load_settings, save_settings
from noteropdf.state_store import StateStore


def test_settings_round_trip_and_remove_unset_values(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite3", acquire_lock=False)
    try:
        save_settings(
            store,
            LocalSettings(
                zotero_data_dir=tmp_path / "Zotero",
                notion_data_source_id="source-id",
                pdf_property_id="pdf-id",
            ),
        )
        loaded = load_settings(store)
        assert loaded.zotero_data_dir == (tmp_path / "Zotero").resolve()
        assert loaded.notion_data_source_id == "source-id"
        assert loaded.pdf_property_id == "pdf-id"

        save_settings(store, LocalSettings(notion_data_source_id="new-source"))
        replaced = load_settings(store)
        assert replaced.notion_data_source_id == "new-source"
        assert replaced.pdf_property_id is None
        assert replaced.zotero_data_dir is None
    finally:
        store.close()


def test_settings_reject_unsupported_schema(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite3", acquire_lock=False)
    try:
        store.update_settings({"settings_schema_version": "999"})
        with pytest.raises(ValueError, match="not supported"):
            load_settings(store)
    finally:
        store.close()
