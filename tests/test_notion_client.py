import requests

from noteropdf.notion_client import NotionApiError, NotionClient


def test_query_by_property_equals_handles_pagination_for_ambiguity(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    payloads = [
        {
            "results": [{"id": "page-1", "url": "https://notion.so/page-1"}],
            "has_more": True,
            "next_cursor": "cursor-1",
        },
        {
            "results": [{"id": "page-2", "url": "https://notion.so/page-2"}],
            "has_more": False,
            "next_cursor": None,
        },
    ]

    calls = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append((method, path, json_body))
        return payloads.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)

    out = client.query_by_property_equals(
        data_source_id="ds",
        property_name="Zotero URI",
        value="zotero://select/library/items/ABC",
        property_type="rich_text",
    )

    assert len(out) == 2
    assert out[0].page_id == "page-1"
    assert out[1].page_id == "page-2"
    assert calls[0][2]["page_size"] == 100
    assert calls[1][2]["start_cursor"] == "cursor-1"


def test_send_file_bytes_requires_upload_id(tmp_path):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    try:
        try:
            client.send_file_bytes({}, pdf_path)
            assert False, "Expected NotionApiError"
        except NotionApiError as exc:
            assert exc.code == "UPLOAD_FAILED"
            assert "id missing" in str(exc)
    finally:
        client.close()


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_request_maps_validation_errors_to_schema_error(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    def fake_request(*args, **kwargs):
        return _FakeResponse(
            400,
            text="bad request",
            payload={"code": "validation_error", "message": "bad property"},
        )

    monkeypatch.setattr(client._session, "request", fake_request)

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_SCHEMA_ERROR"
        assert "validation failed" in str(exc)
        assert exc.hint is not None
    finally:
        client.close()


def test_request_maps_file_too_large_validation_error(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    def fake_request(*args, **kwargs):
        return _FakeResponse(
            400,
            text="bad request",
            payload={"code": "validation_error", "message": "File too large"},
        )

    monkeypatch.setattr(client._session, "request", fake_request)

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "FILE_TOO_LARGE"
        assert "too large" in str(exc).lower()
        assert exc.hint is not None
    finally:
        client.close()


def test_request_maps_auth_error_to_plain_message(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    monkeypatch.setattr(
        client._session,
        "request",
        lambda *args, **kwargs: _FakeResponse(401, text="unauthorized"),
    )

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_AUTH_ERROR"
        assert "authentication" in str(exc).lower()
        assert exc.hint is not None
    finally:
        client.close()


def test_request_maps_non_json_success_payload_to_api_error(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    monkeypatch.setattr(
        client._session,
        "request",
        lambda *args, **kwargs: _FakeResponse(200, text="ok", payload=None),
    )

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_API_ERROR"
    finally:
        client.close()


def test_send_file_bytes_maps_network_error(tmp_path, monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    def fake_post(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(client._session, "post", fake_post)

    try:
        client.send_file_bytes({"id": "upload-1"}, pdf_path)
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_NETWORK_ERROR"
    finally:
        client.close()


def test_send_file_bytes_maps_auth_http_error(tmp_path, monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: _FakeResponse(401, text="unauthorized"),
    )

    try:
        client.send_file_bytes({"id": "upload-1"}, pdf_path)
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_AUTH_ERROR"
    finally:
        client.close()


def test_send_file_bytes_maps_file_too_large_http_error(tmp_path, monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            400,
            text='{"object":"error","status":400,"code":"validation_error","message":"File too large"}',
        ),
    )

    try:
        client.send_file_bytes({"id": "upload-1"}, pdf_path)
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "FILE_TOO_LARGE"
        assert "upload size" in str(exc).lower()
    finally:
        client.close()


def test_resolve_target_ids_requires_explicit_target():
    client = NotionClient(token="x", notion_version="2026-03-11")

    try:
        client.resolve_target_ids("", "")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_SCHEMA_ERROR"
        assert "explicitly" in str(exc).lower()
    finally:
        client.close()


def test_resolve_target_ids_uses_database_and_single_data_source(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "data_sources": [{"id": "cc60e681-3c44-83c3-a31e-878c0824d6ac"}]
        },
    )

    database_id, data_source_id = client.resolve_target_ids(
        "3180e681-3c44-8198-9a97-e4532809e30e", ""
    )

    assert database_id == "3180e681-3c44-8198-9a97-e4532809e30e"
    assert data_source_id == "cc60e681-3c44-83c3-a31e-878c0824d6ac"


def test_resolve_data_source_id_rejects_multiple_matches(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "data_sources": [{"id": "a"}, {"id": "b"}]
        },
    )

    try:
        client.resolve_data_source_id("3180e681-3c44-8198-9a97-e4532809e30e", "")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_SCHEMA_ERROR"
        assert "set notion.data_source_id explicitly" in str(exc)
    finally:
        client.close()


def test_list_accessible_data_sources_returns_sorted_targets(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "results": [
                {
                    "object": "data_source",
                    "id": "b",
                    "title": [{"plain_text": "Beta"}],
                    "parent": {"database_id": "db-b"},
                    "url": "https://www.notion.so/beta",
                },
                {
                    "object": "data_source",
                    "id": "a",
                    "title": [{"plain_text": "Alpha"}],
                    "parent": {"database_id": "db-a"},
                    "url": "https://www.notion.so/alpha",
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )

    targets = client.list_accessible_data_sources()

    assert [target.label for target in targets] == ["Alpha", "Beta"]
    assert targets[0].data_source_id == "a"
    assert targets[0].database_id == "db-a"


def test_create_file_upload_uses_single_part_mode(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    calls = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append((method, path, json_body))
        return {"id": "upload-1"}

    monkeypatch.setattr(client, "_request", fake_request)

    client.create_file_upload("sample.pdf", "application/pdf", file_size=1024)

    assert calls[0][2]["mode"] == "single_part"
    assert "number_of_parts" not in calls[0][2]


def test_create_file_upload_uses_multi_part_mode(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    calls = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append((method, path, json_body))
        return {"id": "upload-1"}

    monkeypatch.setattr(client, "_request", fake_request)

    client.create_file_upload(
        "large.pdf",
        "application/pdf",
        file_size=(NotionClient.MULTIPART_THRESHOLD_BYTES + 1),
    )

    assert calls[0][2]["mode"] == "multi_part"
    assert calls[0][2]["number_of_parts"] == 2


def test_send_file_bytes_completes_multi_part_upload(tmp_path, monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "large.pdf"
    pdf_path.write_bytes(b"a" * (NotionClient.MULTIPART_THRESHOLD_BYTES + 5))

    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _FakeResponse(200, text="ok", payload={})

    monkeypatch.setattr(client._session, "post", fake_post)

    upload_id = client.send_file_bytes(
        {
            "id": "upload-1",
            "upload_url": "https://upload.example/send",
            "complete_url": "https://upload.example/complete",
        },
        pdf_path,
    )

    assert upload_id == "upload-1"
    send_calls = [call for call in calls if call["url"] == "https://upload.example/send"]
    assert len(send_calls) == 2
    assert send_calls[0]["data"] == {"part_number": "1"}
    assert send_calls[1]["data"] == {"part_number": "2"}
    assert send_calls[0]["headers"]["Authorization"] == "Bearer x"
    assert len(send_calls[0]["files"]["file"][1]) == NotionClient.MULTIPART_THRESHOLD_BYTES
    assert len(send_calls[1]["files"]["file"][1]) == 5
    assert calls[-1]["url"] == "https://upload.example/complete"
    assert calls[-1]["json"] == {}


def test_send_file_bytes_completes_multi_part_upload_without_complete_url(
    tmp_path, monkeypatch
):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pdf_path = tmp_path / "large.pdf"
    pdf_path.write_bytes(b"a" * (NotionClient.MULTIPART_THRESHOLD_BYTES + 1))

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _FakeResponse(200, text="ok", payload={})

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: calls.append(f"{method}:{path}") or {},
    )

    upload_id = client.send_file_bytes({"id": "upload-1"}, pdf_path)

    assert upload_id == "upload-1"
    assert calls.count("https://api.notion.com/v1/file_uploads/upload-1/send") == 2
    assert "POST:/file_uploads/upload-1/complete" in calls


def test_complete_file_upload_maps_completion_failure(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: _FakeResponse(429, text="rate limited"),
    )

    try:
        client.complete_file_upload("upload-1", complete_url="https://upload.example/complete")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_RATE_LIMIT"
    finally:
        client.close()


def test_workspace_upload_limit_is_exposed_from_bot_payload(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "bot": {
                "workspace_limits": {"max_file_upload_size_in_bytes": 123456789}
            }
        },
    )

    assert client.get_workspace_upload_limit_bytes() == 123456789
