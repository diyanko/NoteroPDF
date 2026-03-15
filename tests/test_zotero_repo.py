from pathlib import Path
import sqlite3

import pytest

from noteropdf.zotero_repo import ZoteroRepository


def _create_minimal_zotero_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE groups (libraryID INTEGER, groupID INTEGER);
        CREATE TABLE settings (setting TEXT, key TEXT, value TEXT);
        """
    )
    conn.commit()
    conn.close()


def test_read_only_guarantees_report_expected_flags(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path)
    try:
        guarantees = repo.read_only_guarantees()
    finally:
        repo.close()

    assert guarantees["uri_mode_ro"] is True
    assert guarantees["uri_immutable"] is True
    assert guarantees["query_guard_enabled"] is True


def test_execute_readonly_rejects_write_statements(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path)
    try:
        with pytest.raises(RuntimeError, match="non-readonly"):
            repo._execute_readonly("UPDATE groups SET groupID = 1")
    finally:
        repo.close()
