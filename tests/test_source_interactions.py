from dataclasses import replace

import pytest

from noteropdf.destination_lock import lock_destination
from noteropdf.models import ZoteroLibrary
from noteropdf.notion_client import NotionApiError
from noteropdf.source_engine import SourceFilesEngine
from noteropdf.state_store import StateStore
from noteropdf.sync_engine import SyncEngine
from tests import test_source_engine
from tests.test_source_engine import apply, preview

journey = test_source_engine.journey


@pytest.fixture(autouse=True)
def isolated_destination_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "noteropdf.destination_lock.get_default_state_db_path",
        lambda: tmp_path / "app" / "state.sqlite3",
    )


def _assert_unlocked():
    with lock_destination("source", "src-id", apply=True):
        pass


def _assert_no_manifests(cfg):
    state = StateStore(cfg.sync.state_db_path)
    try:
        assert not any(
            key.startswith("source_manifest:") for key in state.get_settings()
        )
    finally:
        state.close()


def test_supporting_lock_covers_snapshot_through_ownership_save(journey, monkeypatch):
    _, approved = preview(journey)
    engine = journey.make()
    observed = set()

    def observe(target, name):
        original = getattr(target, name)

        def checked(*args, **kwargs):
            with (
                pytest.raises(RuntimeError, match="Another NoteroPDF run"),
                lock_destination("source", "src-id", apply=True),
            ):
                pytest.fail("A competing writer acquired the active destination")
            observed.add(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(target, name, checked)

    for name in (
        "build_data_source_snapshot",
        "get_page",
        "create_file_upload",
        "send_file_bytes",
        "attach_source_uploads_to_page",
    ):
        observe(journey.notion, name)
    observe(engine.state, "save_source_manifest")
    try:
        rows = engine.sync(apply=True, approved_actions=approved)
    finally:
        engine.close()
    assert all(row.final_status == "OK" for row in rows)
    assert observed == {
        "build_data_source_snapshot",
        "get_page",
        "create_file_upload",
        "send_file_bytes",
        "attach_source_uploads_to_page",
        "save_source_manifest",
    }
    _assert_unlocked()


@pytest.mark.parametrize("competing_engine", [SourceFilesEngine, SyncEngine])
def test_profiles_and_pdf_sync_share_supporting_destination_lock(
    journey, monkeypatch, tmp_path, competing_engine
):
    _, approved = preview(journey)
    other_cfg = replace(
        journey.cfg,
        sync=replace(
            journey.cfg.sync,
            state_db_path=tmp_path / "profiles" / "lab" / "state.sqlite3",
        ),
    )
    assert other_cfg.sync.state_db_path != journey.cfg.sync.state_db_path
    monkeypatch.setattr(
        journey.repo,
        "select_candidate_pdf",
        lambda _item: ("NO_PDF", None, None),
        raising=False,
    )
    send = journey.notion.send_file_bytes
    blocked = []

    def send_with_competitor(*args, **kwargs):
        with (
            pytest.raises(RuntimeError, match="Another NoteroPDF run"),
            lock_destination("source", "src-id", apply=True),
        ):
            pytest.fail("Supporting-file sync did not acquire the destination")
        extras = (
            {"item_keys": (journey.item.key,)}
            if competing_engine is SourceFilesEngine
            else {}
        )
        other = competing_engine(other_cfg, **extras)
        try:
            with pytest.raises(RuntimeError, match="Another NoteroPDF run"):
                other.sync(apply=True, approved_actions=())
            blocked.append(True)
        finally:
            other.close()
        return send(*args, **kwargs)

    monkeypatch.setattr(journey.notion, "send_file_bytes", send_with_competitor)
    rows = apply(journey, approved)
    assert all(row.final_status == "OK" for row in rows)
    assert len(blocked) == len(journey.files)
    assert len(journey.notion.uploads) == len(journey.files)
    assert len(journey.notion.attaches) == 1
    _assert_no_manifests(other_cfg)
    _assert_unlocked()


def test_second_upload_failure_leaves_bundle_unattached_and_retryable(
    journey, monkeypatch
):
    _, approved = preview(journey)
    send = journey.notion.send_file_bytes
    sent = 0

    def fail_second(*args, **kwargs):
        nonlocal sent
        sent += 1
        if sent == 2:
            raise NotionApiError("UPLOAD_FAILED", "Synthetic second-upload failure")
        return send(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(journey.notion, "send_file_bytes", fail_second)
        rows = apply(journey, approved)
    assert rows[0].final_status == "UPLOAD_FAILED"
    assert sent == len(journey.notion.uploads) == 2
    assert not journey.notion.remote
    assert not journey.notion.attaches
    _assert_no_manifests(journey.cfg)
    _assert_unlocked()
    _, approved = preview(journey)
    assert len(approved) == 2
    rows = apply(journey, approved)
    assert all(row.final_status == "OK" for row in rows)
    assert len(journey.notion.attaches) == 1
    assert len(journey.notion.uploads) == 4
    _, approved = preview(journey)
    assert not approved


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, RuntimeError])
def test_interruption_after_attachment_releases_lock_without_granting_ownership(
    journey, monkeypatch, error_type
):
    _, approved = preview(journey)

    def interrupt(*_args, **_kwargs):
        raise error_type("Synthetic interruption after attachment")

    with monkeypatch.context() as patch:
        patch.setattr(StateStore, "save_source_manifest", interrupt)
        if error_type is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                apply(journey, approved)
        else:
            rows = apply(journey, approved)
            assert rows[0].final_status == "STATE_SAVE_FAILED"
    assert len(journey.notion.attaches) == 1
    assert len(journey.notion.remote) == 2
    _assert_no_manifests(journey.cfg)
    _assert_unlocked()
    uploads = len(journey.notion.uploads)
    rows, new_approved = preview(journey)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not new_approved
    rows = apply(journey, approved)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert len(journey.notion.uploads) == uploads
    assert len(journey.notion.attaches) == 1


