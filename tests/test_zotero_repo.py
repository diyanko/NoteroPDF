from pathlib import Path

import pytest

from noteropdf import zotero_repo
from noteropdf.models import ZoteroAttachment, ZoteroItem, ZoteroLibrary
from noteropdf.zotero_repo import ZoteroRepository


def _expected_windows_drive_path(path_without_drive: str) -> Path:
    path_with_drive = f"C:/{path_without_drive}"
    # On Unix hosts this is a relative path; anchor it so expectations are stable.
    if Path(path_with_drive).is_absolute():
        return Path(path_with_drive).resolve()
    return Path(f"/{path_with_drive}").resolve()


def _path_repo(*, storage_dir: Path, data_dir: Path) -> ZoteroRepository:
    repo = object.__new__(ZoteroRepository)
    repo._library = ZoteroLibrary()
    repo._storage_dir = storage_dir.resolve()
    repo._data_dir = data_dir.resolve()
    repo._api_parent_items = []
    repo._api_attachments = {}
    return repo


class _FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        version: str = "10",
        status_code: int = 200,
        api_version: str = "3",
        total: int | str | None = None,
    ):
        self._payload = payload
        self.status_code = status_code
        self.headers = {
            "Zotero-API-Version": api_version,
            "Last-Modified-Version": version,
            "Total-Results": str(
                total
                if total is not None
                else len(payload)
                if isinstance(payload, list)
                else 0
            ),
        }

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.trust_env = True
        self.closed = False

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected local API request")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _api_parent_record() -> dict[str, object]:
    return {
        "data": {
            "key": "PARENT1",
            "itemType": "journalArticle",
            "title": "Paper",
            "DOI": "10.1000/example",
        },
        "library": {"id": 42},
        "links": {
            "alternate": {"href": "https://www.zotero.org/users/42/items/PARENT1"}
        },
    }


def _api_attachment_records(pdf_path: Path) -> list[dict[str, object]]:
    return [
        {
            "data": {
                "key": "NOTION1",
                "itemType": "attachment",
                "parentItem": "PARENT1",
                "linkMode": "linked_url",
                "title": "Notion",
                "url": (
                    "notion://www.notion.so/Paper-123456781234123412341234567890ab"
                ),
                "contentType": "",
            }
        },
        {
            "data": {
                "key": "PDF0001",
                "itemType": "attachment",
                "parentItem": "PARENT1",
                "linkMode": "imported_url",
                "title": "Full Text PDF",
                "filename": pdf_path.name,
                "contentType": "application/pdf",
            },
            "links": {"enclosure": {"href": pdf_path.as_uri()}},
        },
    ]


def _api_items(pdf_path: Path) -> list[dict[str, object]]:
    return [_api_parent_record(), *_api_attachment_records(pdf_path)]


def _use_fake_local_api(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]
) -> _FakeSession:
    session = _FakeSession(responses)
    monkeypatch.setattr(zotero_repo, "LOCAL_API_BASE_URL", "http://127.0.0.1:23119/api")
    monkeypatch.setattr(zotero_repo.requests, "Session", lambda: session)
    return session


def test_local_api_snapshot_is_preferred_and_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    storage_dir = tmp_path / "storage"
    pdf_path = storage_dir / "PDF0001" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"pdf")
    session = _use_fake_local_api(
        monkeypatch,
        [_FakeResponse(_api_items(pdf_path))],
    )

    repo = ZoteroRepository(storage_dir, tmp_path)
    try:
        items = repo.list_parent_items()
        assert len(items) == 1
        item = items[0]
        assert item.key == "PARENT1"
        assert item.notero_page_url == (
            "notion://www.notion.so/Paper-123456781234123412341234567890ab"
        )
        assert repo.select_candidate_pdf(item)[0] == "OK"
    finally:
        repo.close()

    assert session.trust_env is False
    assert session.closed is True
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == (
        "http://127.0.0.1:23119/api/users/0/items"
        "?limit=100&start=0&sort=dateAdded&direction=asc"
    )
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {
        "Accept": "application/json",
        "Zotero-API-Version": "3",
    }


