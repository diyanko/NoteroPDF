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


def test_group_selection_round_trip_and_switch_to_personal(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3", acquire_lock=False)
    try:
        assert load_settings(store).zotero_group_id is None
        save_settings(store, LocalSettings(zotero_group_id=123))
        assert load_settings(store).zotero_group_id == 123
        save_settings(store, LocalSettings())
        assert load_settings(store).zotero_group_id is None
        assert "zotero_group_id" not in store.get_settings()
    finally:
        store.close()


@pytest.mark.parametrize("raw", ["", "0", "-1", "group", "1.5"])
def test_invalid_saved_group_never_defaults_to_personal(tmp_path, raw):
    store = StateStore(tmp_path / "state.sqlite3", acquire_lock=False)
    try:
        store.update_settings({"zotero_group_id": raw})
        with pytest.raises(ValueError):
            load_settings(store)
    finally:
        store.close()


def test_original_settings_migrate_without_losing_selected_values(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.update_settings(
            {
                "settings_schema_version": "1",
                "notion_data_source_id": "source",
                "pdf_property_id": "pdf",
            }
        )
        loaded = load_settings(store)
        assert loaded.schema_version == 2
        assert loaded.notion_data_source_id == "source"
        assert loaded.notion_credential_id is None
        save_settings(store, loaded)
        assert store.get_settings()["settings_schema_version"] == "2"
    finally:
        store.close()


@pytest.mark.parametrize(
    "reference", ["", "../other", "A" * 32, "a" * 31, "saved-token"]
)
def test_invalid_credential_reference_is_rejected(reference):
    with pytest.raises(ValueError, match="credential reference"):
        LocalSettings(notion_credential_id=reference)
