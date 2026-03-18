from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noteropdf.notion_client import NotionApiError, NotionClient
from noteropdf.state_store import StateRecord
from noteropdf.status import Status
from noteropdf.sync_engine import MatchResult, SyncEngine
from noteropdf.models import CandidatePdf, ZoteroItem


def _make_item() -> ZoteroItem:
    return ZoteroItem(
        item_id=1,
        key="ABC123",
        library_id=1,
        title="Paper",
        doi=None,
        zotero_uri="zotero://select/library/items/ABC123",
        zotero_web_uri=None,
        notero_page_url=None,
    )


def _make_pdf(tmp_path: Path, name: str = "sample.pdf", size_bytes: int = 9) -> CandidatePdf:
    pdf_path = tmp_path / name
    pdf_path.write_bytes(b"a" * size_bytes)
    stat = pdf_path.stat()
    return CandidatePdf(
        absolute_path=str(pdf_path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _make_engine(
    tmp_path: Path,
    *,
    pdf: CandidatePdf,
    remote_files: list[dict[str, Any]] | None = None,
    state_record: StateRecord | None = None,
    dry_run: bool = False,
    workspace_limit: int | None = None,
    workspace_limit_error: NotionApiError | None = None,
    create_error: NotionApiError | None = None,
    attach_error: NotionApiError | None = None,
):
    class _State:
        def get(self, _):
            return state_record

        def upsert(self, _):
            return None

    class _Notion:
        def __init__(self):
            self.create_calls = 0
            self.attach_calls = 0

        def get_page_files(self, *_):
            return list(remote_files or [])

        def get_workspace_upload_limit_bytes(self):
            if workspace_limit_error is not None:
                raise workspace_limit_error
            return workspace_limit

        @staticmethod
        def normalize_attachment_filename(filename: str) -> str:
            return NotionClient.normalize_attachment_filename(filename)

        def create_file_upload(self, **_):
            self.create_calls += 1
            if create_error is not None:
                raise create_error
            return {"id": "upload-1"}

        def send_file_bytes(self, *_):
            if create_error is not None:
                raise create_error
            return "upload-1"

        def attach_file_upload_to_page(self, **_):
            self.attach_calls += 1
            if attach_error is not None:
                raise attach_error
            return None

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(dry_run=dry_run, log_level="INFO", report_dir=tmp_path),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    casted.state = _State()
    casted.notion = _Notion()
    casted.zotero = SimpleNamespace(
        select_candidate_pdf=lambda _item: (Status.OK.value, pdf, None)
    )
    casted._logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    casted._get_cached_hash = lambda _path: "dummy-hash"
    casted._resolve_match = lambda _item: MatchResult(
        Status.OK, "page-1", "https://notion.so/page-1", None
    )
    casted.data_source_id = "ds"
    return engine, casted.notion


def test_sync_one_reports_state_save_failure_and_message(tmp_path: Path):
    pdf = _make_pdf(tmp_path)

    class _State:
        def get(self, _):
            return None

        def upsert(self, _):
            raise RuntimeError("disk error")

    class _Notion:
        def get_page_files(self, *_):
            return []

        def get_workspace_upload_limit_bytes(self):
            return None

        @staticmethod
        def normalize_attachment_filename(filename: str) -> str:
            return NotionClient.normalize_attachment_filename(filename)

        def create_file_upload(self, **_):
            return {"id": "upload-1"}

        def send_file_bytes(self, *_):
            return "upload-1"

        def attach_file_upload_to_page(self, **_):
            return None

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(dry_run=False, log_level="INFO", report_dir=tmp_path),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    casted.state = _State()
    casted.notion = _Notion()
    casted.zotero = SimpleNamespace(
        select_candidate_pdf=lambda _item: (Status.OK.value, pdf, None)
    )
    casted._logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    casted._get_cached_hash = lambda _path: "dummy-hash"
    casted._resolve_match = lambda _item: MatchResult(
        Status.OK, "page-1", "https://notion.so/page-1", None
    )
    casted.data_source_id = "ds"

    row = engine._sync_one(_make_item())
    assert row.final_status == Status.STATE_SAVE_FAILED.value
    assert row.error_message is not None
    assert "may re-upload" in row.error_message


def test_sync_one_marks_unchanged_when_remote_file_is_healthy(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        zotero_item_key="ABC123",
        notion_page_id="page-1",
        pdf_absolute_path=pdf.absolute_path,
        pdf_size=pdf.size,
        pdf_mtime_ns=pdf.mtime_ns,
        pdf_sha256="dummy-hash",
        last_sync_time="2026-03-18T00:00:00+00:00",
        last_status=Status.OK.value,
        last_error_code=None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name}],
        state_record=record,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.UNCHANGED.value
    assert row.action_taken == "quick_fingerprint_match"
    assert notion.create_calls == 0


def test_sync_one_reuploads_when_remote_file_name_drifted(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        zotero_item_key="ABC123",
        notion_page_id="page-1",
        pdf_absolute_path=pdf.absolute_path,
        pdf_size=pdf.size,
        pdf_mtime_ns=pdf.mtime_ns,
        pdf_sha256="dummy-hash",
        last_sync_time="2026-03-18T00:00:00+00:00",
        last_status=Status.OK.value,
        last_error_code=None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": "wrong.pdf"}],
        state_record=record,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    assert row.action_taken == "upload_attach:remote_drift_name_mismatch"
    assert notion.create_calls == 1
    assert notion.attach_calls == 1


def test_sync_one_force_reuploads_even_when_remote_file_name_matches(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        zotero_item_key="ABC123",
        notion_page_id="page-1",
        pdf_absolute_path=pdf.absolute_path,
        pdf_size=pdf.size,
        pdf_mtime_ns=pdf.mtime_ns,
        pdf_sha256="dummy-hash",
        last_sync_time="2026-03-18T00:00:00+00:00",
        last_status=Status.OK.value,
        last_error_code=None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name}],
        state_record=record,
    )

    row = engine._sync_one(_make_item(), force=True)

    assert row.final_status == Status.OK.value
    assert row.action_taken == "upload_attach:forced"
    assert notion.create_calls == 1
    assert notion.attach_calls == 1