def test_api_disabled_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _use_fake_local_api(monkeypatch, [_FakeResponse({}, status_code=403)])
    with pytest.raises(RuntimeError) as exc_info:
        ZoteroRepository(tmp_path, tmp_path)

    message = str(exc_info.value)
    assert "local API" in message
    assert "Allow other applications" in message
    assert "Open Zotero" in message


def test_api_imported_pdf_must_be_inside_configured_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    configured_storage = tmp_path / "configured-storage"
    configured_storage.mkdir()
    other_pdf = tmp_path / "other-storage" / "PDF0001" / "paper.pdf"
    other_pdf.parent.mkdir(parents=True)
    other_pdf.write_bytes(b"pdf")
    _use_fake_local_api(
        monkeypatch,
        [_FakeResponse(_api_items(other_pdf))],
    )

    with pytest.raises(RuntimeError, match="different data folder"):
        ZoteroRepository(configured_storage, tmp_path)


@pytest.mark.parametrize(
    "response,match",
    [
        (_FakeResponse({}), "unexpected item payload"),
        (_FakeResponse([{}]), "invalid item record"),
        (_FakeResponse([], api_version="4"), "unsupported API version 4"),
        (_FakeResponse([], version=""), "no Last-Modified-Version"),
        (_FakeResponse([], status_code=500), "returned HTTP 500"),
    ],
)
def test_api_protocol_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
    match: str,
):
    _use_fake_local_api(monkeypatch, [response])

    with pytest.raises(RuntimeError, match=match):
        ZoteroRepository(tmp_path, tmp_path)


def test_api_connection_failure_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FailingSession:
        trust_env = True

        def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            raise zotero_repo.requests.ConnectionError("connection refused")

        def close(self) -> None:
            pass

    monkeypatch.setattr(zotero_repo, "LOCAL_API_BASE_URL", "http://127.0.0.1:23119/api")
    monkeypatch.setattr(zotero_repo.requests, "Session", FailingSession)

    with pytest.raises(RuntimeError, match="Open Zotero"):
        ZoteroRepository(tmp_path, tmp_path)


