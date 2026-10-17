import sys
from subprocess import run
from types import SimpleNamespace

import pytest

from noteropdf.destination_lock import lock_destination
from tests.test_sync_engine import _make_engine, _make_item, _make_pdf


@pytest.fixture
def app_storage(monkeypatch, tmp_path):
    path = tmp_path / "app"
    monkeypatch.setattr(
        "noteropdf.destination_lock.get_default_state_db_path",
        lambda: path / "state.sqlite3",
    )
    return path


def _child_lock(app_storage, source, property_id):
    code = """
from pathlib import Path
import sys
import noteropdf.destination_lock as module
module.get_default_state_db_path = lambda: Path(sys.argv[1]) / "state.sqlite3"
try:
    with module.lock_destination(sys.argv[2], sys.argv[3], apply=True):
        pass
except RuntimeError as error:
    print(str(error))
    sys.exit(2)
"""
    return run(
        [sys.executable, "-c", code, str(app_storage), source, property_id],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_lock_excludes_other_processes_using_equivalent_destination_ids(app_storage):
    source = "aabbccdd-1234-5678-90ab-cdef12345678"
    with lock_destination(source, "x%3Ay", apply=True):
        blocked = _child_lock(app_storage, source.replace("-", "").upper(), "x:y")
        assert blocked.returncode == 2
        assert "Another NoteroPDF run" in blocked.stdout
        assert str(app_storage) not in blocked.stdout
        assert source not in blocked.stdout
        assert _child_lock(app_storage, source, "other").returncode == 0
        assert _child_lock(app_storage, "different-source", "x:y").returncode == 0
    assert _child_lock(app_storage, source, "x:y").returncode == 0
    assert all(
        source not in path.name and "x:y" not in path.name
        for path in (app_storage / "destination-locks").iterdir()
    )


def test_property_ids_remain_case_sensitive_and_uuid_aliases_share_locks(app_storage):
    with (
        lock_destination("source", "x:y", apply=True),
        lock_destination("source", "X:y", apply=True),
    ):
        pass
    with (
        lock_destination("source", "aabbccdd-1234-5678-90ab-cdef12345678", apply=True),
        pytest.raises(RuntimeError, match="Another NoteroPDF run"),
        lock_destination("source", "AABBCCDD1234567890ABCDEF12345678", apply=True),
    ):
        pass


def test_preview_has_no_destination_lock_filesystem_effects(app_storage):
    with lock_destination("source", "pdf-id", apply=False):
        assert not app_storage.exists()
    assert not app_storage.exists()


def test_lock_releases_after_error_and_hides_storage_error_details(
    monkeypatch, app_storage
):
    with (
        pytest.raises(ValueError, match="interrupted"),
        lock_destination("source", "pdf-id", apply=True),
    ):
        raise ValueError("interrupted")
    with lock_destination("source", "pdf-id", apply=True):
        pass
    monkeypatch.setattr(
        "noteropdf.destination_lock.FileLock",
        lambda path: (_ for _ in ()).throw(PermissionError(f"private path: {path}")),
    )
    with (
        pytest.raises(RuntimeError, match="Check access to local app storage") as error,
        lock_destination("source", "pdf-id", apply=True),
    ):
        pass
    assert str(app_storage) not in str(error.value)


def _prepare_engine(tmp_path, *, filename, state_name):
    pdf = _make_pdf(tmp_path, name=filename)
    engine, notion = _make_engine(tmp_path, pdf=pdf, preview=True)
    item = _make_item()
    engine._sync_one(item)
    approved = engine.preview_actions()
    engine.cfg.notion.data_source_id = "source"
    engine.cfg.sync.state_db_path = tmp_path / state_name / "state.db"
    engine.zotero = SimpleNamespace(
        all_items=lambda: [item],
        select_candidate_pdf=lambda item: ("OK", pdf, None),
        close=lambda: None,
    )
    notion.resolve_property = lambda source, prop: SimpleNamespace(id=prop)
    notion.validate_pdf_property = lambda *args: None
    snapshot = engine._snapshot
    notion.build_data_source_snapshot = lambda *args, **kwargs: snapshot
    return engine, notion, approved


def test_apply_locks_snapshot_upload_attach_and_state_save_across_profiles(
    app_storage, tmp_path
):
    first, notion, approved = _prepare_engine(
        tmp_path, filename="first.pdf", state_name="default"
    )
    other, other_notion, other_approved = _prepare_engine(
        tmp_path, filename="second.pdf", state_name="profile-lab"
    )
    assert first.cfg.sync.state_db_path != other.cfg.sync.state_db_path
    observed = []

    def assert_locked(stage):
        with (
            pytest.raises(RuntimeError, match="Another NoteroPDF run"),
            first._destination_lock("source", "pdf-id", apply=True),
        ):
            pytest.fail("a competing writer acquired the same destination")
        observed.append(stage)

    snapshot = first._snapshot

    def build_snapshot(*args, **kwargs):
        assert_locked("snapshot")
        return snapshot

    notion.build_data_source_snapshot = build_snapshot
    get_page = notion.get_page

    def fresh_page(page_id):
        assert_locked("fresh_page")
        return get_page(page_id)

    notion.get_page = fresh_page
    send = notion.send_file_bytes

    def upload(*args):
        assert_locked("upload")
        with pytest.raises(RuntimeError, match="Another NoteroPDF run"):
            other.sync(apply=True, approved_actions=other_approved)
        return send(*args)

    notion.send_file_bytes = upload
    attach = notion.attach_file_upload_to_page

    def attach_file(**kwargs):
        assert_locked("attach")
        return attach(**kwargs)

    notion.attach_file_upload_to_page = attach_file
    save = first.state.upsert

    def save_state(record):
        assert_locked("state_save")
        save(record)

    first.state.upsert = save_state

    rows = first.sync(apply=True, approved_actions=approved)
    assert rows[0].final_status == "OK"
    assert {"snapshot", "fresh_page", "upload", "attach", "state_save"}.issubset(
        observed
    )
    assert notion.create_calls == 1
    assert other_notion.create_calls == 0
    assert other_notion.attach_calls == 0
    with first._destination_lock("source", "pdf-id", apply=True):
        pass


def test_engine_preview_does_not_acquire_destination_lock(app_storage, tmp_path):
    engine, notion, _approved = _prepare_engine(
        tmp_path, filename="paper.pdf", state_name="lab"
    )
    rows = engine.sync(apply=False)
    assert rows[0].action_taken.startswith("preview_upload:")
    assert notion.create_calls == 0
    assert not app_storage.exists()
