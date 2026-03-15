from types import SimpleNamespace
from typing import Any

from noteropdf.notion_client import NotionApiError
from noteropdf.sync_engine import MatchResult, SyncEngine
from noteropdf.status import Status
from noteropdf.models import CandidatePdf, ZoteroItem


class _FakeState:
    def __init__(self, page_ids):
        self._page_ids = page_ids
        self.cleared = False

    def list_known_page_ids(self):
        return list(self._page_ids)

    def clear_all(self):
        self.cleared = True


class _FakeNotion:
    def __init__(self, failing_page_id=None):
        self.failing_page_id = failing_page_id

    def clear_page_files(self, page_id, property_name):
        if page_id == self.failing_page_id:
            raise NotionApiError("NOTION_RATE_LIMIT", "rate limited", 429)


def _make_engine(page_ids, *, failing_page_id=None, dry_run=False):
    engine = SyncEngine.__new__(SyncEngine)
    fake_state = _FakeState(page_ids)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(dry_run=dry_run),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    casted.state = fake_state
    casted.notion = _FakeNotion(failing_page_id=failing_page_id)
    casted._logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    return engine, fake_state


def test_full_reset_clears_state_when_all_page_clears_succeed():
    engine, fake_state = _make_engine(["page-1", "page-2"])

    rows = engine.full_reset()

    assert len(rows) == 2
    assert all(r.final_status == Status.OK.value for r in rows)
    assert fake_state.cleared is True


def test_full_reset_keeps_state_when_any_page_clear_fails():
    engine, fake_state = _make_engine(["page-1", "page-2"], failing_page_id="page-2")

    rows = engine.full_reset()

    assert len(rows) == 2
    assert rows[1].final_status == Status.NOTION_RATE_LIMIT.value
    assert fake_state.cleared is False


def test_sync_one_reports_state_save_failure_and_message(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    item = ZoteroItem(
        item_id=1,
        key="ABC123",
        library_id=1,
        title="Paper",
        doi=None,
        zotero_uri="zotero://select/library/items/ABC123",
        zotero_web_uri=None,
        notero_page_url=None,
    )
    pdf = CandidatePdf(absolute_path=str(pdf_path), size=pdf_path.stat().st_size, mtime_ns=pdf_path.stat().st_mtime_ns)

    class _State:
        def get(self, _):
            return None

        def upsert(self, _):
            raise RuntimeError("disk error")

    class _Notion:
        def page_files_count(self, *_):
            return 0

        def create_file_upload(self, **_):
            return {"id": "upload-1"}

        def send_file_bytes(self, *_):
            return "upload-1"

        def attach_file_upload_to_page(self, **_):
            return None

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(
            max_supported_mb=20,
            max_simple_upload_mb=20,
            dry_run=False,
            continue_on_error=False,
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    casted.state = _State()
    casted.notion = _Notion()
    casted.zotero = SimpleNamespace(select_candidate_pdf=lambda _item: (Status.OK.value, pdf, None))
    casted._logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
    casted._resolve_match = lambda _item: MatchResult(Status.OK, "page-1", "https://notion.so/page-1", None)
    casted.data_source_id = "ds"

    row = engine._sync_one(item, force=False)
    assert row.final_status == Status.STATE_SAVE_FAILED.value
    assert row.error_message is not None
    assert "may re-upload" in row.error_message
