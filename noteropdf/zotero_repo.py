from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from .models import CandidatePdf, ZoteroAttachment, ZoteroItem

LOCAL_API_BASE_URL = "http://127.0.0.1:23119/api"
_LOCAL_API_TIMEOUT = (0.5, 10.0)


class _LocalApiUnavailable(RuntimeError):
    """The Zotero local API cannot provide the configured library."""


class _LocalApiProtocolError(RuntimeError):
    """The running local API answered but its response is unsafe to use."""


class ZoteroRepository:
    def __init__(self, storage_dir: Path, data_dir: Path):
        self._storage_dir = storage_dir.expanduser().resolve()
        self._data_dir = data_dir.expanduser().resolve()
        self._api_parent_items: list[ZoteroItem] = []
        self._api_attachments: dict[int, list[ZoteroAttachment]] = {}

        session = requests.Session()
        # Local discovery must never be sent through a user-configured proxy.
        session.trust_env = False
        try:
            self._load_local_api_snapshot(session)
        except _LocalApiUnavailable as exc:
            raise RuntimeError(
                "Could not read Zotero through its local API. Open Zotero, then "
                "enable ‘Allow other applications on this computer to communicate "
                f"with Zotero’ in Settings > Advanced. Details: {exc}"
            ) from exc
        finally:
            session.close()

    def close(self) -> None:
        """Release repository resources (the API snapshot is already in memory)."""

    @staticmethod
    def _validate_response_version(response: requests.Response) -> None:
        api_version = (response.headers.get("Zotero-API-Version") or "").strip()
        if api_version != "3":
            raise _LocalApiProtocolError(
                "Zotero local API compatibility check failed: unsupported API "
                f"version {api_version or 'unknown'}."
            )
        version = (response.headers.get("Last-Modified-Version") or "").strip()
        if not version:
            raise _LocalApiProtocolError(
                "Zotero local API compatibility check failed: the response has "
                "no Last-Modified-Version."
            )

    @staticmethod
    def _response_json(response: requests.Response) -> Any:
        try:
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise _LocalApiProtocolError(
                "Zotero local API returned invalid JSON."
            ) from exc

    def _api_get(
        self,
        session: requests.Session,
        path: str,
    ) -> Any:
        try:
            response = session.get(
                f"{LOCAL_API_BASE_URL}{path}",
                headers={
                    "Accept": "application/json",
                    "Zotero-API-Version": "3",
                },
                timeout=_LOCAL_API_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise _LocalApiUnavailable(str(exc) or "connection failed") from exc
        if response.status_code == 403:
            raise _LocalApiUnavailable("the local API is disabled in Zotero")
        if response.status_code != 200:
            raise _LocalApiProtocolError(
                f"Zotero local API returned HTTP {response.status_code}."
            )
        self._validate_response_version(response)
        return self._response_json(response)

    def _load_local_api_snapshot(self, session: requests.Session) -> None:
        items_raw = self._api_get(session, "/users/0/items")
        if not isinstance(items_raw, list):
            raise _LocalApiProtocolError(
                "Zotero local API returned an unexpected item payload."
            )

        parents, attachments = self._build_api_snapshot(items_raw)
        self._api_parent_items = parents
        self._api_attachments = attachments

    @staticmethod
    def _api_data(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict) or not isinstance(record.get("data"), dict):
            raise _LocalApiProtocolError(
                "Zotero local API returned an invalid item record."
            )
        return record["data"]

    @staticmethod
    def _api_link(record: dict[str, Any], name: str) -> str | None:
        links = record.get("links")
        if not isinstance(links, dict):
            return None
        link = links.get(name)
        if not isinstance(link, dict):
            return None
        href = link.get("href")
        return str(href).strip() if href else None

    def _validated_imported_enclosure(
        self, record: dict[str, Any], *, attachment_key: str
    ) -> str | None:
        href = self._api_link(record, "enclosure")
        if href is None:
            return None
        path = self._file_url_path(href)
        if path is None:
            raise _LocalApiProtocolError(
                "Zotero local API returned an invalid imported PDF path."
            )
        attachment_root = (self._storage_dir / attachment_key).resolve()
        if not attachment_root.is_relative_to(self._storage_dir):
            raise _LocalApiProtocolError(
                "Zotero local API returned an unsafe attachment key."
            )
        if not path.is_relative_to(self._storage_dir):
            raise _LocalApiUnavailable(
                "the running Zotero instance uses a different data folder than configured"
            )
        if not path.is_relative_to(attachment_root):
            raise _LocalApiProtocolError(
                "Zotero local API returned an unsafe imported attachment path."
            )
        return href

    def _build_api_snapshot(
        self, items_raw: list[Any]
    ) -> tuple[list[ZoteroItem], dict[int, list[ZoteroAttachment]]]:
        parent_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
        attachment_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw in items_raw:
            data = self._api_data(raw)
            item_type = str(data.get("itemType") or "")
            if item_type == "attachment":
                attachment_records.append((raw, data))
                continue
            if item_type in {"note", "annotation"}:
                continue
            parent_records.append((raw, data))

        id_by_key: dict[str, int] = {}
        for item_id, (_record, data) in enumerate(parent_records, start=1):
            key = str(data.get("key") or "").strip()
            if not key or key in id_by_key:
                raise _LocalApiProtocolError(
                    "Zotero local API returned an invalid or duplicate item key."
                )
            id_by_key[key] = item_id

        attachments: dict[int, list[ZoteroAttachment]] = {
            item_id: [] for item_id in id_by_key.values()
        }
        for record, data in attachment_records:
            parent_key = str(data.get("parentItem") or "").strip()
            parent_id = id_by_key.get(parent_key)
            if parent_id is None:
                continue
            attachment_key = str(data.get("key") or "").strip()
            if not attachment_key:
                raise _LocalApiProtocolError(
                    "Zotero local API returned an attachment without a key."
                )
            link_mode = str(data.get("linkMode") or "")
            content_type = str(data.get("contentType") or "") or None
            if link_mode == "linked_url":
                path_raw = str(data.get("url") or "").strip() or None
            elif link_mode.startswith("imported"):
                path_raw = self._validated_imported_enclosure(
                    record, attachment_key=attachment_key
                )
                if path_raw is None:
                    filename = str(data.get("filename") or "").strip()
                    path_raw = f"storage:{filename}" if filename else None
            else:
                path_raw = self._api_link(record, "enclosure")
                if path_raw is None:
                    path_raw = str(data.get("path") or "").strip() or None
            attachments[parent_id].append(
                ZoteroAttachment(
                    parent_item_id=parent_id,
                    parent_key=parent_key,
                    attachment_key=attachment_key,
                    path_raw=path_raw,
                    content_type=content_type,
                    title=str(data.get("title") or "").strip() or None,
                )
            )

        parents: list[ZoteroItem] = []
        for _record, data in parent_records:
            key = str(data["key"])
            item_id = id_by_key[key]
            parents.append(
                ZoteroItem(
                    item_id=item_id,
                    key=key,
                    title=str(data.get("title") or "").strip() or None,
                    zotero_uri=f"zotero://select/library/items/{key}",
                    notero_page_url=self._find_notero_link(attachments[item_id]),
                )
            )
        return parents, attachments

    def list_parent_items(self) -> list[ZoteroItem]:
        return list(self._api_parent_items)

    def list_child_attachments(
        self, parent_item_id: int, parent_key: str
    ) -> list[ZoteroAttachment]:
        return list(self._api_attachments.get(parent_item_id, ()))

    def find_notero_page_link_for_parent(self, parent_item_id: int) -> str | None:
        return self._find_notero_link(
            self.list_child_attachments(parent_item_id, parent_key="")
        )

    @staticmethod
    def _find_notero_link(attachments: list[ZoteroAttachment]) -> str | None:
        matches: set[str] = set()
        for att in attachments:
            # Notero creates a link attachment named exactly "Notion". Requiring
            # that marker avoids treating an unrelated Notion bookmark as a match.
            if (att.title or "").strip().casefold() != "notion":
                continue
            raw = (att.path_raw or "").strip()
            if not raw:
                continue
            try:
                parsed = urllib.parse.urlparse(raw)
            except ValueError:
                continue
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme.lower() not in {"http", "https", "notion"}:
                continue
            if (
                hostname == "notion.so"
                or hostname.endswith(".notion.so")
                or hostname == "app.notion.com"
            ):
                matches.add(raw)
        return next(iter(matches)) if len(matches) == 1 else None

    def resolve_attachment_path(self, att: ZoteroAttachment) -> Path | None:
        raw = (att.path_raw or "").strip()
        if not raw:
            return None

        if raw.startswith("storage:"):
            suffix = raw.split(":", 1)[1]
            if (
                not suffix
                or suffix.startswith(("/", "\\"))
                or re.match(r"^[A-Za-z]:", suffix)
            ):
                return None
            storage_root = self._storage_dir.resolve()
            attachment_root = (storage_root / att.attachment_key).resolve()
            candidate = (attachment_root / suffix).resolve()
            if not attachment_root.is_relative_to(storage_root):
                return None
            if not candidate.is_relative_to(attachment_root):
                return None
            return candidate

        if raw.startswith("file://"):
            return self._file_url_path(raw)

        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()

        return (self._data_dir / p).resolve()

    @staticmethod
    def _file_url_path(raw: str) -> Path | None:
        try:
            parsed = urllib.parse.urlparse(raw)
        except ValueError:
            return None
        if parsed.scheme.casefold() != "file":
            return None

        netloc = parsed.netloc
        is_windows_drive_path = False
        # Handle file URLs with netloc for Windows drive letters and UNC paths.
        if netloc and netloc.casefold() != "localhost":
            if re.fullmatch(r"[A-Za-z]:", netloc):
                # Windows drive letter URL (e.g., file://C:/path).
                url_path = f"{netloc}{parsed.path}"
                is_windows_drive_path = True
            else:
                # UNC path (e.g., file://server/share/path).
                url_path = f"//{netloc}{parsed.path}"
        elif re.match(r"^/[A-Za-z]:", parsed.path):
            # file:///C:/... must drop the URL-only leading slash on Windows.
            url_path = parsed.path[1:]
            is_windows_drive_path = True
        else:
            url_path = parsed.path

        # Decode the URL path directly. Python 3.14's url2pathname rejects
        # non-local file authorities on POSIX, even though we only need to
        # preserve the cross-platform path for inspection.
        resolved_path = urllib.parse.unquote(url_path)
        # On Unix, C:/... is relative; anchor it to root so it never resolves
        # under the process working directory. On Windows it is already absolute.
        if is_windows_drive_path and not Path(resolved_path).is_absolute():
            resolved_path = f"/{resolved_path.lstrip('/')}"
        return Path(resolved_path).expanduser().resolve()

    def select_candidate_pdf(
        self, parent: ZoteroItem
    ) -> tuple[str, CandidatePdf | None, str | None]:
        attachments = self.list_child_attachments(parent.item_id, parent.key)

        valid: list[CandidatePdf] = []
        broken_found = False
        for att in attachments:
            raw_path = (att.path_raw or "").strip()
            content_type = (att.content_type or "").partition(";")[0].strip().lower()
            is_pdf = (
                content_type == "application/pdf"
                or Path(raw_path).suffix.lower() == ".pdf"
            )
            if not is_pdf:
                continue

            path = self.resolve_attachment_path(att)
            if path is None:
                broken_found = True
                continue
            if not path.exists() or not path.is_file():
                broken_found = True
                continue

            try:
                stat = path.stat()
            except OSError:
                broken_found = True
                continue
            valid.append(
                CandidatePdf(
                    absolute_path=str(path),
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            )

        if len(valid) == 0 and broken_found:
            return (
                "BROKEN_ATTACHMENT_PATH",
                None,
                "Attachment path is missing or not readable",
            )
        if len(valid) == 0:
            return ("NO_PDF", None, "No valid PDF attachment found")
        if len(valid) > 1:
            return ("MULTIPLE_PDFS", None, "Multiple valid PDF attachments found")
        return ("OK", valid[0], None)

    def all_items(self) -> list[ZoteroItem]:
        return self.list_parent_items()