def test_resolve_attachment_path_storage_prefix(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    repo = _path_repo(storage_dir=storage_dir, data_dir=tmp_path)
    try:
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="storage:document.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = (storage_dir / "abc123" / "document.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


@pytest.mark.parametrize(
    "raw_path,attachment_key",
    [
        ("storage:../outside.pdf", "abc123"),
        ("storage:/absolute.pdf", "abc123"),
        ("storage:C:/absolute.pdf", "abc123"),
        ("storage:document.pdf", "../../outside"),
    ],
)
def test_storage_attachment_paths_cannot_escape_their_directory(
    tmp_path: Path, raw_path: str, attachment_key: str
):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    repo = _path_repo(storage_dir=storage_dir, data_dir=tmp_path)
    try:
        attachment = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key=attachment_key,
            path_raw=raw_path,
            content_type="application/pdf",
            title="Test PDF",
        )
        assert repo.resolve_attachment_path(attachment) is None
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_triple_slash(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    try:
        # Test file:///C:/... format (triple slash, Windows drive URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file:///C:/Users/test/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = _expected_windows_drive_path("Users/test/Documents/file.pdf")
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_double_slash(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    try:
        # Test file://C:/... format (double slash, Windows drive in netloc)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file://C:/Users/test/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = _expected_windows_drive_path("Users/test/Documents/file.pdf")
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_file_url_unc(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    try:
        # Test UNC-style file://server/share/... format
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="file://server/share/Documents/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = Path("//server/share/Documents/file.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_absolute_path(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    try:
        absolute_raw = "/Users/test/Documents/file.pdf"
        if not Path(absolute_raw).is_absolute():
            absolute_raw = "C:/Users/test/Documents/file.pdf"

        # Test absolute path (not a URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw=absolute_raw,
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        assert result == Path(absolute_raw).resolve()
    finally:
        repo.close()


def test_resolve_attachment_path_relative_path(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    repo = _path_repo(storage_dir=tmp_path, data_dir=data_dir)
    try:
        # Test relative path (not a URL)
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="subdir/file.pdf",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        expected = (data_dir / "subdir" / "file.pdf").resolve()
        assert result == expected
    finally:
        repo.close()


def test_resolve_attachment_path_empty_path(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    try:
        # Test empty path
        att = ZoteroAttachment(
            parent_item_id=1,
            parent_key="parent1",
            attachment_key="abc123",
            path_raw="",
            content_type="application/pdf",
            title="Test PDF",
        )
        result = repo.resolve_attachment_path(att)
        assert result is None
    finally:
        repo.close()


def test_missing_non_pdf_attachment_does_not_report_broken_pdf(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)
    item = ZoteroItem(
        item_id=1,
        key="ABC123",
        title="Paper",
        zotero_uri="zotero://select/library/items/ABC123",
        notero_page_url=None,
    )
    repo.list_child_attachments = lambda *_: [
        ZoteroAttachment(
            parent_item_id=1,
            parent_key=item.key,
            attachment_key="LINK",
            path_raw="https://www.notion.so/missing-page",
            content_type="text/html",
            title="Notero link",
        )
    ]
    try:
        status, pdf, message = repo.select_candidate_pdf(item)
    finally:
        repo.close()

    assert status == "NO_PDF"
    assert pdf is None
    assert message == "No valid PDF attachment found"


def test_notero_link_requires_unique_exact_notion_host(tmp_path: Path):
    repo = _path_repo(storage_dir=tmp_path, data_dir=tmp_path)

    def attachment(url: str) -> ZoteroAttachment:
        return ZoteroAttachment(1, "", "LINK", url, "text/html", "Notion")

    try:
        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("https://workspace.notion.so/valid")
        ]
        assert (
            repo.find_notero_page_link_for_parent(1)
            == "https://workspace.notion.so/valid"
        )

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("https://app.notion.com/current")
        ]
        assert (
            repo.find_notero_page_link_for_parent(1) == "https://app.notion.com/current"
        )

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("notion://www.notion.so/current")
        ]
        assert (
            repo.find_notero_page_link_for_parent(1) == "notion://www.notion.so/current"
        )

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("https://notion.so.attacker.example/invalid")
        ]
        assert repo.find_notero_page_link_for_parent(1) is None

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("notion://notion.so.attacker.example/invalid")
        ]
        assert repo.find_notero_page_link_for_parent(1) is None

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            ZoteroAttachment(
                1,
                "",
                "LINK",
                "https://www.notion.so/unrelated",
                "text/html",
                "Research notes",
            )
        ]
        assert repo.find_notero_page_link_for_parent(1) is None

        repo.list_child_attachments = lambda *_args, **_kwargs: [
            attachment("https://notion.so/one"),
            attachment("https://www.notion.so/two"),
        ]
        assert repo.find_notero_page_link_for_parent(1) is None
    finally:
        repo.close()


def test_group_snapshot_reads_only_selected_library(tmp_path, monkeypatch):
    pdf = tmp_path / "storage" / "PDF0001" / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    records = _api_items(pdf)
    for record in records:
        record["library"] = {"type": "group", "id": 123}
    session = _use_fake_local_api(monkeypatch, [_FakeResponse(records)])
    repo = ZoteroRepository(tmp_path / "storage", tmp_path, group_id=123)
    item = repo.all_items()[0]
    assert item.key == "PARENT1"
    assert item.sync_key == "groups/123/PARENT1"
    assert item.zotero_uri == "zotero://select/groups/123/items/PARENT1"
    assert item.notero_page_url is not None
    assert repo.select_candidate_pdf(item)[0] == "OK"
    assert len(session.calls) == 1
    assert "/groups/123/items?" in session.calls[0][0]
    assert session.closed


def test_missing_group_never_falls_back_to_personal_library(tmp_path, monkeypatch):
    session = _use_fake_local_api(monkeypatch, [_FakeResponse({}, status_code=404)])
    with pytest.raises(RuntimeError, match="selected group library is unavailable"):
        ZoteroRepository(tmp_path, tmp_path, group_id=123)
    assert len(session.calls) == 1
    assert "/groups/123/items?" in session.calls[0][0]
    assert session.closed


@pytest.mark.parametrize(
    "library", [None, {"type": "user", "id": 123}, {"type": "group", "id": 456}]
)
def test_group_snapshot_rejects_wrong_library(tmp_path, monkeypatch, library):
    record = _api_parent_record()
    record["library"] = library
    _use_fake_local_api(monkeypatch, [_FakeResponse([record])])
    with pytest.raises(RuntimeError, match="outside the selected group"):
        ZoteroRepository(tmp_path, tmp_path, group_id=123)


