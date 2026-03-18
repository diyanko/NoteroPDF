from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import AppConfig
from .models import CandidatePdf, SyncRow, ZoteroItem
from .notion_client import NotionApiError, NotionClient
from .state_store import StateRecord, StateStore
from .status import Status
from .util import sha256_file
from .zotero_repo import ZoteroRepository


@dataclass(frozen=True)
class MatchResult:
    status: Status
    page_id: Optional[str]
    page_url: Optional[str]
    message: Optional[str]


class SyncEngine:
    DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._logger = logging.getLogger("noteropdf.sync")
        self.zotero: ZoteroRepository | None = None
        self.notion: NotionClient | None = None
        self.state: StateStore | None = None
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
            self.state = StateStore(cfg.sync.state_db_path)
        except Exception:
            # Ensure partially initialized resources are always released.
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

    def sync(self, *, force: bool = False) -> list[SyncRow]:
        _, ds_id = self.notion.resolve_target_ids(
            configured_database_id=self.cfg.notion.database_id,
            configured_data_source_id=self.cfg.notion.data_source_id,
        )
        self.data_source_id = ds_id
        self.notion.validate_pdf_property(ds_id, self.cfg.notion.pdf_property_name)

        rows: list[SyncRow] = []
        items = list(self.zotero.all_items())
        self._logger.info("Starting sync run: total_parent_items=%s force=%s", len(items), force)

        for idx, item in enumerate(items, start=1):
            row = self._sync_one(item, force=force)
            rows.append(row)
            self._logger.info(
                "item=%s/%s status=%s action=%s key=%s page=%s title=%s",
                idx,
                len(items),
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

        self._logger.info("Sync run completed: processed=%s", len(rows))
        return rows

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

