from noteropdf.notion_client import NotionClient
from noteropdf.notion_client import NotionApiError
import requests


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


def test_request_maps_auth_error_to_plain_message(monkeypatch):
    client = NotionClient(token="x", notion_version="2026-03-11", max_retries=1)

    monkeypatch.setattr(client._session, "request", lambda *args, **kwargs: _FakeResponse(401, text="unauthorized"))

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

    monkeypatch.setattr(client._session, "request", lambda *args, **kwargs: _FakeResponse(200, text="ok", payload=None))

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