@pytest.mark.parametrize("group_id", [0, -1, True, "123", 1.5])
def test_invalid_group_id_is_rejected_before_requests(tmp_path, monkeypatch, group_id):
    session = _use_fake_local_api(monkeypatch, [])
    with pytest.raises(ValueError, match="positive integer"):
        ZoteroRepository(tmp_path, tmp_path, group_id=group_id)
    assert not session.calls


def test_pagination_keeps_parents_and_attachments_together(tmp_path, monkeypatch):
    pdf = tmp_path / "storage" / "PDF0001" / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    records = _api_items(pdf)
    session = _use_fake_local_api(
        monkeypatch,
        [
            _FakeResponse(records[:1], total=3),
            _FakeResponse(records[1:], total=3),
        ],
    )
    repo = ZoteroRepository(tmp_path / "storage", tmp_path)
    assert repo.select_candidate_pdf(repo.all_items()[0])[0] == "OK"
    assert "start=1" in session.calls[1][0]


@pytest.mark.parametrize(
    "second,match",
    [
        (_FakeResponse([], total=2, version="11"), "changed during scanning"),
        (_FakeResponse([], total=3), "changed during scanning"),
        (_FakeResponse([], total=2), "incomplete or inconsistent"),
        (_FakeResponse([_api_parent_record()], total=2), "duplicate item key"),
        (_FakeResponse([], total="invalid"), "Total-Results"),
    ],
)
def test_inconsistent_pagination_fails_closed(tmp_path, monkeypatch, second, match):
    session = _use_fake_local_api(
        monkeypatch,
        [
            _FakeResponse([_api_parent_record()], total=2),
            second,
        ],
    )
    with pytest.raises(RuntimeError, match=match):
        ZoteroRepository(tmp_path, tmp_path)
    assert session.closed


def test_group_discovery_needs_no_library_version(monkeypatch):
    session = _use_fake_local_api(
        monkeypatch,
        [
            _FakeResponse(
                [
                    {"data": {"id": 456, "name": "Second group"}},
                    {"data": {"id": 123, "name": "First group"}},
                ],
                version="",
            )
        ],
    )
    assert ZoteroRepository.list_libraries() == [
        ZoteroLibrary(),
        ZoteroLibrary(123, "First group"),
        ZoteroLibrary(456, "Second group"),
    ]
    assert session.calls[0][0].endswith("/users/0/groups")
    assert session.trust_env is False
    assert session.closed


@pytest.mark.parametrize(
    "records,total",
    [
        ({}, 0),
        ([{"data": {"id": 123, "name": "Group"}}], 2),
        ([{"data": {"id": 0, "name": "Group"}}], 1),
        ([{"data": {"id": True, "name": "Group"}}], 1),
        ([{"data": {"id": 123, "name": ""}}], 1),
        ([{"data": {"id": 123, "name": "Group"}}] * 2, 2),
    ],
)
def test_group_discovery_rejects_ambiguous_responses(monkeypatch, records, total):
    session = _use_fake_local_api(monkeypatch, [_FakeResponse(records, total=total)])
    with pytest.raises(RuntimeError):
        ZoteroRepository.list_libraries()
    assert session.closed


def test_collection_membership_comes_from_same_snapshot(tmp_path, monkeypatch):
    record = _api_parent_record()
    record["data"]["collections"] = ["COLLECT1", "COLLECT2"]
    session = _use_fake_local_api(monkeypatch, [_FakeResponse([record])])
    repo = ZoteroRepository(tmp_path, tmp_path)
    assert repo.all_items()[0].collection_keys == ("COLLECT1", "COLLECT2")
    assert len(session.calls) == 1


@pytest.mark.parametrize("collections", [None, "COLLECT1", [1], ["invalid1"], ["../x"]])
def test_invalid_collection_membership_stops_snapshot(
    tmp_path, monkeypatch, collections
):
    record = _api_parent_record()
    record["data"]["collections"] = collections
    session = _use_fake_local_api(monkeypatch, [_FakeResponse([record])])
    with pytest.raises(RuntimeError, match="invalid collection membership"):
        ZoteroRepository(tmp_path, tmp_path)
    assert session.closed
