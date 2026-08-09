from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noteropdf.models import CandidatePdf, ZoteroItem
from noteropdf.notion_client import (
    NotionApiError,
    NotionClient,
    NotionDataSourceSnapshot,
)
from noteropdf.state_store import StateRecord
from noteropdf.status import Status
from noteropdf.sync_engine import MatchResult, PreviewAction, SyncEngine
from noteropdf.util import sha256_file


def _make_item(
    *,
    key: str = "ABC123",
    notero_page_url: str | None = None,
) -> ZoteroItem:
    return ZoteroItem(
        item_id=1,
        key=key,
        title="Paper",
        zotero_uri=f"zotero://select/library/items/{key}",
        notero_page_url=notero_page_url,
    )


def _make_pdf(
    tmp_path: Path, name: str = "sample.pdf", size_bytes: int = 9
) -> CandidatePdf:
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
    preview: bool = False,
    workspace_limit: int | None = None,
    workspace_limit_error: NotionApiError | None = None,
    create_error: NotionApiError | None = None,
    attach_error: NotionApiError | None = None,
    preserve_missing_remote_identity: bool = False,
):
    normalized_remote_files: list[dict[str, Any]] = []
    for index, raw in enumerate(remote_files or []):
        remote = dict(raw)
        file_type = str(remote.get("type") or "file")
        remote["type"] = file_type
        if file_type == "file" and not isinstance(remote.get("file"), dict):
            remote["file"] = {
                "url": f"https://files.notion.test/remote-{index}?signature=old"
            }
        normalized_remote_files.append(remote)

    page = {
        "id": "page-1",
        "properties": {
            "pdf-id": {
                "id": "pdf-id",
                "type": "files",
                "files": normalized_remote_files,
            }
        },
    }
    effective_state_record = state_record
    signature = NotionClient.files_property_signature(page, "pdf-id")
    if (
        effective_state_record is not None
        and not preserve_missing_remote_identity
        and len(signature) == 1
        and effective_state_record.remote_file_identity is None
    ):
        effective_state_record = replace(
            effective_state_record,
            remote_file_name=signature[0][0],
            remote_file_type=signature[0][1],
            remote_file_identity=signature[0][2],
        )

    class _State:
        def __init__(self):
            self.saved = None

        def get(self, _):
            return effective_state_record

        def upsert(self, record):
            self.saved = record

    class _Notion:
        def __init__(self):
            self.create_calls = 0
            self.attach_calls = 0

        get_page_property = staticmethod(NotionClient.get_page_property)

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

        def get_page(self, _page_id):
            return page

        def attach_file_upload_to_page(self, **kwargs):
            self.attach_calls += 1
            if attach_error is not None:
                raise attach_error
            filename = NotionClient.normalize_attachment_filename(kwargs["filename"])
            return {
                "id": "page-1",
                "properties": {
                    "pdf-id": {
                        "id": "pdf-id",
                        "type": "files",
                        "files": [
                            {
                                "name": filename,
                                "type": "file",
                                "file": {
                                    "url": "https://files.notion.test/upload-1"
                                    "?signature=new"
                                },
                            }
                        ],
                    }
                },
            }

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(),
        notion=SimpleNamespace(pdf_property_id="pdf-id"),
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
    casted._get_cached_hash = lambda path: sha256_file(Path(path))
    casted._apply_writes = not preview
    casted._preview_actions = {}
    casted._approved_actions = None
    casted._match_cache = None
    casted._pdf_property_ref = "pdf-id"
    casted._snapshot = NotionDataSourceSnapshot(pages_by_id={"page1": page})
    casted._resolve_match = lambda _item: MatchResult(
        Status.OK, "page-1", "https://notion.so/page-1", None
    )
    casted.data_source_id = "ds"
    return engine, casted.notion