def test_personal_ownership_does_not_authorize_group_with_same_item_keys(journey):
    _, approved = preview(journey)
    apply(journey, approved)
    group_item = replace(journey.item, library=ZoteroLibrary(group_id=42))
    journey.repo.all_items = lambda: [group_item]
    rows, approved = preview(journey)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not approved
    assert len(journey.notion.attaches) == 1
    assert len(journey.notion.uploads) == 2


@pytest.mark.parametrize(
    "damage",
    [
        "extra_entry",
        "wrong_parent",
        "missing_parent",
        "missing_property",
        "missing_page_id",
        "wrong_page_id",
    ],
)
def test_unverified_attachment_response_cannot_grant_ownership(
    journey, monkeypatch, damage
):
    _, approved = preview(journey)
    attach = journey.notion.attach_source_uploads_to_page

    def damaged_response(*args, **kwargs):
        page = attach(*args, **kwargs)
        if damage == "extra_entry":
            page["properties"]["Source files"]["files"].append(None)
        elif damage == "wrong_parent":
            page["parent"]["data_source_id"] = "other-source"
        elif damage == "missing_parent":
            del page["parent"]
        elif damage == "missing_property":
            page["properties"] = {}
        elif damage == "missing_page_id":
            del page["id"]
        elif damage == "wrong_page_id":
            page["id"] = "87654321876543218765432187654321"
        return page

    monkeypatch.setattr(
        journey.notion, "attach_source_uploads_to_page", damaged_response
    )
    rows = apply(journey, approved)
    assert rows[0].final_status == "STATE_SAVE_FAILED"
    assert len(journey.notion.attaches) == 1
    _assert_no_manifests(journey.cfg)
    _assert_unlocked()
    rows, approved = preview(journey)
    assert rows[0].final_status == "REMOTE_PDF_CONFLICT"
    assert not approved
    assert len(journey.notion.attaches) == 1


@pytest.mark.parametrize(
    "configured, resolved, supporting",
    [
        ("x%3Ay", "x:y", "x:y"),
        ("x:y", "x:y", "x%3Ay"),
        ("PDF field", "src-id", "src-id"),
        (
            "AABBCCDD1234567890ABCDEF12345678",
            "aabbccdd-1234-5678-90ab-cdef12345678",
            "aabbccdd-1234-5678-90ab-cdef12345678",
        ),
    ],
)
def test_supporting_configuration_rejects_equivalent_pdf_destination(
    journey, monkeypatch, configured, resolved, supporting
):
    from types import SimpleNamespace

    from noteropdf import source_engine

    closed = []
    client = SimpleNamespace(
        find_files_property=lambda *_: SimpleNamespace(id=supporting),
        resolve_property=lambda *_: SimpleNamespace(id=resolved),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(source_engine, "NotionClient", lambda **_kwargs: client)
    cfg = replace(
        journey.cfg,
        notion=replace(journey.cfg.notion, pdf_property_id=configured),
    )
    with pytest.raises(ValueError, match="separate files property"):
        source_engine.source_files_config(cfg)
    assert closed == [True]
    assert not journey.notion.uploads
    assert not journey.notion.attaches
