from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


class NotionApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int | None = None,
        hint: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.hint = hint


@dataclass(frozen=True)
class NotionMatch:
    page_id: str
    page_url: str | None


class NotionClient:
    def __init__(self, token: str, notion_version: str, max_retries: int = 5):
        self._session = requests.Session()
        self._base = "https://api.notion.com/v1"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": notion_version,
            "Content-Type": "application/json",
        }
        self._max_retries = max_retries
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self._session.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        raw_data: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        headers = dict(self._headers)
        if extra_headers:
            headers.update(extra_headers)

        attempt = 0
        while True:
            attempt += 1
            try:
                if raw_data is not None:
                    resp = self._session.request(
                        method, url, headers=headers, data=raw_data, timeout=60
                    )
                else:
                    resp = self._session.request(
                        method, url, headers=headers, json=json_body, timeout=60
                    )
            except requests.RequestException as exc:
                if attempt >= self._max_retries:
                    raise NotionApiError(
                        "NOTION_NETWORK_ERROR",
                        f"Network error contacting Notion: {exc}",
                        hint="Check your internet connection and try again.",
                    ) from exc
                time.sleep(min(2.0 * attempt, 8.0))
                continue

            if resp.status_code == 429:
                if attempt >= self._max_retries:
                    raise NotionApiError(
                        "NOTION_RATE_LIMIT",
                        "Notion rate-limited requests too many times.",
                        429,
                        hint="Wait a little and run the command again.",
                    )
                retry_after = resp.headers.get("Retry-After", "1")
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 1.0
                time.sleep(max(0.5, delay))
                continue

            if resp.status_code >= 500:
                if attempt >= self._max_retries:
                    raise NotionApiError(
                        "NOTION_API_ERROR",
                        f"Notion server error: {resp.status_code}",
                        resp.status_code,
                        hint="Notion is temporarily unavailable. Try again later.",
                    )
                time.sleep(min(2.0 * attempt, 8.0))
                continue

            if allow_404 and resp.status_code == 404:
                return {"_not_found": True}

            if resp.status_code in (401, 403):
                raise NotionApiError(
                    "NOTION_AUTH_ERROR",
                    "Notion rejected authentication for this request.",
                    resp.status_code,
                    hint="Check your Notion token and confirm the integration has access to the target database.",
                )

            if resp.status_code >= 400:
                raise self._map_error_response(resp)

            if not resp.text:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise NotionApiError(
                    "NOTION_API_ERROR",
                    "Notion returned an unexpected non-JSON response.",
                    resp.status_code,
                    hint="Retry the command. If this keeps happening, check logs and Notion status.",
                ) from exc

    def _map_error_response(self, resp: requests.Response) -> NotionApiError:
        message = (resp.text or "").strip()
        api_code = ""
        try:
            payload = resp.json()
            message = str(payload.get("message", message))
            api_code = str(payload.get("code", "")).strip().lower()
        except Exception:
            payload = None

        if resp.status_code == 400 and api_code in {
            "validation_error",
            "invalid_json",
            "invalid_request",
        }:
            return NotionApiError(
                "NOTION_SCHEMA_ERROR",
                f"Notion request validation failed: {message}",
                resp.status_code,
                hint="Check property names and types in config.yaml, then run doctor again.",
            )
        if resp.status_code == 404:
            return NotionApiError(
                "NOTION_SCHEMA_ERROR",
                f"Notion resource was not found: {message}",
                resp.status_code,
                hint="Verify your database/data source IDs and that the integration has access.",
            )
        if resp.status_code in (408,):
            return NotionApiError(
                "NOTION_NETWORK_ERROR",
                "Timed out waiting for Notion.",
                resp.status_code,
                hint="Try again. If this repeats, reduce run size or check your network.",
            )
        if resp.status_code == 413:
            return NotionApiError(
                "FILE_TOO_LARGE",
                "Notion rejected the file because it is too large.",
                resp.status_code,
                hint="Lower file size or increase local size limits only if Notion supports that file size.",
            )

        return NotionApiError(
            "NOTION_API_ERROR",
            message or f"Notion API error ({resp.status_code})",
            resp.status_code,
            hint="Check the logs and retry the command.",
        )

    def ping(self) -> None:
        self._request("GET", "/users/me")

    def resolve_data_source_id(
        self, database_id: str, configured_data_source_id: str
    ) -> str:
        if configured_data_source_id:
            return configured_data_source_id

        payload = self._request("GET", f"/databases/{database_id}")
        data_sources = payload.get("data_sources") or []
        if len(data_sources) != 1:
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR",
                "Unable to deterministically resolve data source ID from database. Please set notion.data_source_id explicitly.",
            )
        ds_id = data_sources[0].get("id", "")
        if not ds_id:
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR", "Resolved data source has no id"
            )
        return ds_id

    def get_data_source_schema(self, data_source_id: str) -> dict[str, Any]:
        cached = self._schema_cache.get(data_source_id)
        if cached is not None:
            return cached
        schema = self._request("GET", f"/data_sources/{data_source_id}")
        self._schema_cache[data_source_id] = schema
        return schema

    def validate_pdf_property(self, data_source_id: str, property_name: str) -> None:
        schema = self.get_data_source_schema(data_source_id)
        props = schema.get("properties") or {}
        if property_name not in props:
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR",
                f"Missing required Notion property: {property_name}",
            )
        ptype = props[property_name].get("type")
        if ptype != "files":
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR",
                f"Property '{property_name}' exists but is not type 'files'",
            )

    def has_property(self, data_source_id: str, property_name: str) -> bool:
        schema = self.get_data_source_schema(data_source_id)
        props = schema.get("properties") or {}
        return property_name in props

    def get_property_type(self, data_source_id: str, property_name: str) -> str | None:
        schema = self.get_data_source_schema(data_source_id)
        props = schema.get("properties") or {}
        if property_name not in props:
            return None
        return props[property_name].get("type")

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        payload = self._request("GET", f"/pages/{page_id}", allow_404=True)
        if payload.get("_not_found"):
            return None
        return payload

    def query_by_property_equals(
        self,
        data_source_id: str,
        property_name: str,
        value: str,
        property_type: str,
    ) -> list[NotionMatch]:
        if property_type == "url":
            condition = {"url": {"equals": value}}
        elif property_type == "title":
            condition = {"title": {"equals": value}}
        else:
            condition = {"rich_text": {"equals": value}}

        body = {
            "filter": {
                "property": property_name,
                **condition,
            },
            "page_size": 100,
        }

        matches: list[NotionMatch] = []
        cursor: str | None = None
        while True:
            query_body = dict(body)
            if cursor:
                query_body["start_cursor"] = cursor
            payload = self._request(
                "POST", f"/data_sources/{data_source_id}/query", json_body=query_body
            )
            matches.extend(self._extract_matches(payload))
            if len(matches) > 1:
                return matches
            if not payload.get("has_more"):
                return matches
            cursor = payload.get("next_cursor")
            if not cursor:
                return matches

    def query_by_doi(
        self,
        data_source_id: str,
        doi_property_name: str,
        doi: str,
        property_type: str,
    ) -> list[NotionMatch]:
        return self.query_by_property_equals(
            data_source_id=data_source_id,
            property_name=doi_property_name,
            value=doi,
            property_type=property_type,
        )

    def _extract_matches(self, payload: dict[str, Any]) -> list[NotionMatch]:
        out: list[NotionMatch] = []
        for row in payload.get("results") or []:
            out.append(NotionMatch(page_id=row.get("id", ""), page_url=row.get("url")))
        out = [x for x in out if x.page_id]
        return out

    def page_files_count(self, page_id: str, property_name: str) -> int:
        page = self.get_page(page_id)
        if not page:
            return 0
        props = page.get("properties") or {}
        prop = props.get(property_name) or {}
        files = prop.get("files") or []
        return len(files)

    def clear_page_files(self, page_id: str, property_name: str) -> None:
        body = {
            "properties": {
                property_name: {
                    "files": [],
                }
            }
        }
        self._request("PATCH", f"/pages/{page_id}", json_body=body)

    def create_file_upload(
        self, filename: str, content_type: str, file_size: int
    ) -> dict[str, Any]:
        body = {
            "filename": filename,
            "content_type": content_type,
            "file_size": file_size,
            "upload_mode": "single_part",
        }
        return self._request("POST", "/file_uploads", json_body=body)

    def send_file_bytes(self, create_payload: dict[str, Any], pdf_path: Path) -> str:
        upload_obj = create_payload.get("file_upload") or create_payload
        upload_id = upload_obj.get("id") or create_payload.get("id")
        if not isinstance(upload_id, str) or not upload_id.strip():
            raise NotionApiError(
                "UPLOAD_FAILED", "File upload id missing from create response"
            )
        upload_id = upload_id.strip()

        size_bytes = pdf_path.stat().st_size
        size_mb = max(1, int(size_bytes / (1024 * 1024)))
        upload_timeout = max(120, 60 + (size_mb * 15))

        upload_url = upload_obj.get("upload_url") or create_payload.get("upload_url")
        if upload_url:
            # Notion expects multipart upload for the /send endpoint.
            try:
                with pdf_path.open("rb") as f:
                    files = {
                        "file": (pdf_path.name, f, "application/pdf"),
                    }
                    headers = {
                        "Authorization": self._headers["Authorization"],
                        "Notion-Version": self._headers["Notion-Version"],
                    }
                    resp = self._session.post(
                        upload_url, headers=headers, files=files, timeout=upload_timeout
                    )
            except requests.RequestException as exc:
                raise NotionApiError(
                    "NOTION_NETWORK_ERROR",
                    f"Network error while uploading file bytes: {exc}",
                    hint="Check your connection and retry sync.",
                ) from exc
            if resp.status_code >= 400:
                self._raise_upload_http_error(resp)
            return upload_id

        try:
            with pdf_path.open("rb") as f:
                files = {
                    "file": (pdf_path.name, f, "application/pdf"),
                }
                resp = self._session.post(
                    f"{self._base}/file_uploads/{upload_id}/send",
                    headers={
                        "Authorization": self._headers["Authorization"],
                        "Notion-Version": self._headers["Notion-Version"],
                    },
                    files=files,
                    timeout=upload_timeout,
                )
        except requests.RequestException as exc:
            raise NotionApiError(
                "NOTION_NETWORK_ERROR",
                f"Network error while uploading file bytes: {exc}",
                hint="Check your connection and retry sync.",
            ) from exc
        if resp.status_code >= 400:
            self._raise_upload_http_error(resp)
        return upload_id

    def _raise_upload_http_error(self, resp: requests.Response) -> None:
        detail = (resp.text or "").strip().replace("\n", " ")
        if resp.status_code in (401, 403):
            raise NotionApiError(
                "NOTION_AUTH_ERROR",
                f"Upload rejected by Notion auth/permissions: {resp.status_code} {detail}",
                resp.status_code,
                hint="Verify token permissions and integration access to the target database.",
            )
        if resp.status_code == 413:
            raise NotionApiError(
                "FILE_TOO_LARGE",
                f"Notion rejected upload size: {resp.status_code} {detail}",
                resp.status_code,
                hint="Use a smaller PDF, or lower local size limits to skip unsupported files earlier.",
            )
        if resp.status_code == 429:
            raise NotionApiError(
                "NOTION_RATE_LIMIT",
                f"Notion rate-limited upload: {resp.status_code} {detail}",
                resp.status_code,
                hint="Wait and rerun sync.",
            )
        if resp.status_code >= 500:
            raise NotionApiError(
                "NOTION_API_ERROR",
                f"Notion server upload error: {resp.status_code} {detail}",
                resp.status_code,
                hint="Retry later when Notion service is stable.",
            )
        raise NotionApiError(
            "UPLOAD_FAILED",
            f"Upload byte transfer failed: {resp.status_code} {detail}",
            resp.status_code,
            hint="Retry sync and inspect logs if this repeats.",
        )

    def attach_file_upload_to_page(
        self, page_id: str, property_name: str, upload_id: str, filename: str
    ) -> None:
        safe_name = filename
        if len(safe_name) > 100:
            p = Path(safe_name)
            stem = p.stem
            suffix = p.suffix
            keep = max(1, 100 - len(suffix))
            safe_name = stem[:keep] + suffix

        body = {
            "properties": {
                property_name: {
                    "files": [
                        {
                            "name": safe_name,
                            "type": "file_upload",
                            "file_upload": {
                                "id": upload_id,
                            },
                        }
                    ]
                }
            }
        }
        self._request("PATCH", f"/pages/{page_id}", json_body=body)
