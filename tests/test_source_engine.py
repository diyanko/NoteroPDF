from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from noteropdf import sync_engine
from noteropdf.config import AppConfig, NotionConfig, SyncConfig, ZoteroConfig
from noteropdf.models import SourceFile, ZoteroItem
from noteropdf.notion_client import (
    NotionApiError,
    NotionClient,
    NotionDataSourceSnapshot,
)
from noteropdf.source_engine import SourceFilesEngine

PAGE = "123456781234123412341234567890ab"


@pytest.fixture
def journey(tmp_path, monkeypatch):
    item = ZoteroItem(
        1,
        "PARENT01",
        None,
        "zotero://select/library/items/PARENT01",
        f"https://notion.so/{PAGE}",
    )
    sources = []
    for index, (filename, mime) in enumerate(
        [
            (
                "source.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("methods.pdf", "application/pdf"),
        ]
    ):
        path = tmp_path / filename
        path.write_bytes(b"test source")
        stat = path.stat()
        sources.append(
            SourceFile(
                f"ATT0000{index}", str(path), mime, stat.st_size, stat.st_mtime_ns
            )
        )
    repository = SimpleNamespace(
        all_items=lambda: [item],
        select_source_files=lambda _item: ("OK", sources, None),
        close=lambda: None,
    )

    class Notion:
        normalize_attachment_filename = staticmethod(
            NotionClient.normalize_attachment_filename
        )

        def __init__(self):
            self.remote = []
            self.uploads = []
            self.attaches = []
            self.before_attach = None

        def close(self):
            pass

        def resolve_property(self, _ds, _ref):
            return SimpleNamespace(id="src-id")

        def validate_pdf_property(self, *_args):
            pass

        def get_workspace_upload_limit_bytes(self):
            return 1000000

        def page(self):
            return {
                "id": PAGE,
                "parent": {"type": "data_source_id", "data_source_id": "source"},
                "properties": {
                    "Source files": {
                        "id": "src-id",
                        "type": "files",
                        "files": list(self.remote),
                    }
                },
            }

        def get_page(self, _page):
            return self.page()

        def build_data_source_snapshot(self, *_args, **_kwargs):
            return NotionDataSourceSnapshot({PAGE: self.page()})

        def create_file_upload(self, filename, content_type, *, file_size):
            self.uploads.append((filename, content_type, file_size))
            return {"id": f"upload-{len(self.uploads)}"}

        def send_file_bytes(self, payload, _path, *, content_type):
            if self.before_attach:
                self.before_attach()
            return payload["id"]

        def attach_source_uploads_to_page(self, _page, _property, files):
            self.attaches.append(files)
            self.remote = [
                {
                    "name": f["name"],
                    "type": "file",
                    "file": {
                        "url": f"https://files.example/{f['file_upload']['id']}?signed=1"
                    },
                }
                for f in files
            ]
            return self.page()

    notion = Notion()

    class Factory:
        files_property_signature = staticmethod(NotionClient.files_property_signature)

        def __new__(cls, **_kw):
            return notion

    monkeypatch.setattr(sync_engine, "ZoteroRepository", lambda **kw: repository)
    monkeypatch.setattr(sync_engine, "NotionClient", Factory)
    cfg = AppConfig(
        ZoteroConfig(tmp_path, tmp_path),
        NotionConfig("2026-03-11", "source", "src-id"),
        SyncConfig(tmp_path / "state.sqlite3", tmp_path / "logs"),
        "test-token",
        "keyring",
    )

    def make(**kw):
        return SourceFilesEngine(cfg, item_keys=kw.pop("item_keys", (item.key,)), **kw)

    return SimpleNamespace(
        cfg=cfg, make=make, notion=notion, repo=repository, files=sources, item=item
    )


def preview(journey):
    engine = journey.make()
    try:
        rows = engine.sync(apply=False)
        return rows, engine.preview_actions()
    finally:
        engine.close()


def apply(journey, approved):
    engine = journey.make()
    try:
        return engine.sync(apply=True, approved_actions=approved)
    finally:
        engine.close()


def test_multi_file_preview_apply_and_repeat_are_deterministic(journey):
    rows, actions = preview(journey)
    assert len(rows) == len(actions) == 2
    assert not journey.notion.uploads
    assert not journey.notion.attaches
    result = apply(journey, actions)
    assert all(row.final_status == "OK" for row in result)
    assert len(journey.notion.uploads) == 2
    assert len(journey.notion.attaches) == 1
    assert {x[1] for x in journey.notion.uploads} == {
        f.content_type for f in journey.files
    }
    repeat, actions = preview(journey)
    assert not actions
    assert all(row.final_status == "UNCHANGED" for row in repeat)
    assert len(journey.notion.uploads) == 2


def test_changed_file_preserves_other_owned_attachment(journey):
    _, actions = preview(journey)
    apply(journey, actions)
    file = journey.files[0]
    Path(file.absolute_path).write_bytes(b"updated source")
    stat = Path(file.absolute_path).stat()
    journey.files[0] = replace(file, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    _, actions = preview(journey)
    assert len(actions) == 1
    apply(journey, actions)
    assert len(journey.notion.uploads) == 3
    assert len(journey.notion.remote) == 2
    assert any(
        f["file"]["url"].startswith("https://files.example/upload-2")
        for f in journey.notion.remote
    )


def test_unknown_remote_file_is_never_replaced(journey):
    journey.notion.remote = [
        {
            "name": "manual.pdf",
            "type": "external",
            "external": {"url": "https://example.com/manual.pdf"},
        }
    ]
    rows, actions = preview(journey)
    assert not actions
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not journey.notion.uploads
    assert not journey.notion.attaches


def test_concurrent_remote_change_after_preview_blocks_upload(journey):
    _, actions = preview(journey)
    journey.notion.remote = [
        {
            "name": "manual.pdf",
            "type": "external",
            "external": {"url": "https://example.com/manual.pdf"},
        }
    ]
    rows = apply(journey, actions)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not journey.notion.uploads


def test_local_change_after_preview_blocks_upload(journey):
    _, actions = preview(journey)
    file = journey.files[0]
    Path(file.absolute_path).write_bytes(b"other source")
    stat = Path(file.absolute_path).stat()
    journey.files[0] = replace(file, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    rows = apply(journey, actions)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not journey.notion.uploads


def test_remote_change_during_upload_never_attaches_bytes(journey):
    _, actions = preview(journey)
    journey.notion.before_attach = lambda: journey.notion.remote.append(
        {
            "name": "manual",
            "type": "external",
            "external": {"url": "https://example.com/manual"},
        }
    )
    rows = apply(journey, actions)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not journey.notion.attaches


def test_unapproved_new_attachment_blocks_entire_bundle(journey):
    _, actions = preview(journey)
    rows = apply(journey, actions[:1])
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not journey.notion.uploads


def test_removing_local_attachment_does_not_delete_remote(journey):
    _, actions = preview(journey)
    apply(journey, actions)
    journey.files.pop()
    rows, actions = preview(journey)
    assert all(row.final_status == "UNCHANGED" for row in rows)
    assert not actions
    assert len(journey.notion.remote) == 2


def test_new_file_preserves_retained_owned_files(journey):
    _, actions = preview(journey)
    apply(journey, actions)
    journey.files.pop()
    file = journey.files[0]
    new_path = Path(file.absolute_path).with_name("new-source.xlsx")
    new_path.write_bytes(b"new source")
    stat = new_path.stat()
    journey.files[0] = replace(
        file,
        attachment_key="NEW00001",
        absolute_path=str(new_path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    _, actions = preview(journey)
    apply(journey, actions)
    assert len(journey.notion.remote) == 3


def test_apply_requires_explicit_preview(journey):
    engine = journey.make()
    try:
        with pytest.raises(ValueError, match="approved preview"):
            engine.sync(apply=True)
    finally:
        engine.close()


def test_collection_and_item_scope_are_enforced(journey):
    engine = journey.make(item_keys=("OTHER001",))
    try:
        with pytest.raises(ValueError, match="No references matched"):
            engine.sync()
    finally:
        engine.close()
    with pytest.raises(ValueError, match="at least one item"):
        SourceFilesEngine(journey.cfg, item_keys=())
    with pytest.raises(ValueError, match="8 uppercase"):
        SourceFilesEngine(journey.cfg, item_keys=("invalid",))


def test_missing_local_source_is_reported(journey):
    journey.repo.select_source_files = lambda _item: (
        "BROKEN_ATTACHMENT_PATH",
        [],
        "Missing file",
    )
    rows, actions = preview(journey)
    assert rows[0].final_status == "BROKEN_ATTACHMENT_PATH"
    assert not actions


def test_no_matching_notero_link_never_uploads(journey):
    journey.repo.all_items = lambda: [replace(journey.item, notero_page_url=None)]
    rows, actions = preview(journey)
    assert rows[0].final_status == "NO_NOTION_MATCH"
    assert not actions


def test_workspace_limit_is_enforced(journey):
    journey.notion.get_workspace_upload_limit_bytes = lambda: 1
    rows, actions = preview(journey)
    assert rows[0].final_status == "FILE_TOO_LARGE"
    assert not actions


def test_upload_failure_does_not_attach_partial_bundle(journey):
    _, actions = preview(journey)

    def fail(*_args, **_kwargs):
        raise NotionApiError("UPLOAD_FAILED", "Test failure")

    journey.notion.send_file_bytes = fail
    rows = apply(journey, actions)
    assert rows[0].final_status == "UPLOAD_FAILED"
    assert not journey.notion.attaches


def test_state_save_failure_never_grants_future_ownership(journey, monkeypatch):
    from noteropdf.state_store import StateStore

    _, actions = preview(journey)

    def fail(*_args):
        raise RuntimeError("Test failure")

    monkeypatch.setattr(StateStore, "save_source_manifest", fail)
    result = apply(journey, actions)
    assert result[0].final_status == "STATE_SAVE_FAILED"
    rows, actions = preview(journey)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not actions


def test_source_configuration_resolves_existing_property_and_isolates_history(
    journey, monkeypatch
):
    from noteropdf import source_engine

    client = SimpleNamespace(
        find_files_property=lambda *_: SimpleNamespace(id="src-id"), close=lambda: None
    )
    monkeypatch.setattr(source_engine, "NotionClient", lambda **_kw: client)
    cfg = source_engine.source_files_config(
        replace(
            journey.cfg, notion=replace(journey.cfg.notion, pdf_property_id="pdf-id")
        )
    )
    assert cfg.notion.pdf_property_id == "src-id"
    assert cfg.sync.state_db_path == journey.cfg.sync.state_db_path
    client.find_files_property = lambda *_: None
    with pytest.raises(Exception, match="Create a dedicated"):
        source_engine.source_files_config(journey.cfg)


def test_history_change_after_preview_cannot_replace_a_retained_file(journey):
    from noteropdf.state_store import StateStore

    _, actions = preview(journey)
    apply(journey, actions)
    journey.files.pop()
    path = Path(journey.files[0].absolute_path)
    path.write_bytes(b"changed source")
    stat = path.stat()
    journey.files[0] = replace(
        journey.files[0], size=stat.st_size, mtime_ns=stat.st_mtime_ns
    )
    _, approved = preview(journey)
    state = StateStore(journey.cfg.sync.state_db_path)
    try:
        key, raw = next(
            (key, raw)
            for key, raw in state.get_settings().items()
            if key.startswith("source_manifest:")
        )
        import json

        manifest = json.loads(raw)
        retained = next(key for key in manifest["files"] if key.endswith("ATT00001"))
        manifest["files"][retained]["upload_id"] = "unapproved-replacement"
        state.update_settings({key: json.dumps(manifest)})
    finally:
        state.close()
    count = len(journey.notion.uploads)
    attached = len(journey.notion.attaches)
    result = apply(journey, approved)
    assert result[0].final_status == "STALE_PREVIEW"
    assert len(journey.notion.uploads) == count
    assert len(journey.notion.attaches) == attached


def test_retained_filename_collision_blocks_before_upload(journey):
    _, actions = preview(journey)
    apply(journey, actions)
    journey.files.pop()
    journey.files[0] = replace(journey.files[0], attachment_key="NEW00001")
    count = len(journey.notion.uploads)
    rows, actions = preview(journey)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not actions
    assert len(journey.notion.uploads) == count


@pytest.mark.parametrize(
    "remote",
    [[None], [{"name": "unknown"}], [{"name": "unknown", "type": "file", "file": {}}]],
)
def test_malformed_remote_entries_are_never_overwritten(journey, remote):
    journey.notion.remote = remote
    rows, actions = preview(journey)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not actions
    assert not journey.notion.uploads


def test_destination_moved_during_upload_blocks_attachment(journey):
    _, actions = preview(journey)
    original = journey.notion.page

    def moved():
        page = original()
        page["parent"]["data_source_id"] = "another-source"
        return page

    journey.notion.before_attach = lambda: setattr(journey.notion, "page", moved)
    rows = apply(journey, actions)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not journey.notion.attaches


def test_duplicate_page_target_outside_selection_blocks_supporting_files(journey):
    outside = replace(journey.item, key="OTHER001")
    journey.repo.all_items = lambda: [journey.item, outside]
    rows, actions = preview(journey)
    assert rows[0].final_status == "MULTIPLE_NOTION_MATCHES"
    assert not actions
    assert not journey.notion.uploads


def test_supporting_field_cannot_reuse_pdf_destination(journey, monkeypatch):
    from noteropdf import source_engine

    client = SimpleNamespace(
        find_files_property=lambda *_: SimpleNamespace(id="src-id"), close=lambda: None
    )
    monkeypatch.setattr(source_engine, "NotionClient", lambda **_kwargs: client)
    with pytest.raises(ValueError, match="separate files property"):
        source_engine.source_files_config(journey.cfg)


def test_whitespace_filename_is_rejected_before_upload(journey):
    old = journey.files[0]
    path = Path(old.absolute_path).with_name(" source.xlsx")
    path.write_bytes(b"test source")
    stat = path.stat()
    journey.files[0] = replace(
        old, absolute_path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns
    )
    rows, actions = preview(journey)
    assert rows[0].final_status == "BROKEN_ATTACHMENT_PATH"
    assert "Rename" in rows[0].error_message
    assert not actions
    assert not journey.notion.uploads
