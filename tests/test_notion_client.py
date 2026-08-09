import requests

from noteropdf.notion_client import NotionApiError, NotionClient, NotionProperty


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


def test_request_retries_conflict_and_honors_retry_after(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=2)
    responses = [
        _FakeResponse(409, text="conflict"),
        _FakeResponse(200, text='{"ok": true}', payload={"ok": True}),
    ]
    responses[0].headers["Retry-After"] = "0"
    sleeps = []
    monkeypatch.setattr(
        client._session, "request", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr("noteropdf.notion_client.time.sleep", sleeps.append)

    assert client._request("GET", "/users/me") == {"ok": True}
    assert sleeps == [0.5]


def test_request_reports_expired_personal_access_token_without_retry(monkeypatch):
    seen_authorization = []

    def fake_request(*args, **kwargs):
        seen_authorization.append(kwargs["headers"]["Authorization"])
        return _FakeResponse(401, text="expired")

    client = NotionClient(
        token="old",
        notion_version="2026-03-11",
    )
    monkeypatch.setattr(client._session, "request", fake_request)

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_AUTH_ERROR"
        assert "connect" in (exc.hint or "")
    assert seen_authorization == ["Bearer old"]


def test_request_reports_permission_failure(monkeypatch):
    client = NotionClient(
        token="valid-but-forbidden",
        notion_version="2026-03-11",
    )
    monkeypatch.setattr(
        client._session,
        "request",
        lambda *args, **kwargs: _FakeResponse(403, text="forbidden"),
    )

    try:
        client._request("GET", "/users/me")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_AUTH_ERROR"


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
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)
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


def test_list_accessible_data_sources_returns_sorted_targets(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json_body")))
        return {
            "results": [
                {
                    "object": "data_source",
                    "id": "b",
                    "title": [{"plain_text": "Beta"}],
                    "url": "https://www.notion.so/beta",
                },
                {
                    "object": "data_source",
                    "id": "a",
                    "title": [{"plain_text": "Alpha"}],
                    "url": "https://www.notion.so/alpha",
                },
            ],
            "has_more": False,
            "next_cursor": None,
        }

    monkeypatch.setattr(client, "_request", fake_request)

    targets = client.list_accessible_data_sources()

    assert [target.label for target in targets] == ["Alpha", "Beta"]
    assert targets[0].data_source_id == "a"
    assert targets[0].url == "https://www.notion.so/alpha"
    assert calls[0][2]["filter"] == {
        "property": "object",
        "value": "data_source",
    }


def test_list_data_source_pages_paginates_and_skips_trashed(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    payloads = [
        {
            "results": [
                {"object": "page", "id": "page-1", "url": "https://notion.so/page-1"},
                {
                    "object": "page",
                    "id": "page-2",
                    "url": "https://notion.so/page-2",
                    "in_trash": True,
                },
            ],
            "has_more": True,
            "next_cursor": "cursor-1",
        },
        {
            "results": [
                {"object": "data_source", "id": "ds-ignored"},
                {"object": "page", "id": "page-3", "url": "https://notion.so/page-3"},
            ],
            "has_more": False,
            "next_cursor": None,
        },
    ]
    calls = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append((method, path, json_body))
        return payloads.pop(0)

    monkeypatch.setattr(client, "_request", fake_request)

    pages = client.list_data_source_pages("ds-1", property_ids=("pdf-id", "uri-id"))

    assert [page["id"] for page in pages] == ["page-1", "page-3"]
    assert calls[0][2]["page_size"] == 100
    assert calls[0][2]["result_type"] == "page"
    assert "filter_properties%5B%5D=pdf-id" in calls[0][1]
    assert "filter_properties%5B%5D=uri-id" in calls[0][1]
    assert calls[1][2]["start_cursor"] == "cursor-1"


def test_list_data_source_pages_rejects_an_incomplete_query(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: {
            "results": [],
            "has_more": False,
            "request_status": {
                "type": "incomplete",
                "incomplete_reason": "query_result_limit_reached",
            },
        },
    )

    try:
        client.list_data_source_pages("too-large")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_SCHEMA_ERROR"
        assert "10,000" in str(exc)


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
    send_calls = [
        call for call in calls if call["url"] == "https://upload.example/send"
    ]
    assert len(send_calls) == 2
    assert send_calls[0]["data"] == {"part_number": "1"}
    assert send_calls[1]["data"] == {"part_number": "2"}
    assert send_calls[0]["headers"]["Authorization"] == "Bearer x"
    assert (
        len(send_calls[0]["files"]["file"][1]) == NotionClient.MULTIPART_THRESHOLD_BYTES
    )
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
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: _FakeResponse(429, text="rate limited"),
    )

    try:
        client.complete_file_upload(
            "upload-1", complete_url="https://upload.example/complete"
        )
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert exc.code == "NOTION_RATE_LIMIT"
    finally:
        client.close()


def test_upload_does_not_retry_expired_personal_access_token(monkeypatch):
    authorizations = []

    def send():
        authorizations.append(client._headers["Authorization"])
        return _FakeResponse(401, text="expired")

    client = NotionClient(
        token="old",
        notion_version="2026-03-11",
        max_retries=3,
    )
    sleeps = []
    monkeypatch.setattr("noteropdf.notion_client.time.sleep", sleeps.append)

    response = client._send_upload_request(send)

    assert response.status_code == 401
    assert authorizations == ["Bearer old"]
    assert sleeps == []


def test_upload_returns_permission_failure():
    client = NotionClient(
        token="valid-but-forbidden",
        notion_version="2026-03-11",
    )

    response = client._send_upload_request(lambda: _FakeResponse(403, text="forbidden"))

    assert response.status_code == 403


def test_workspace_upload_limit_is_exposed_from_bot_payload(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {
            "bot": {"workspace_limits": {"max_file_upload_size_in_bytes": 123456789}}
        },
    )

    assert client.get_workspace_upload_limit_bytes() == 123456789


def test_files_property_signature_preserves_ordered_remote_identity():
    page = {
        "properties": {
            "PDF": {
                "id": "pdf-id",
                "type": "files",
                "files": [
                    {
                        "name": "first.pdf",
                        "type": "file_upload",
                        "file_upload": {"id": "upload-1"},
                    },
                    {
                        "name": "second.pdf",
                        "type": "external",
                        "external": {"url": "https://example.com/second.pdf"},
                    },
                ],
            }
        }
    }

    assert NotionClient.files_property_signature(page, "pdf-id") == (
        ("first.pdf", "file_upload", "upload-1"),
        ("second.pdf", "external", "https://example.com/second.pdf"),
    )


def test_files_property_signature_ignores_expiring_hosted_file_query_strings():
    def page(signature: str):
        return {
            "properties": {
                "PDF": {
                    "id": "pdf-id",
                    "type": "files",
                    "files": [
                        {
                            "name": "paper.pdf",
                            "type": "file",
                            "file": {
                                "url": "https://files.notion.test/object/paper.pdf"
                                f"?signature={signature}"
                            },
                        }
                    ],
                }
            }
        }

    assert NotionClient.files_property_signature(
        page("old"), "pdf-id"
    ) == NotionClient.files_property_signature(page("new"), "pdf-id")


def test_find_files_property_requires_the_exact_dedicated_name(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "properties": {
                "NoteroPDF PDF": {"id": "pdf-id", "type": "files"},
                "Other files": {"id": "other-id", "type": "files"},
            }
        },
    )

    found = client.find_files_property("ds", "NoteroPDF PDF")

    assert found is not None and found.id == "pdf-id"
    assert client.find_files_property("ds", "noteropdf pdf") is None