def test_sync_refuses_apply_without_approved_preview_actions():
    engine = SyncEngine.__new__(SyncEngine)

    try:
        engine.sync(apply=True)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "approved preview" in str(exc)


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
        properties["Zotero URI"] = {
            "type": "rich_text",
            "rich_text": [{"plain_text": zotero_uri}],
        }
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
        get_page_property = staticmethod(NotionClient.get_page_property)

        def get_workspace_upload_limit_bytes(self):
            return None

        @staticmethod
        def normalize_attachment_filename(filename: str) -> str:
            return NotionClient.normalize_attachment_filename(filename)

        def create_file_upload(self, **_):
            return {"id": "upload-1"}

        def send_file_bytes(self, *_):
            return "upload-1"

        def get_page(self, _page_id):
            return page

        def attach_file_upload_to_page(self, **kwargs):
            return {
                "id": "page-1",
                "properties": {
                    "pdf-id": {
                        "id": "pdf-id",
                        "type": "files",
                        "files": [
                            {
                                "name": kwargs["filename"],
                                "type": "file",
                                "file": {"url": "https://files.notion.test/upload-1"},
                            }
                        ],
                    }
                },
            }

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(
        sync=SimpleNamespace(),
        notion=SimpleNamespace(pdf_property_id="pdf-id"),
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
    casted._get_cached_hash = lambda path: sha256_file(Path(path))
    casted._apply_writes = True
    casted._approved_actions = None
    casted._match_cache = None
    casted._pdf_property_ref = "pdf-id"
    page = {
        "id": "page-1",
        "properties": {
            "pdf-id": {
                "id": "pdf-id",
                "type": "files",
                "files": [],
            }
        },
    }
    casted._snapshot = NotionDataSourceSnapshot(pages_by_id={"page1": page})
    casted._resolve_match = lambda _item: MatchResult(
        Status.OK, "page-1", "https://notion.so/page-1", None
    )
    casted.data_source_id = "ds"

    row = engine._sync_one(_make_item())
    assert row.final_status == Status.STATE_SAVE_FAILED.value
    assert row.error_message is not None
    assert "will not replace" in row.error_message


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
            {
                "name": NotionClient.normalize_attachment_filename(
                    Path(pdf.absolute_path).name
                )
            }
        ],
        state_record=record,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.UNCHANGED.value
    assert row.action_taken == "quick_fingerprint_match"
    assert notion.create_calls == 0


def test_sync_one_preview_reports_missing_remote_pdf(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, _ = _make_engine(tmp_path, pdf=pdf, remote_files=[], preview=True)

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    assert row.action_taken == "preview_upload:missing_remote_pdf"


def test_sync_one_preview_reports_multiple_remote_files_as_drift(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, _ = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": "a.pdf"}, {"name": "b.pdf"}],
        preview=True,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == "REMOTE_PDF_CONFLICT"
    assert row.action_taken == "skip:remote_drift_multiple_files"


def test_sync_one_conflicts_on_same_name_single_file_without_state(
    tmp_path: Path,
):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name, "type": "file"}],
        state_record=None,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.REMOTE_PDF_CONFLICT.value
    assert row.action_taken == "skip:remote_pdf_not_managed"
    assert notion.create_calls == 0


def test_sync_one_conflicts_on_unmanaged_mismatched_single_file(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": "someone-elses.pdf", "type": "file"}],
        state_record=None,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == "REMOTE_PDF_CONFLICT"
    assert row.action_taken == "skip:remote_pdf_not_managed"
    assert notion.create_calls == 0


def test_state_without_remote_identity_does_not_claim_a_remote_file(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        "ABC123",
        "page-1",
        pdf.absolute_path,
        pdf.size,
        pdf.mtime_ns,
        "dummy-hash",
        "2026-03-18T00:00:00+00:00",
        Status.OK.value,
        None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name}],
        state_record=record,
        preserve_missing_remote_identity=True,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.REMOTE_PDF_CONFLICT.value
    assert notion.create_calls == 0


def test_remote_identity_change_is_a_conflict(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        "ABC123",
        "page-1",
        pdf.absolute_path,
        pdf.size,
        pdf.mtime_ns,
        "dummy-hash",
        "2026-03-18T00:00:00+00:00",
        Status.OK.value,
        None,
        Path(pdf.absolute_path).name,
        "file",
        "/previous-remote-object",
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name}],
        state_record=record,
    )

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.REMOTE_PDF_CONFLICT.value
    assert notion.create_calls == 0


