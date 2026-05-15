from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noteropdf.notion_client import NotionApiError, NotionClient
from noteropdf.state_store import StateRecord
from noteropdf.status import Status
from noteropdf.sync_engine import MatchResult, SyncEngine
from noteropdf.models import CandidatePdf, SyncRow, ZoteroItem
from noteropdf.util import parse_notion_page_id_from_url


def _make_item(
    *,
    key: str = "ABC123",
    zotero_web_uri: str | None = None,
    notero_page_url: str | None = None,
) -> ZoteroItem:
    return ZoteroItem(
        item_id=1,
        key=key,
        library_id=1,
        title="Paper",
        doi=None,
        zotero_uri=f"zotero://select/library/items/{key}",
        zotero_web_uri=zotero_web_uri,
        notero_page_url=notero_page_url,
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
        debug=lambda *args, **kwargs: None,
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


def _make_cleanup_engine(
    *,
    items: list[ZoteroItem],
    pages: list[dict[str, Any]],
    has_uri_property: bool = True,
    trash_error: NotionApiError | None = None,
):
    class _Notion:
        def __init__(self):
            self.trash_calls: list[str] = []

        def resolve_target_ids(self, **_):
            return "", "ds-1"

        def has_property(self, *_):
            return has_uri_property

        def list_data_source_pages(self, *_args, **_kwargs):
            return list(pages)

        def get_page_property_text(self, page, property_name):
            assert property_name == "Zotero URI"
            props = page.get("properties") or {}
            prop = props.get(property_name) or {}
            if prop.get("type") == "url":
                return prop.get("url")
            if prop.get("type") == "rich_text":
                rich_text = prop.get("rich_text") or []
                return rich_text[0].get("plain_text") if rich_text else None
            return None

        def get_page_title_text(self, page):
            props = page.get("properties") or {}
            title_prop = props.get("Name") or {}
            title = title_prop.get("title") or []
            return title[0].get("plain_text") if title else None

        def trash_page(self, page_id: str):
            self.trash_calls.append(page_id)
            if trash_error is not None:
                raise trash_error

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        notion=SimpleNamespace(
            database_id="",
            data_source_id="ds-1",
            zotero_uri_property_name="Zotero URI",
        )
    )
    casted.notion = _Notion()
    casted.zotero = SimpleNamespace(
        all_items=lambda: list(items),
        extract_notero_page_id=lambda item: (
            parse_notion_page_id_from_url(item.notero_page_url)
            if item.notero_page_url
            else None
        ),
    )
    casted.state = None
    casted._logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    casted.data_source_id = None
    return engine, casted.notion


def test_sync_progress_callback_reports_start_periodic_and_finished():
    engine = SyncEngine.__new__(SyncEngine)
    items = [_make_item(key=f"KEY{i}") for i in range(25)]
    progress: list[tuple[int, int]] = []

    casted: Any = engine
    casted.cfg = SimpleNamespace(notion=SimpleNamespace(pdf_property_name="PDF"))
    casted.notion = SimpleNamespace(validate_pdf_property=lambda *_: None)
    casted.zotero = SimpleNamespace(all_items=lambda: list(items))
    casted._resolve_data_source = lambda: "ds"
    casted._logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    def _fake_sync_one(item, *, force=False):
        return SyncRow(
            zotero_item_key=item.key,
            title=item.title,
            zotero_uri=item.zotero_uri,
            notion_page_id="page-1",
            notion_page_url="https://notion.so/page-1",
            local_pdf_path="/tmp/a.pdf",
            action_taken="quick_fingerprint_match",
            final_status=Status.UNCHANGED.value,
            error_message=None,
        )

    casted._sync_one = _fake_sync_one

    rows = engine.sync(progress_callback=lambda done, total: progress.append((done, total)))

    assert len(rows) == 25
    assert progress == [
        (0, 25),
        (3, 25),
        (6, 25),
        (9, 25),
        (12, 25),
        (15, 25),
        (18, 25),
        (21, 25),
        (24, 25),
        (25, 25),
    ]


def _page(
    page_id: str,
    *,
    zotero_uri: str | None = None,
    title: str = "Paper",
    in_trash: bool = False,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "Name": {"type": "title", "title": [{"plain_text": title}]}
    }
    if zotero_uri is not None:
        properties["Zotero URI"] = {"type": "rich_text", "rich_text": [{"plain_text": zotero_uri}]}
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "in_trash": in_trash,
        "properties": properties,
    }


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
        debug=lambda *args, **kwargs: None,
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
        debug=lambda *args, **kwargs: None,
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


def test_cleanup_preview_marks_missing_zotero_rows_stale_without_trashing():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="zotero://select/library/items/DEAD999")],
    )

    rows = engine.cleanup_deleted_pages(apply=False)

    assert rows[0].final_status == Status.STALE_NOTION_ROW.value
    assert rows[0].action_taken == "dry_run_trash:missing_from_zotero_library"
    assert notion.trash_calls == []


