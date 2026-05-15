from __future__ import annotations

import logging
import sqlite3
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import AppConfig
from .models import CandidatePdf, CleanupRow, SyncRow, ZoteroItem
from .notion_client import NotionApiError, NotionClient
from .state_store import FileLock, StateRecord, StateStore
from .status import Status
from .util import sha256_file
from .zotero_repo import ZoteroRepository


@dataclass(frozen=True)
class MatchResult:
    status: Status
    page_id: Optional[str]
    page_url: Optional[str]
    message: Optional[str]


@dataclass(frozen=True)
class CleanupCandidate:
    page_id: str
    page_url: str | None
    title: str | None
    zotero_uri: str | None
    created_time: str | None
    last_edited_time: str | None


class SyncEngine:
    DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024

    def __init__(
        self,
        cfg: AppConfig,
        *,
        open_state: bool = True,
        acquire_lock: bool = True,
    ):
        self.cfg = cfg
        self._logger = logging.getLogger("noteropdf.sync")
        self.zotero: ZoteroRepository | None = None
        self.notion: NotionClient | None = None
        self.state: StateStore | None = None
        self._run_lock: FileLock | None = None
        self._hash_cache: dict[str, str] = {}  # Cache for file hashes
        try:
            self.zotero = ZoteroRepository(
                sqlite_path=cfg.zotero.sqlite_path,
                storage_dir=cfg.zotero.storage_dir,
                data_dir=cfg.zotero.data_dir,
            )
            self.notion = NotionClient(
                token=cfg.notion_token, notion_version=cfg.notion.notion_version
            )
            if open_state:
                self.state = StateStore(
                    cfg.sync.state_db_path,
                    acquire_lock=acquire_lock,
                )
            elif acquire_lock:
                self._run_lock = FileLock.for_state_db(cfg.sync.state_db_path)
        except Exception:
            # Ensure partially initialized resources are always released.
            if self._run_lock is not None:
                self._run_lock.close()
                self._run_lock = None
            if self.state is not None:
                self.state.close()
                self.state = None
            if self.notion is not None:
                self.notion.close()
                self.notion = None
            if self.zotero is not None:
                self.zotero.close()
                self.zotero = None
            raise
        self.data_source_id: str | None = None

    def close(self) -> None:
        if self.zotero is not None:
            self.zotero.close()
        if self.notion is not None:
            self.notion.close()
        if self.state is not None:
            self.state.close()
        if self._run_lock is not None:
            self._run_lock.close()

    @staticmethod
    def _normalize_status_code(code: str | None, fallback: Status) -> str:
        if not code:
            return fallback.value
        if code in {status.value for status in Status}:
            return code
        return fallback.value

    @staticmethod
    def _format_api_error(exc: NotionApiError) -> str:
        base = str(exc).strip() or "Unknown Notion error"
        if exc.hint:
            return f"{base} Next step: {exc.hint}"
        return base

    @staticmethod
    def _cleanup_uri_key(value: str | None) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _cleanup_page_id_key(value: str | None) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _cleanup_web_uri_user(value: str | None) -> str | None:
        raw = (value or "").strip()
        if not raw:
            return None
        parsed = urllib.parse.urlparse(raw)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"zotero.org", "www.zotero.org"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[1].lower() == "items":
            return parts[0].lower()
        return None

    @classmethod
    def _cleanup_uri_in_local_scope(
        cls, value: str | None, *, local_web_users: set[str]
    ) -> bool:
        uri = cls._cleanup_uri_key(value)
        if uri.startswith("zotero://select/library/items/"):
            return True
        if uri.startswith("zotero://select/groups/"):
            return False

        web_user = cls._cleanup_web_uri_user(value)
        return web_user is not None and web_user in local_web_users

    def _validate_zotero_paths(self) -> list[str]:
        """Validate Zotero paths and return status messages."""
        lines: list[str] = []
        if not self.cfg.zotero.data_dir.exists():
            raise RuntimeError(f"Zotero data_dir not found: {self.cfg.zotero.data_dir}")
        if not self.cfg.zotero.sqlite_path.exists():
            raise RuntimeError(
                f"Zotero sqlite_path not found: {self.cfg.zotero.sqlite_path}"
            )
        if not self.cfg.zotero.storage_dir.exists():
            raise RuntimeError(
                f"Zotero storage_dir not found: {self.cfg.zotero.storage_dir}"
            )
        lines.append("- Zotero folders were found.")
        return lines

    def doctor(self) -> list[str]:
        lines: list[str] = []
        lines.append("Setup check results")

        lines.extend(self._validate_zotero_paths())

        lines.append(f"- Notion token source: {self.cfg.notion_token_source}.")

        try:
            sample = self.zotero.list_parent_items()
            lines.append(
                f"- Zotero library can be read. Found {len(sample)} parent items."
            )
            skipped_group_items = self.zotero.count_group_parent_items()
            lines.append(
                "- Zotero group libraries are not synced in this release. "
                f"Skipped group parent items: {skipped_group_items}."
            )
        except Exception as exc:
            raise RuntimeError(f"Zotero DB read-only check failed: {exc}") from exc
        safety = self.zotero.read_only_guarantees()
        lines.append(
            "- Zotero write safety: guaranteed (immutable read-only connection and read-only query guard)."
        )
        if not all(safety.values()):
            raise RuntimeError("Zotero safety checks failed. Refusing to continue.")

        self.notion.ping()
        lines.append("- Notion login works.")
        upload_limit_bytes = self.notion.get_workspace_upload_limit_bytes()
        if upload_limit_bytes:
            lines.append(
                f"- Notion workspace upload limit: {upload_limit_bytes} bytes."
            )

        resolved_db_id, ds_id = self.notion.resolve_target_ids(
            configured_database_id=self.cfg.notion.database_id,
            configured_data_source_id=self.cfg.notion.data_source_id,
        )
        self.data_source_id = ds_id
        if resolved_db_id:
            lines.append(f"- Notion database was resolved: {resolved_db_id}")
        lines.append(f"- Notion data source was resolved: {ds_id}")

        self.notion.validate_pdf_property(ds_id, self.cfg.notion.pdf_property_name)
        lines.append(
            f"- Target Notion property '{self.cfg.notion.pdf_property_name}' exists and is a files field."
        )

        has_uri = self.notion.has_property(
            ds_id, self.cfg.notion.zotero_uri_property_name
        )
        lines.append(
            f"- Optional match property '{self.cfg.notion.zotero_uri_property_name}' is "
            f"{'present' if has_uri else 'missing'}."
        )

        for label, path in (
            ("State DB folder", self.cfg.sync.state_db_path.parent),
            ("Report folder", self.cfg.sync.report_dir),
            ("Log folder", self.cfg.sync.log_dir),
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                raise RuntimeError(f"{label} is not writable: {path} ({exc})") from exc
            lines.append(f"- {label} is writable: {path}")

        lines.append(
            "- Safety scope: this app only updates the configured Notion files field."
        )
        return lines

    def estimate_parent_item_count(self) -> int:
        return len(self.zotero.list_parent_items())

    def _resolve_data_source(self) -> str:
        _, ds_id = self.notion.resolve_target_ids(
            configured_database_id=self.cfg.notion.database_id,
            configured_data_source_id=self.cfg.notion.data_source_id,
        )
        self.data_source_id = ds_id
        return ds_id

    def _resolve_match(self, item: ZoteroItem) -> MatchResult:
        if self.data_source_id is None:
            raise RuntimeError("data_source_id must be set before matching")
        # 1) Primary match: Notero page URL attachment.
        page_id = self.zotero.extract_notero_page_id(item)
        stale_primary = False
        if page_id:
            page = self.notion.get_page(page_id)
            if page is not None and not page.get(
                "in_trash", page.get("archived", False)
            ):
                return MatchResult(
                    status=Status.OK,
                    page_id=page_id,
                    page_url=page.get("url"),
                    message=None,
                )
            # If primary mapping is stale, continue to fallback checks.
            stale_primary = True

        # 2) Secondary match: exact Zotero URI property.
        if self.notion.has_property(
            self.data_source_id, self.cfg.notion.zotero_uri_property_name
        ):
            uri_prop_type = (
                self.notion.get_property_type(
                    self.data_source_id,
                    self.cfg.notion.zotero_uri_property_name,
                )
                or "rich_text"
            )

            candidate_uris = [item.zotero_uri]
            if item.zotero_web_uri:
                candidate_uris.append(item.zotero_web_uri)

            matches = []
            for candidate in candidate_uris:
                matches = self.notion.query_by_property_equals(
                    self.data_source_id,
                    self.cfg.notion.zotero_uri_property_name,
                    candidate,
                    uri_prop_type,
                )
                if len(matches) == 1:
                    break
                if len(matches) > 1:
                    return MatchResult(
                        Status.MULTIPLE_NOTION_MATCHES,
                        None,
                        None,
                        f"Multiple Notion rows matched exact Zotero URI: {candidate}",
                    )

            if len(matches) == 1:
                return MatchResult(
                    Status.OK, matches[0].page_id, matches[0].page_url, None
                )
            if len(matches) > 1:
                return MatchResult(
                    Status.MULTIPLE_NOTION_MATCHES,
                    None,
                    None,
                    "Multiple Notion rows matched exact Zotero URI",
                )

        # 3) Tertiary match: exact DOI when both sides expose it.
        doi_property = self.cfg.notion.doi_property_name
        if item.doi and self.notion.has_property(self.data_source_id, doi_property):
            doi_prop_type = (
                self.notion.get_property_type(self.data_source_id, doi_property)
                or "rich_text"
            )
            matches = self.notion.query_by_doi(
                self.data_source_id, doi_property, item.doi, doi_prop_type
            )
            if len(matches) == 1:
                return MatchResult(
                    Status.OK, matches[0].page_id, matches[0].page_url, None
                )
            if len(matches) > 1:
                return MatchResult(
                    Status.MULTIPLE_NOTION_MATCHES,
                    None,
                    None,
                    "Multiple Notion rows matched exact DOI",
                )

        msg = "No confident Notion match found"
        if stale_primary:
            msg = "Notero page link exists but page is missing/inaccessible and no deterministic fallback match was found"
        return MatchResult(Status.NO_NOTION_MATCH, None, None, msg)

    def _get_cached_hash(self, pdf_path: str) -> str:
        """Get file hash from cache or compute and cache it."""
        if pdf_path not in self._hash_cache:
            self._hash_cache[pdf_path] = sha256_file(Path(pdf_path))
        return self._hash_cache[pdf_path]

    def _needs_upload(
        self, item_key: str, page_id: str, pdf: CandidatePdf, *, force: bool
    ) -> tuple[bool, str, str]:
        if self.state is None:
            raise RuntimeError("Sync state is required before checking uploads")
        rec = self.state.get(item_key)
        expected_name = self.notion.normalize_attachment_filename(
            Path(pdf.absolute_path).name
        )
        remote_reason = self._remote_pdf_state_reason(page_id, expected_name)
        if remote_reason is not None:
            digest = self._get_cached_hash(pdf.absolute_path)
            return True, remote_reason, digest

        if force:
            digest = self._get_cached_hash(pdf.absolute_path)
            return True, "forced", digest

        if rec is None:
            digest = self._get_cached_hash(pdf.absolute_path)
            return True, "first_sync", digest

        if (
            rec.pdf_absolute_path == pdf.absolute_path
            and rec.pdf_size == pdf.size
            and rec.pdf_mtime_ns == pdf.mtime_ns
            and rec.notion_page_id == page_id
        ):
            return False, "quick_fingerprint_match", rec.pdf_sha256

        digest = self._get_cached_hash(pdf.absolute_path)
        if (
            rec.pdf_sha256 == digest
            and rec.notion_page_id == page_id
            and rec.pdf_size == pdf.size
        ):
            return False, "hash_match", digest

        return True, "changed", digest

    def _remote_pdf_state_reason(self, page_id: str, expected_name: str) -> str | None:
        remote_files = self.notion.get_page_files(page_id, self.cfg.notion.pdf_property_name)
        if not remote_files:
            return "missing_remote_pdf"
        if len(remote_files) > 1:
            return "remote_drift_multiple_files"

        actual_name = str(remote_files[0].get("name") or "").strip()
        if actual_name != expected_name:
            return "remote_drift_name_mismatch"
        return None

    def sync(
        self,
        *,
        force: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[SyncRow]:
        ds_id = self._resolve_data_source()
        self.notion.validate_pdf_property(ds_id, self.cfg.notion.pdf_property_name)

        rows: list[SyncRow] = []
        items = list(self.zotero.all_items())
        total = len(items)
        self._logger.debug(
            "Starting sync run: total_parent_items=%s force=%s", total, force
        )
        if progress_callback is not None:
            progress_callback(0, total)
        progress_interval = max(1, (total + 9) // 10) if total else 1

        for idx, item in enumerate(items, start=1):
            row = self._sync_one(item, force=force)
            rows.append(row)
            if progress_callback is not None and (
                idx == total or idx % progress_interval == 0
            ):
                progress_callback(idx, total)
            self._logger.debug(
                "item=%s/%s status=%s action=%s key=%s page=%s title=%s",
                idx,
                total,
                row.final_status,
                row.action_taken,
                item.key,
                row.notion_page_id or "-",
                item.title or "",
            )
            if row.error_message:
                self._logger.warning(
                    "item=%s key=%s status=%s reason=%s",
                    idx,
                    item.key,
                    row.final_status,
                    row.error_message,
                )

        self._logger.debug("Sync run completed: processed=%s", len(rows))
        return rows

    def cleanup_deleted_pages(self, *, apply: bool = False) -> list[CleanupRow]:
        ds_id = self._resolve_data_source()
        if not self.notion.has_property(ds_id, self.cfg.notion.zotero_uri_property_name):
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR",
                f"Missing required Notion property: {self.cfg.notion.zotero_uri_property_name}",
                hint=(
                    "Cleanup requires the configured Zotero URI property to "
                    "exist on the target data source."
                ),
            )

        live_items = list(self.zotero.all_items())
        live_item_key_by_uri: dict[str, str] = {}
        canonical_page_id_by_item_key: dict[str, str] = {}
        canonical_item_key_by_page_id: dict[str, str] = {}
        local_web_users: set[str] = set()
        for item in live_items:
            live_item_key_by_uri[self._cleanup_uri_key(item.zotero_uri)] = item.key
            if item.zotero_web_uri:
                live_item_key_by_uri[self._cleanup_uri_key(item.zotero_web_uri)] = item.key
                web_user = self._cleanup_web_uri_user(item.zotero_web_uri)
                if web_user:
                    local_web_users.add(web_user)
            page_id = self.zotero.extract_notero_page_id(item)
            if page_id:
                page_key = self._cleanup_page_id_key(page_id)
                canonical_page_id_by_item_key[item.key] = page_key
                canonical_item_key_by_page_id[page_key] = item.key

        raw_pages = self.notion.list_data_source_pages(ds_id)
        candidates: list[CleanupCandidate] = []
        active_page_ids: set[str] = set()
        rows_by_live_item_key: dict[str, list[CleanupCandidate]] = defaultdict(list)
        for page in raw_pages:
            if page.get("in_trash", False):
                continue
            page_id = str(page.get("id") or "").strip()
            if not page_id:
                continue
            active_page_ids.add(self._cleanup_page_id_key(page_id))
            candidate = CleanupCandidate(
                page_id=page_id,
                page_url=str(page.get("url") or "").strip() or None,
                title=self.notion.get_page_title_text(page),
                zotero_uri=self.notion.get_page_property_text(
                    page, self.cfg.notion.zotero_uri_property_name
                ),
                created_time=str(page.get("created_time") or "").strip() or None,
                last_edited_time=str(page.get("last_edited_time") or "").strip() or None,
            )
            candidates.append(candidate)
            if candidate.zotero_uri:
                live_item_key = live_item_key_by_uri.get(
                    self._cleanup_uri_key(candidate.zotero_uri)
                )
                if live_item_key:
                    rows_by_live_item_key[live_item_key].append(candidate)

        rows: list[CleanupRow] = []
        self._logger.debug(
            "Starting cleanup run: total_live_items=%s total_notion_pages=%s apply=%s",
            len(live_items),
            len(candidates),
            apply,
        )

        for idx, candidate in enumerate(candidates, start=1):
            row = self._cleanup_one(
                candidate,
                live_item_key_by_uri=live_item_key_by_uri,
                canonical_page_id_by_item_key=canonical_page_id_by_item_key,
                canonical_item_key_by_page_id=canonical_item_key_by_page_id,
                rows_by_live_item_key=rows_by_live_item_key,
                active_page_ids=active_page_ids,
                local_web_users=local_web_users,
                apply=apply,
            )
            rows.append(row)
            self._logger.debug(
                "cleanup=%s/%s status=%s action=%s page=%s title=%s",
                idx,
                len(candidates),
                row.final_status,
                row.action_taken,
                row.notion_page_id,
                row.title or "",
            )
            if row.error_message:
                self._logger.warning(
                    "cleanup=%s page=%s status=%s reason=%s",
                    idx,
                    row.notion_page_id,
                    row.final_status,
                    row.error_message,
                )

        self._logger.debug("Cleanup run completed: processed=%s", len(rows))
        return rows

    def _cleanup_one(
        self,
        candidate: CleanupCandidate,
        *,
        live_item_key_by_uri: dict[str, str],
        canonical_page_id_by_item_key: dict[str, str],
        canonical_item_key_by_page_id: dict[str, str],
        rows_by_live_item_key: dict[str, list[CleanupCandidate]],
        active_page_ids: set[str],
        local_web_users: set[str],
        apply: bool,
    ) -> CleanupRow:
        candidate_page_key = self._cleanup_page_id_key(candidate.page_id)
        if candidate_page_key in canonical_item_key_by_page_id:
            return CleanupRow(
                notion_page_id=candidate.page_id,
                notion_page_url=candidate.page_url,
                title=candidate.title,
                zotero_uri=candidate.zotero_uri,
                action_taken="keep:canonical_notero_page",
                final_status=Status.UNCHANGED.value,
                error_message=None,
                created_time=candidate.created_time,
                last_edited_time=candidate.last_edited_time,
            )

        if not candidate.zotero_uri:
            return CleanupRow(
                notion_page_id=candidate.page_id,
                notion_page_url=candidate.page_url,
                title=candidate.title,
                zotero_uri=None,
                action_taken="skip:unmanaged_missing_zotero_uri",
                final_status=Status.UNMANAGED_NOTION_ROW.value,
                error_message=(
                    "Row has no usable Zotero URI and is not the canonical "
                    "Notero-linked page for a live Zotero item."
                ),
                created_time=candidate.created_time,
                last_edited_time=candidate.last_edited_time,
            )

        live_item_key = live_item_key_by_uri.get(
            self._cleanup_uri_key(candidate.zotero_uri)
        )
        if live_item_key is None:
            if not self._cleanup_uri_in_local_scope(
                candidate.zotero_uri, local_web_users=local_web_users
            ):
                return CleanupRow(
                    notion_page_id=candidate.page_id,
                    notion_page_url=candidate.page_url,
                    title=candidate.title,
                    zotero_uri=candidate.zotero_uri,
                    action_taken="skip:out_of_scope_zotero_uri",
                    final_status=Status.UNMANAGED_NOTION_ROW.value,
                    error_message=(
                        "Row has a Zotero URI outside the local personal library "
                        "scope that cleanup can classify safely."
                    ),
                    created_time=candidate.created_time,
                    last_edited_time=candidate.last_edited_time,
                )
            return self._trash_cleanup_candidate(
                candidate,
                reason="missing_from_zotero_library",
                apply=apply,
            )

        canonical_page_id = canonical_page_id_by_item_key.get(live_item_key)
        if canonical_page_id and canonical_page_id != candidate_page_key:
            if canonical_page_id not in active_page_ids:
                return CleanupRow(
                    notion_page_id=candidate.page_id,
                    notion_page_url=candidate.page_url,
                    title=candidate.title,
                    zotero_uri=candidate.zotero_uri,
                    action_taken="skip:stale_canonical_notero_page",
                    final_status=Status.AMBIGUOUS_CLEANUP_MATCH.value,
                    error_message=(
                        "Zotero's Notero page link points to a page that is not "
                        "active in the target data source, so cleanup cannot "
                        "choose a duplicate row safely."
                    ),
                    created_time=candidate.created_time,
                    last_edited_time=candidate.last_edited_time,
                )
            return self._trash_cleanup_candidate(
                candidate,
                reason="duplicate_of_canonical_notero_page",
                apply=apply,
            )

        competing_rows = rows_by_live_item_key.get(live_item_key, [])
        if len(competing_rows) > 1:
            return CleanupRow(
                notion_page_id=candidate.page_id,
                notion_page_url=candidate.page_url,
                title=candidate.title,
                zotero_uri=candidate.zotero_uri,
                action_taken="skip:ambiguous_duplicate_live_match",
                final_status=Status.AMBIGUOUS_CLEANUP_MATCH.value,
                error_message=(
                    "Multiple Notion rows match the same live Zotero item and "
                    "no canonical Notero-linked page resolves the conflict."
                ),
                created_time=candidate.created_time,
                last_edited_time=candidate.last_edited_time,
            )

        return CleanupRow(
            notion_page_id=candidate.page_id,
            notion_page_url=candidate.page_url,
            title=candidate.title,
            zotero_uri=candidate.zotero_uri,
            action_taken="keep:live_zotero_match",
            final_status=Status.UNCHANGED.value,
            error_message=None,
            created_time=candidate.created_time,
            last_edited_time=candidate.last_edited_time,
        )

    def _trash_cleanup_candidate(
        self, candidate: CleanupCandidate, *, reason: str, apply: bool
    ) -> CleanupRow:
        if not apply:
            return CleanupRow(
                notion_page_id=candidate.page_id,
                notion_page_url=candidate.page_url,
                title=candidate.title,
                zotero_uri=candidate.zotero_uri,
                action_taken=f"dry_run_trash:{reason}",
                final_status=Status.STALE_NOTION_ROW.value,
                error_message=None,
                created_time=candidate.created_time,
                last_edited_time=candidate.last_edited_time,
            )

        try:
            self.notion.trash_page(candidate.page_id)
        except NotionApiError as exc:
            return CleanupRow(
                notion_page_id=candidate.page_id,
                notion_page_url=candidate.page_url,
                title=candidate.title,
                zotero_uri=candidate.zotero_uri,
                action_taken=f"trash:{reason}",
                final_status=self._normalize_status_code(exc.code, Status.ATTACH_FAILED),
                error_message=self._format_api_error(exc),
                created_time=candidate.created_time,
                last_edited_time=candidate.last_edited_time,
            )

        return CleanupRow(
            notion_page_id=candidate.page_id,
            notion_page_url=candidate.page_url,
            title=candidate.title,
            zotero_uri=candidate.zotero_uri,
            action_taken=f"trash:{reason}",
            final_status=Status.STALE_NOTION_ROW.value,
            error_message=None,
            created_time=candidate.created_time,
            last_edited_time=candidate.last_edited_time,
        )

    def _sync_one(self, item: ZoteroItem, *, force: bool = False) -> SyncRow:
        pdf_status, pdf, pdf_msg = self.zotero.select_candidate_pdf(item)
        if pdf_status != Status.OK.value:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=None,
                notion_page_url=None,
                local_pdf_path=pdf.absolute_path if pdf else None,
                action_taken="skip",
                final_status=pdf_status,
                error_message=pdf_msg,
            )

        if pdf is None:
            raise RuntimeError("PDF must be selected before upload")

        try:
            match = self._resolve_match(item)
        except NotionApiError as exc:
            code = self._normalize_status_code(exc.code, Status.ATTACH_FAILED)
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=None,
                notion_page_url=None,
                local_pdf_path=pdf.absolute_path,
                action_taken="error",
                final_status=code,
                error_message=self._format_api_error(exc),
            )

        if match.status != Status.OK:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=None,
                notion_page_url=None,
                local_pdf_path=pdf.absolute_path,
                action_taken="skip",
                final_status=match.status.value,
                error_message=match.message,
            )

        if match.page_id is None:
            raise RuntimeError("Match result must have page_id")

        try:
            workspace_limit = self.notion.get_workspace_upload_limit_bytes()
        except NotionApiError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="error",
                final_status=self._normalize_status_code(
                    exc.code, Status.ATTACH_FAILED
                ),
                error_message=self._format_api_error(exc),
            )
        max_supported = (
            workspace_limit
            if workspace_limit is not None
            else self.DEFAULT_MAX_UPLOAD_BYTES
        )
        if pdf.size > max_supported:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="skip",
                final_status=Status.FILE_TOO_LARGE.value,
                error_message=(
                    f"File is {pdf.size} bytes, above the supported upload limit ({max_supported} bytes)."
                ),
            )

        try:
            needs_upload, reason, digest = self._needs_upload(
                item.key, match.page_id, pdf, force=force
            )
        except NotionApiError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="error",
                final_status=self._normalize_status_code(
                    exc.code, Status.ATTACH_FAILED
                ),
                error_message=self._format_api_error(exc),
            )

        if not needs_upload:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken=reason,
                final_status=Status.UNCHANGED.value,
                error_message=None,
            )

        if self.cfg.sync.dry_run:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken=f"dry_run_upload:{reason}",
                final_status=Status.OK.value,
                error_message=None,
            )

        try:
            create = self.notion.create_file_upload(
                filename=Path(pdf.absolute_path).name,
                content_type="application/pdf",
                file_size=pdf.size,
            )
            upload_id = self.notion.send_file_bytes(create, Path(pdf.absolute_path))
        except NotionApiError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="upload",
                final_status=self._normalize_status_code(
                    exc.code, Status.UPLOAD_FAILED
                ),
                error_message=self._format_api_error(exc),
            )

        try:
            self.notion.attach_file_upload_to_page(
                page_id=match.page_id,
                property_name=self.cfg.notion.pdf_property_name,
                upload_id=upload_id,
                filename=Path(pdf.absolute_path).name,
            )
        except NotionApiError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="attach",
                final_status=Status.ATTACH_FAILED.value,
                error_message=self._format_api_error(exc),
            )

        now = datetime.now(tz=timezone.utc).isoformat()
        try:
            self.state.upsert(
                StateRecord(
                    zotero_item_key=item.key,
                    notion_page_id=match.page_id,
                    pdf_absolute_path=pdf.absolute_path,
                    pdf_size=pdf.size,
                    pdf_mtime_ns=pdf.mtime_ns,
                    pdf_sha256=digest,
                    last_sync_time=now,
                    last_status=Status.OK.value,
                    last_error_code=None,
                )
            )
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            self._logger.error(
                "Upload attached but local state update failed for item=%s page=%s: %s",
                item.key,
                match.page_id,
                exc,
            )
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="attach",
                final_status=Status.STATE_SAVE_FAILED.value,
                error_message=(
                    "File was attached, but local sync state could not be saved. "
                    "A later run may re-upload this file."
                ),
            )

        return SyncRow(
            zotero_item_key=item.key,
            title=item.title,
            zotero_uri=item.zotero_uri,
            notion_page_id=match.page_id,
            notion_page_url=match.page_url,
            local_pdf_path=pdf.absolute_path,
            action_taken=f"upload_attach:{reason}",
            final_status=Status.OK.value,
            error_message=None,
        )