def test_find_files_property_does_not_adopt_arbitrary_files_property(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "properties": {"Attachments": {"id": "files-id", "type": "files"}}
        },
    )

    assert client.find_files_property("ds", "NoteroPDF PDF") is None


def test_create_files_property_invalidates_schema_cache(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    schemas = [
        {"properties": {}},
        {"properties": {"NoteroPDF PDF": {"id": "pdf-id", "type": "files"}}},
    ]
    calls = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append((method, path, json_body))
        if method == "GET":
            return schemas.pop(0)
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.get_data_source_schema("ds")

    prop = client.create_files_property("ds")

    assert prop.id == "pdf-id"
    assert (
        "PATCH",
        "/data_sources/ds",
        {"properties": {"NoteroPDF PDF": {"files": {}}}},
    ) in calls


def test_create_files_property_refuses_same_name_non_files_collision(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    monkeypatch.setattr(
        client,
        "list_data_source_properties",
        lambda _data_source_id: (
            NotionProperty("existing-id", "NoteroPDF PDF", "rich_text"),
        ),
    )
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("schema must not be changed")
        ),
    )

    try:
        client.create_files_property("ds")
        assert False, "Expected NotionApiError"
    except NotionApiError as exc:
        assert "will not change its type" in str(exc)


def test_snapshot_indexes_page_id_and_requests_only_pdf_property(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11")
    pages = [
        {
            "object": "page",
            "id": "11111111-1111-1111-1111-111111111111",
            "url": "https://notion.so/one",
            "properties": {
                "NoteroPDF PDF": {"id": "pdf-id", "type": "files", "files": []},
            },
        },
        {
            "object": "page",
            "id": "page-2",
            "url": "https://notion.so/two",
            "properties": {},
        },
    ]
    calls = []

    def list_pages(*args, **kwargs):
        calls.append((args, kwargs))
        return pages

    monkeypatch.setattr(client, "list_data_source_pages", list_pages)

    snapshot = client.build_data_source_snapshot("ds", pdf_property="pdf-id")

    assert snapshot.get_page("11111111111111111111111111111111") is pages[0]
    assert calls == [(("ds",), {"property_ids": ("pdf-id",)})]