def test_cleanup_apply_trashes_missing_zotero_rows():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="zotero://select/library/items/DEAD999")],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.STALE_NOTION_ROW.value
    assert rows[0].action_taken == "trash:missing_from_zotero_library"
    assert notion.trash_calls == ["page-1"]


def test_cleanup_trashes_duplicate_when_canonical_page_exists():
    canonical_page_id = "11111111-1111-1111-1111-111111111111"
    duplicate_page_id = "22222222-2222-2222-2222-222222222222"
    item = _make_item(notero_page_url=f"https://www.notion.so/{canonical_page_id}")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[
            _page(canonical_page_id, zotero_uri=item.zotero_uri),
            _page(duplicate_page_id, zotero_uri=item.zotero_uri),
        ],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    by_page = {row.notion_page_id: row for row in rows}
    assert by_page[canonical_page_id].final_status == Status.UNCHANGED.value
    assert by_page[duplicate_page_id].final_status == Status.STALE_NOTION_ROW.value
    assert by_page[duplicate_page_id].action_taken == "trash:duplicate_of_canonical_notero_page"
    assert notion.trash_calls == [duplicate_page_id]


def test_cleanup_does_not_trash_live_row_when_canonical_page_is_not_active():
    stale_canonical_page_id = "11111111-1111-1111-1111-111111111111"
    active_page_id = "22222222-2222-2222-2222-222222222222"
    item = _make_item(notero_page_url=f"https://www.notion.so/{stale_canonical_page_id}")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page(active_page_id, zotero_uri=item.zotero_uri)],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.AMBIGUOUS_CLEANUP_MATCH.value
    assert rows[0].action_taken == "skip:stale_canonical_notero_page"
    assert notion.trash_calls == []


def test_cleanup_marks_duplicate_live_rows_without_canonical_page_ambiguous():
    item = _make_item(zotero_web_uri="https://zotero.org/user/items/ABC123")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[
            _page("page-1", zotero_uri=item.zotero_uri),
            _page("page-2", zotero_uri=item.zotero_web_uri),
        ],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert {row.final_status for row in rows} == {Status.AMBIGUOUS_CLEANUP_MATCH.value}
    assert notion.trash_calls == []


def test_cleanup_skips_group_library_uri_as_unmanaged():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="zotero://select/groups/42/items/DEAD999")],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.UNMANAGED_NOTION_ROW.value
    assert rows[0].action_taken == "skip:out_of_scope_zotero_uri"
    assert notion.trash_calls == []


def test_cleanup_skips_other_user_web_uri_as_unmanaged():
    item = _make_item(zotero_web_uri="https://zotero.org/diyanko/items/ABC123")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="https://zotero.org/someone/items/DEAD999")],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.UNMANAGED_NOTION_ROW.value
    assert rows[0].action_taken == "skip:out_of_scope_zotero_uri"
    assert notion.trash_calls == []


def test_cleanup_matches_web_uris_case_insensitively():
    item = _make_item(zotero_web_uri="https://zotero.org/Diyanko/items/ABC123")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="https://zotero.org/diyanko/items/ABC123")],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.UNCHANGED.value
    assert rows[0].action_taken == "keep:live_zotero_match"
    assert notion.trash_calls == []


def test_cleanup_keeps_canonical_row_even_without_zotero_uri():
    page_id = "33333333-3333-3333-3333-333333333333"
    item = _make_item(notero_page_url=f"https://www.notion.so/{page_id}")
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page(page_id, zotero_uri=None)],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.UNCHANGED.value
    assert rows[0].action_taken == "keep:canonical_notero_page"
    assert notion.trash_calls == []


def test_cleanup_marks_missing_uri_row_unmanaged_when_not_canonical():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri=None)],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.UNMANAGED_NOTION_ROW.value
    assert rows[0].action_taken == "skip:unmanaged_missing_zotero_uri"
    assert notion.trash_calls == []


def test_cleanup_ignores_trashed_pages_returned_by_client():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="zotero://select/library/items/DEAD999", in_trash=True)],
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows == []
    assert notion.trash_calls == []


def test_cleanup_requires_zotero_uri_property():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri=item.zotero_uri)],
        has_uri_property=False,
    )

    try:
        engine.cleanup_deleted_pages(apply=True)
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_SCHEMA_ERROR"
    assert notion.trash_calls == []


def test_cleanup_maps_trash_errors_to_status_code():
    item = _make_item()
    engine, notion = _make_cleanup_engine(
        items=[item],
        pages=[_page("page-1", zotero_uri="zotero://select/library/items/DEAD999")],
        trash_error=NotionApiError("NOTION_RATE_LIMIT", "rate limited", 429),
    )

    rows = engine.cleanup_deleted_pages(apply=True)

    assert rows[0].final_status == Status.NOTION_RATE_LIMIT.value
    assert rows[0].action_taken == "trash:missing_from_zotero_library"
    assert notion.trash_calls == ["page-1"]
