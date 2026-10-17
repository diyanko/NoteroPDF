from copy import deepcopy

import pytest

from tests.test_sync_engine import _make_engine, _make_item, _make_pdf


@pytest.mark.parametrize(
    "damage",
    [
        "moved",
        "wrong_page_id",
        "archived",
        "missing_parent",
        "missing_property",
        "malformed_entries",
        "wrong_property_type",
    ],
)
def test_pdf_destination_change_during_upload_never_attaches(
    tmp_path, monkeypatch, damage
):
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, preview=True)
    page = engine._snapshot.get_page("page-1")
    page["parent"] = {"type": "data_source_id", "data_source_id": "ds"}
    engine.cfg.notion.data_source_id = "ds"
    engine.data_source_id = "ds"
    item = _make_item()
    row = engine._sync_one(item)
    assert row.action_taken.startswith("preview_upload:")
    engine._approved_actions = {
        action.zotero_item_key: action for action in engine.preview_actions()
    }
    engine._apply_writes = True
    current_page = deepcopy(page)
    monkeypatch.setattr(notion, "get_page", lambda _page: current_page)
    send = notion.send_file_bytes

    def change_destination(*args):
        if damage == "moved":
            current_page["parent"]["data_source_id"] = "other-source"
        elif damage == "wrong_page_id":
            current_page["id"] = "other-page"
        elif damage == "archived":
            current_page["archived"] = True
            current_page["in_trash"] = False
        elif damage == "missing_parent":
            del current_page["parent"]
        elif damage == "missing_property":
            current_page["properties"] = {}
        elif damage == "malformed_entries":
            current_page["properties"]["pdf-id"]["files"] = [None]
        elif damage == "wrong_property_type":
            current_page["properties"]["pdf-id"]["type"] = "rich_text"
        return send(*args)

    monkeypatch.setattr(notion, "send_file_bytes", change_destination)
    row = engine._sync_one(item)
    assert row.final_status == "STALE_PREVIEW"
    assert notion.create_calls == 1
    assert notion.attach_calls == 0
    assert engine.state.saved is None
