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

    assert not lock_path.exists()


def test_state_store_can_open_without_lock(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"

    first = StateStore(db_path, acquire_lock=False)
    second = StateStore(db_path, acquire_lock=False)
    try:
        assert not Path(f"{db_path}.lock").exists()
    finally:
        first.close()
        second.close()