def test_successful_upload_records_the_remote_identity(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, _ = _make_engine(tmp_path, pdf=pdf, remote_files=[])

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    saved = engine.state.saved
    assert saved.remote_file_name == "sample.pdf"
    assert saved.remote_file_type == "file"
    assert saved.remote_file_identity == "/upload-1"


def test_local_file_disappearing_during_upload_is_an_item_failure(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])

    def disappear(*_args, **_kwargs):
        Path(pdf.absolute_path).unlink()
        raise OSError("file disappeared")

    notion.send_file_bytes = disappear

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert "became unavailable" in (row.error_message or "")


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
            storage_dir=tmp_path / "zotero" / "storage",
        ),
        sync=SimpleNamespace(
            state_db_path=tmp_path / "state" / "noteropdf.sqlite3",
            log_dir=tmp_path / "logs",
        ),
        notion=SimpleNamespace(
            data_source_id="ds-1",
            pdf_property_id="pdf-id",
        ),
        notion_token_source="keyring",
    )
    casted._logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    casted.data_source_id = None

    casted.cfg.zotero.data_dir.mkdir(parents=True)
    casted.cfg.zotero.storage_dir.mkdir(parents=True)

    casted.zotero = SimpleNamespace(
        list_parent_items=lambda: [
            SimpleNamespace(notero_page_url="https://notion.so/page"),
            SimpleNamespace(notero_page_url=None),
        ],
    )
    casted.notion = SimpleNamespace(
        ping=lambda: None,
        get_workspace_upload_limit_bytes=lambda: 123456,
        resolve_property=lambda _ds, ref: SimpleNamespace(id=ref),
        validate_pdf_property=lambda *_: None,
    )

    lines = engine.doctor()

    assert any("group libraries are not synced" in line for line in lines)
    assert any("local read-only API" in line for line in lines)
    assert any("Notion workspace upload limit: 123456 bytes." in line for line in lines)
    assert any("Notion data source is accessible: ds-1" in line for line in lines)

    def fail_snapshot() -> list[object]:
        raise RuntimeError("source unavailable")

    casted.zotero.list_parent_items = fail_snapshot
    with pytest.raises(RuntimeError, match="Zotero read-only snapshot check failed"):
        engine.doctor()


def test_resolve_match_ignores_trashed_primary_page():
    page_id = "123456781234123412341234567890ab"
    item = ZoteroItem(
        item_id=1,
        key="ABC123",
        title="Paper",
        zotero_uri="zotero://select/library/items/ABC123",
        notero_page_url=f"https://www.notion.so/{page_id}",
    )

    engine = SyncEngine.__new__(SyncEngine)
    casted: Any = engine
    casted.cfg = SimpleNamespace(notion=SimpleNamespace(pdf_property_id="pdf-id"))
    casted.data_source_id = "ds"
    casted._snapshot = NotionDataSourceSnapshot(
        pages_by_id={
            page_id: {
                "id": page_id,
                "url": f"https://notion.so/{page_id}",
                "in_trash": True,
            }
        }
    )
    casted.notion = SimpleNamespace()

    match = engine._resolve_match(item)

    assert match.status == Status.NO_NOTION_MATCH


