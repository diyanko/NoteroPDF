from pathlib import Path

import pytest

from noteropdf.state_store import FileLock, StateStore


def test_state_store_lock_prevents_second_instance(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"

    first = StateStore(db_path)
    try:
        with pytest.raises(RuntimeError, match="Another NoteroPDF run"):
            StateStore(db_path)
    finally:
        first.close()

    second = StateStore(db_path)
    second.close()


def test_file_lock_prevents_second_instance(tmp_path: Path):
    lock_path = tmp_path / "run.lock"

    first = FileLock(lock_path)
    try:
        assert lock_path.exists()
        with pytest.raises(RuntimeError, match="Another NoteroPDF run"):
            FileLock(lock_path)
    finally:
        first.close()

    # A released advisory lock is immediately reusable whether the platform's
    # backend keeps or removes its lock file.
    second = FileLock(lock_path)
    second.close()


def test_state_store_can_open_without_lock(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"

    first = StateStore(db_path, acquire_lock=False)
    second = StateStore(db_path, acquire_lock=False)
    try:
        assert not Path(f"{db_path}.lock").exists()
    finally:
        first.close()
        second.close()


def test_state_store_persists_and_deletes_settings(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"
    store = StateStore(db_path, acquire_lock=False)
    store.update_settings({"notion_data_source_id": "source-1"})
    store.update_settings(
        {"pdf_property_id": "pdf-id", "notion_data_source_id": "source-2"}
    )

    assert store.get_settings() == {
        "notion_data_source_id": "source-2",
        "pdf_property_id": "pdf-id",
    }

    store.update_settings({"pdf_property_id": None})
    assert store.get_settings() == {"notion_data_source_id": "source-2"}
    store.close()


def test_state_store_rejects_empty_setting_key(tmp_path: Path):
    store = StateStore(tmp_path / "state.sqlite3", acquire_lock=False)
    try:
        with pytest.raises(ValueError, match="cannot be empty"):
            store.update_settings({"": "value"})
    finally:
        store.close()