def test_sync_one_keeps_long_filename_items_unchanged_when_remote_name_is_truncated(
    tmp_path: Path,
):
    long_name = ("a" * 120) + ".pdf"
    pdf = _make_pdf(tmp_path, name=long_name)
    record = StateRecord(
        zotero_item_key="ABC123",
        notion_page_id="page-1",
        pdf_absolute_path=pdf.absolute_path,
        pdf_size=pdf.size,
        pdf_mtime_ns=pdf.mtime_ns,
        pdf_sha256="dummy-hash",
        last_sync_time="2026-03-18T00:00:00+00:00",
        last_status=Status.OK.value,
        last_error_code=None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[
            {"name": NotionClient.normalize_attachment_filename(Path(pdf.absolute_path).name)}
        ],
        state_record=record,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.UNCHANGED.value
    assert row.action_taken == "quick_fingerprint_match"
    assert notion.create_calls == 0


def test_sync_one_dry_run_reports_missing_remote_pdf(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, _ = _make_engine(tmp_path, pdf=pdf, remote_files=[], dry_run=True)

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    assert row.action_taken == "dry_run_upload:missing_remote_pdf"


def test_sync_one_dry_run_reports_multiple_remote_files_as_drift(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, _ = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": "a.pdf"}, {"name": "b.pdf"}],
        dry_run=True,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    assert row.action_taken == "dry_run_upload:remote_drift_multiple_files"


def test_sync_one_skips_files_above_workspace_limit(tmp_path: Path):
    pdf = _make_pdf(tmp_path, size_bytes=16)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name}],
        workspace_limit=8,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.FILE_TOO_LARGE.value
    assert "supported upload limit" in (row.error_message or "")
    assert notion.create_calls == 0


def test_sync_one_maps_workspace_limit_lookup_failures(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[],
        workspace_limit_error=NotionApiError("NOTION_RATE_LIMIT", "rate limited", 429),
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.NOTION_RATE_LIMIT.value
    assert row.action_taken == "error"
    assert notion.create_calls == 0


def test_sync_one_maps_upload_failures(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[],
        create_error=NotionApiError("NOTION_RATE_LIMIT", "rate limited", 429),
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.NOTION_RATE_LIMIT.value
    assert row.action_taken == "upload"
    assert notion.attach_calls == 0


def test_sync_one_maps_attach_failures(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[],
        attach_error=NotionApiError("NOTION_AUTH_ERROR", "denied", 403),
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.ATTACH_FAILED.value
    assert row.action_taken == "attach"
    assert notion.create_calls == 1
    assert notion.attach_calls == 1


def test_doctor_reports_workspace_limit_and_group_libraries(tmp_path: Path):
    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        zotero=SimpleNamespace(
            data_dir=tmp_path / "zotero",
            sqlite_path=tmp_path / "zotero" / "zotero.sqlite",
            storage_dir=tmp_path / "zotero" / "storage",
        ),
        sync=SimpleNamespace(
            state_db_path=tmp_path / "state" / "sync-state.sqlite3",
            report_dir=tmp_path / "reports",
            log_dir=tmp_path / "logs",
        ),
        notion=SimpleNamespace(
            database_id="db-1",
            data_source_id="",
            pdf_property_name="PDF",
            zotero_uri_property_name="Zotero URI",
        ),
        notion_token_source="env",
    )
    casted._logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    casted.data_source_id = None

    casted.cfg.zotero.data_dir.mkdir(parents=True)
    casted.cfg.zotero.sqlite_path.write_bytes(b"sqlite")
    casted.cfg.zotero.storage_dir.mkdir(parents=True)

    casted.zotero = SimpleNamespace(
        list_parent_items=lambda: [1, 2],
        count_group_parent_items=lambda: 3,
        read_only_guarantees=lambda: {
            "immutable_uri": True,
            "readonly_guard": True,
        },
    )
    casted.notion = SimpleNamespace(
        ping=lambda: None,
        get_workspace_upload_limit_bytes=lambda: 123456,
        resolve_target_ids=lambda **_: ("db-1", "ds-1"),
        validate_pdf_property=lambda *_: None,
        has_property=lambda *_: True,
    )

    lines = engine.doctor()

    assert any("Skipped group parent items: 3" in line for line in lines)
    assert any("Notion workspace upload limit: 123456 bytes." in line for line in lines)
    assert any("Notion data source was resolved: ds-1" in line for line in lines)


def test_resolve_match_ignores_trashed_primary_page():
    item = ZoteroItem(
        item_id=1,
        key="ABC123",
        library_id=1,
        title="Paper",
        doi=None,
        zotero_uri="zotero://select/library/items/ABC123",
        zotero_web_uri=None,
        notero_page_url="https://www.notion.so/trashedpage",
    )

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        notion=SimpleNamespace(
            pdf_property_name="PDF",
            zotero_uri_property_name="Zotero URI",
            doi_property_name="DOI",
        )
    )
    casted.data_source_id = "ds"
    casted.zotero = SimpleNamespace(extract_notero_page_id=lambda _item: "page-1")
    casted.notion = SimpleNamespace(
        get_page=lambda _page_id: {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "in_trash": True,
        },
        has_property=lambda *_: False,
    )

    match = engine._resolve_match(item)

    assert match.status == Status.NO_NOTION_MATCH
