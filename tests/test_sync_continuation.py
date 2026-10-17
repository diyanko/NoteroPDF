from copy import deepcopy
from types import SimpleNamespace

from noteropdf.notion_client import NotionDataSourceSnapshot
from noteropdf.status import Status
from noteropdf.sync_engine import MatchResult
from tests.test_sync_engine import _make_engine, _make_item, _make_pdf


def test_malformed_approved_destination_preserves_other_item_results(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "noteropdf.destination_lock.get_default_state_db_path",
        lambda: tmp_path / "app" / "state.sqlite3",
    )
    pdf = _make_pdf(tmp_path)
    engine, notion = _make_engine(tmp_path, pdf=pdf, preview=True)
    engine.cfg.notion.data_source_id = "ds"
    items = [_make_item(key=key) for key in ("FIRST001", "SECOND01", "THIRD001")]
    destinations = {item.key: f"page-{index}" for index, item in enumerate(items)}
    pages = {
        NotionDataSourceSnapshot.page_id_key(page_id): {
            "id": page_id,
            "parent": {"type": "data_source_id", "data_source_id": "ds"},
            "properties": {"PDF": {"id": "pdf-id", "type": "files", "files": []}},
        }
        for page_id in destinations.values()
    }
    snapshot = NotionDataSourceSnapshot(pages)
    repository = SimpleNamespace(
        all_items=lambda: items,
        select_candidate_pdf=lambda _item: ("OK", pdf, None),
        close=lambda: None,
    )
    engine.zotero = repository
    engine._resolve_match = lambda item: MatchResult(
        Status.OK, destinations[item.key], None, None
    )
    notion.resolve_property = lambda _source, ref: SimpleNamespace(id=ref)
    notion.validate_pdf_property = lambda *_args: None
    notion.build_data_source_snapshot = lambda *_args, **_kwargs: snapshot
    notion.get_page = lambda page_id: snapshot.get_page(page_id)
    saved = []
    engine.state.upsert = saved.append

    def attach(**kwargs):
        notion.attach_calls += 1
        page = deepcopy(snapshot.get_page(kwargs["page_id"]))
        page["properties"]["PDF"]["files"] = [
            {
                "name": kwargs["filename"],
                "type": "file",
                "file": {"url": f"https://files.example/{kwargs['page_id']}"},
            }
        ]
        return page

    notion.attach_file_upload_to_page = attach
    rows = engine.sync(apply=False)
    assert [row.final_status for row in rows] == ["OK"] * 3
    approved = engine.preview_actions()
    snapshot.get_page(destinations["SECOND01"])["properties"] = {}
    engine.zotero = repository
    rows = engine.sync(apply=True, approved_actions=approved)
    assert [row.final_status for row in rows] == ["OK", "STALE_PREVIEW", "OK"]
    assert [record.zotero_item_key for record in saved] == ["FIRST001", "THIRD001"]
    assert notion.create_calls == notion.attach_calls == 2
