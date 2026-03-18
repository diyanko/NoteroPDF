import sys
from unittest.mock import MagicMock

import pytest

from noteropdf.util import (normalize_notion_target_inputs,
                            parse_notion_page_id_from_url, zotero_maybe_open)


def test_parse_canonical_uuid_from_url():
    url = "https://www.notion.so/workspace/Page-12345678-1234-1234-1234-1234567890ab"
    assert parse_notion_page_id_from_url(url) == "12345678-1234-1234-1234-1234567890ab"


def test_parse_compact_notion_id_from_url():
    url = "https://www.notion.so/workspace/Page-123456781234123412341234567890ab"
    assert parse_notion_page_id_from_url(url) == "12345678-1234-1234-1234-1234567890ab"


def test_parse_invalid_url_returns_none():
    assert parse_notion_page_id_from_url("https://example.com/no-id-here") is None


def test_normalize_notion_target_inputs_moves_collection_url_to_data_source():
    database_id, data_source_id = normalize_notion_target_inputs(
        "collection://12345678-1234-1234-1234-1234567890ab", ""
    )

    assert database_id == ""
    assert data_source_id == "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def mock_subprocess(monkeypatch):
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)
    return mock_run


def test_zotero_maybe_open_on_windows_zotero_running(mock_subprocess, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mock_subprocess.return_value = MagicMock(
        stdout="Zotero.exe Console 12345 Running\n",
        returncode=0,
    )
    assert zotero_maybe_open() is True
    mock_subprocess.assert_called_once()


def test_zotero_maybe_open_on_windows_zotero_not_running(mock_subprocess, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mock_subprocess.return_value = MagicMock(
        stdout="INFO: No tasks are running which match the specified command line.\n",
        returncode=0,
    )
    assert zotero_maybe_open() is False
    mock_subprocess.assert_called_once()


def test_zotero_maybe_open_on_non_windows_zotero_running(mock_subprocess, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    mock_subprocess.return_value = MagicMock(
        stdout="12345\n",
        returncode=0,
    )
    assert zotero_maybe_open() is True
    mock_subprocess.assert_called_once()


def test_zotero_maybe_open_on_non_windows_zotero_not_running(
    mock_subprocess, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "darwin")
    mock_subprocess.return_value = MagicMock(
        stdout="",
        returncode=1,
    )
    assert zotero_maybe_open() is False
    mock_subprocess.assert_called_once()


def test_zotero_maybe_open_on_exception_returns_false(mock_subprocess, monkeypatch):
    mock_subprocess.side_effect = Exception("Process error")
    assert zotero_maybe_open() is False
