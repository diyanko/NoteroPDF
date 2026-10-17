from copy import deepcopy

import pytest

from noteropdf.notion_client import NotionClient
from tests import test_source_engine
from tests.test_source_engine import apply, preview
from tests.test_sync_engine import _make_engine, _make_item, _make_pdf

journey = test_source_engine.journey


def _page(properties):
    return {
        "id": "page-1",
        "parent": {"type": "data_source_id", "data_source_id": "ds"},
        "properties": properties,
    }


def _verified(page, property_ref="pdf-id"):
    return NotionClient.verified_files_signature(
        page, page_id="page-1", data_source_id="ds", property_ref=property_ref
    )


def _manual_file():
    return {
        "name": "manual.pdf",
        "type": "file",
        "file": {"url": "https://files.example/manual"},
    }


def test_verification_uses_stable_id_before_a_colliding_property_name():
    target = {"id": "pdf-id", "type": "files", "files": [_manual_file()]}
    page = _page(
        {
            "pdf-id": {"id": "other-id", "type": "files", "files": []},
            "PDF": target,
        }
    )
    assert NotionClient.get_page_property(page, "pdf-id") is target
    assert _verified(page) == (("manual.pdf", "file", "/manual"),)
    assert NotionClient.get_page_property(page, "PDF") is target


@pytest.mark.parametrize(
    "reference, actual",
    [
        ("x:y", "x%3Ay"),
        ("x%3Ay", "x:y"),
        (
            "AABBCCDD1234567890ABCDEF12345678",
            "aabbccdd-1234-5678-90ab-cdef12345678",
        ),
    ],
)
def test_verification_accepts_canonical_property_id_aliases(reference, actual):
    page = _page({"Renamed field": {"id": actual, "type": "files", "files": []}})
    assert _verified(page, reference) == ()


@pytest.mark.parametrize(
    "properties",
    [
        None,
        [],
        {"pdf-id": {"type": "files", "files": []}},
        {"pdf-id": {"id": 12, "type": "files", "files": []}},
        {"pdf-id": {"id": "other-id", "type": "files", "files": []}},
        {
            "One": {"id": "x:y", "type": "files", "files": []},
            "Two": {"id": "x%3Ay", "type": "files", "files": []},
        },
    ],
)
def test_verification_rejects_missing_wrong_or_ambiguous_property_ids(properties):
    with pytest.raises(ValueError, match="field could not be verified"):
        _verified(
            _page(properties), "x:y" if properties and "One" in properties else "pdf-id"
        )


@pytest.mark.parametrize("kind", [[], {}])
def test_unknown_file_type_shapes_keep_the_value_error_contract(kind):
    page = _page(
        {
            "PDF": {
                "id": "pdf-id",
                "type": "files",
                "files": [{"name": "x", "type": kind}],
            }
        }
    )
    with pytest.raises(ValueError, match="unknown entries"):
        _verified(page)


def test_pdf_does_not_overwrite_a_change_hidden_by_a_colliding_field_name(
    tmp_path, monkeypatch
):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, preview=True)
    item = _make_item()
    engine._sync_one(item)
    engine._approved_actions = {
        action.zotero_item_key: action for action in engine.preview_actions()
    }
    engine._apply_writes = True
    current = deepcopy(engine._snapshot.get_page("page-1"))
    monkeypatch.setattr(notion, "get_page", lambda _page: current)
    send = notion.send_file_bytes

    def concurrent_change(*args):
        current["properties"] = {
            "PDF": {"id": "pdf-id", "type": "files", "files": [_manual_file()]},
            "pdf-id": {"id": "other-id", "type": "files", "files": []},
        }
        return send(*args)

    monkeypatch.setattr(notion, "send_file_bytes", concurrent_change)
    row = engine._sync_one(item)
    assert row.final_status == "STALE_PREVIEW"
    assert notion.create_calls == 1
    assert notion.attach_calls == 0
    assert engine.state.saved is None


def test_sources_do_not_overwrite_a_change_hidden_by_a_colliding_field_name(
    journey, monkeypatch
):
    _, approved = preview(journey)
    page = journey.notion.page

    def full_page():
        current = page()
        current["properties"]["src-id"] = {
            "id": "other-id",
            "type": "files",
            "files": [],
        }
        return current

    monkeypatch.setattr(journey.notion, "page", full_page)
    journey.notion.before_attach = lambda: journey.notion.remote.append(_manual_file())
    rows = apply(journey, approved)
    assert rows[0].final_status == "STALE_PREVIEW"
    assert not journey.notion.attaches
    assert all(entry["name"] == "manual.pdf" for entry in journey.notion.remote)
