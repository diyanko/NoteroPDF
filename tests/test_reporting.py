from datetime import datetime as real_datetime
from datetime import timezone
from pathlib import Path

from noteropdf.models import SyncRow
from noteropdf.reporting import write_reports


def test_report_paths_do_not_overwrite_same_second_reports(monkeypatch, tmp_path: Path):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 5, 15, 12, 30, 0, tzinfo=timezone.utc)

    row = SyncRow(
        zotero_item_key="A",
        title="Paper",
        zotero_uri="zotero://select/library/items/A",
        notion_page_id="page-1",
        notion_page_url="https://notion.so/page-1",
        local_pdf_path="/tmp/a.pdf",
        action_taken="upload_attach:first_sync",
        final_status="OK",
        error_message=None,
    )

    monkeypatch.setattr("noteropdf.reporting.datetime", FrozenDateTime)

    first = write_reports(tmp_path, "sync", [row])
    second = write_reports(tmp_path, "sync", [row])

    assert first != second
    assert all(path.exists() for path in first)
    assert all(path.exists() for path in second)
    assert second[0].name.endswith("-2.json")
