import sqlite3
from pathlib import Path

import pytest

from noteropdf.models import ZoteroAttachment
from noteropdf.zotero_repo import ZoteroRepository


def _expected_windows_drive_path(path_without_drive: str) -> Path:
    path_with_drive = f"C:/{path_without_drive}"
    # On Unix hosts this is a relative path; anchor it so expectations are stable.
    if Path(path_with_drive).is_absolute():
        return Path(path_with_drive).resolve()
    return Path(f"/{path_with_drive}").resolve()


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

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
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

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        with pytest.raises(RuntimeError, match="non-readonly"):
            repo._execute_readonly("UPDATE groups SET groupID = 1")
    finally:
        repo.close()


def test_resolve_attachment_path_storage_prefix(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=storage_dir, data_dir=tmp_path
    )
    try:
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="storage:document.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = (storage_dir / "abc123" / "document.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_triple_slash(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        # Test file:///C:/... format (triple slash, Windows drive URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file:///C:/Users/test/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = _expected_windows_drive_path("Users/test/Documents/file.pdf")
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_double_slash(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        # Test file://C:/... format (double slash, Windows drive in netloc)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file://C:/Users/test/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = _expected_windows_drive_path("Users/test/Documents/file.pdf")
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_unc(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        # Test UNC-style file://server/share/... format
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file://server/share/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = Path("//server/share/Documents/file.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_absolute_path(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        absolute_raw = "/Users/test/Documents/file.pdf"
        if not Path(absolute_raw).is_absolute():
            absolute_raw = "C:/Users/test/Documents/file.pdf"

        # Test absolute path (not a URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw=absolute_raw,
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        assert result == Path(absolute_raw).resolve()
    finally:
        repo.close()


def test_resolve_attachment_path_relative_path(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=data_dir
    )
    try:
        # Test relative path (not a URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="subdir/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = (data_dir / "subdir" / "file.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_empty_path(tmp_path: Path):
    db_path = tmp_path / "zotero.sqlite"
    _create_minimal_zotero_db(db_path)

    repo = ZoteroRepository(
        sqlite_path=db_path, storage_dir=tmp_path, data_dir=tmp_path
    )
    try:
        # Test empty path
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        assert result is None
    finally:
        repo.close()