def test_sync_one_skips_when_pdf_changed_since_preview(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    casted: Any = engine
    casted._get_cached_hash = lambda path: sha256_file(Path(path))
    Path(pdf.absolute_path).write_bytes(b"changed after preview")

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert row.action_taken == "skip:stale_preview"
    assert notion.create_calls == 0


def test_sync_one_skips_when_target_page_disappeared(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    casted: Any = engine
    casted._get_cached_hash = lambda path: sha256_file(Path(path))
    notion.get_page = lambda _page_id: None

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert "target Notion page" in (row.error_message or "")
    assert notion.create_calls == 0


def test_sync_one_only_applies_an_unchanged_approved_preview(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    casted: Any = engine
    digest = sha256_file(Path(pdf.absolute_path))
    casted._apply_writes = True
    casted._approved_actions = {
        "ABC123": PreviewAction(
            zotero_item_key="ABC123",
            notion_page_id="page-1",
            pdf_absolute_path=pdf.absolute_path,
            pdf_size=pdf.size,
            pdf_mtime_ns=pdf.mtime_ns,
            pdf_sha256=digest,
        )
    }
    casted._get_cached_hash = lambda _path: digest

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.OK.value
    assert notion.create_calls == 1


def test_approved_action_cannot_be_satisfied_by_a_different_current_target(
    tmp_path: Path,
):
    pdf = _make_pdf(tmp_path)
    record = StateRecord(
        zotero_item_key="ABC123",
        notion_page_id="page-1",
        pdf_absolute_path=pdf.absolute_path,
        pdf_size=pdf.size,
        pdf_mtime_ns=pdf.mtime_ns,
        pdf_sha256=sha256_file(Path(pdf.absolute_path)),
        last_sync_time="2026-08-09T00:00:00+00:00",
        last_status=Status.OK.value,
        last_error_code=None,
    )
    engine, notion = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=[{"name": Path(pdf.absolute_path).name, "type": "file"}],
        state_record=record,
    )
    digest = sha256_file(Path(pdf.absolute_path))
    engine._approved_actions = {
        "ABC123": PreviewAction(
            "ABC123",
            "different-page",
            pdf.absolute_path,
            pdf.size,
            pdf.mtime_ns,
            digest,
        )
    }

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert notion.create_calls == 0


def test_preview_action_records_remote_files_signature(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    remote_files = [{"name": "old.pdf", "type": "file"}]
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
    engine, _ = _make_engine(
        tmp_path,
        pdf=pdf,
        remote_files=remote_files,
        state_record=record,
        preview=True,
    )

    engine._sync_one(_make_item())

    assert engine.preview_actions()[0].remote_files_signature == (
        ("old.pdf", "file", "/remote-0"),
    )


def test_apply_skips_when_remote_files_change_before_upload(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    digest = sha256_file(Path(pdf.absolute_path))
    engine._approved_actions = {
        "ABC123": PreviewAction(
            "ABC123", "page-1", pdf.absolute_path, pdf.size, pdf.mtime_ns, digest, ()
        )
    }
    engine._get_cached_hash = lambda _path: digest
    notion.get_page = lambda _page_id: {
        "id": "page-1",
        "properties": {
            "pdf-id": {
                "id": "pdf-id",
                "type": "files",
                "files": [{"name": "new.pdf", "type": "file"}],
            }
        },
    }

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert notion.create_calls == 0


def test_apply_checks_remote_files_again_immediately_before_attach(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    digest = sha256_file(Path(pdf.absolute_path))
    engine._approved_actions = {
        "ABC123": PreviewAction(
            "ABC123", "page-1", pdf.absolute_path, pdf.size, pdf.mtime_ns, digest, ()
        )
    }
    engine._get_cached_hash = lambda _path: digest
    unchanged_page = engine._snapshot.get_page("page-1")
    changed_page = {
        "id": "page-1",
        "properties": {
            "pdf-id": {
                "id": "pdf-id",
                "type": "files",
                "files": [{"name": "new.pdf", "type": "file"}],
            }
        },
    }
    fresh_pages = iter((unchanged_page, changed_page))
    notion.get_page = lambda _page_id: next(fresh_pages)

    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert notion.create_calls == 1
    assert notion.attach_calls == 0


def test_match_cache_rejects_multiple_items_for_one_page():
    first = _make_item(key="FIRST")
    second = _make_item(key="SECOND")
    engine = SyncEngine.__new__(SyncEngine)
    engine._resolve_match = lambda _item: MatchResult(
        Status.OK, "same-page", "https://notion.so/same-page", None
    )

    cache = engine._build_match_cache([first, second])

    assert cache[first.key].status == Status.MULTIPLE_NOTION_MATCHES
    assert cache[second.key].status == Status.MULTIPLE_NOTION_MATCHES


def test_sync_one_skips_upload_not_in_approved_preview(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, remote_files=[])
    casted: Any = engine
    casted._apply_writes = True
    casted._approved_actions = {}
    row = engine._sync_one(_make_item())

    assert row.final_status == Status.STALE_PREVIEW.value
    assert row.action_taken == "skip:stale_preview"
    assert notion.create_calls == 0


def test_sync_orchestrates_one_complete_preview_from_stable_snapshots(tmp_path: Path):
    pdf = _make_pdf(tmp_path)
    page_id = "11111111-1111-1111-1111-111111111111"
    item = _make_item(notero_page_url=f"https://www.notion.so/{page_id}")
    page = {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "properties": {"pdf-id": {"id": "pdf-id", "type": "files", "files": []}},
    }

    class Zotero:
        closed = False

        def all_items(self):
            return [item]

        def select_candidate_pdf(self, _item):
            return Status.OK.value, pdf, None

        def close(self):
            self.closed = True

    class Notion:
        normalize_attachment_filename = staticmethod(
            NotionClient.normalize_attachment_filename
        )

        def resolve_property(self, _source, property_id):
            return SimpleNamespace(id=property_id)

        def validate_pdf_property(self, *_args):
            return None

        def build_data_source_snapshot(self, _source, **kwargs):
            assert kwargs["pdf_property"] == "pdf-id"
            return NotionDataSourceSnapshot(
                pages_by_id={page_id.replace("-", ""): page},
            )

        def get_workspace_upload_limit_bytes(self):
            return None

    engine = SyncEngine.__new__(SyncEngine)
    engine.cfg = SimpleNamespace(
        notion=SimpleNamespace(
            data_source_id="source",
            pdf_property_id="pdf-id",
        )
    )
    engine.zotero = Zotero()
    engine.notion = Notion()
    engine.state = SimpleNamespace(get=lambda _key: None)
    engine._logger = SimpleNamespace(
        debug=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    engine.data_source_id = None
    engine._snapshot = None
    engine._pdf_property_ref = ""
    engine._apply_writes = False
    engine._preview_actions = {}
    engine._approved_actions = None
    engine._match_cache = None
    engine._hash_cache = {}

    rows = engine.sync()

    assert engine.zotero is None
    assert rows[0].action_taken == "preview_upload:missing_remote_pdf"
    assert engine.preview_actions()[0].notion_page_id == page_id
