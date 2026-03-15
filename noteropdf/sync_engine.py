from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import logging

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
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._logger = logging.getLogger("noteropdf.sync")
        self.zotero: ZoteroRepository | None = None
        self.notion: NotionClient | None = None
        self.state: StateStore | None = None
        try:
            self.zotero = ZoteroRepository(
                sqlite_path=cfg.zotero.sqlite_path,
                storage_dir=cfg.zotero.storage_dir,
                data_dir=cfg.zotero.data_dir,
            )
            self.notion = NotionClient(token=cfg.notion_token, notion_version=cfg.notion.notion_version)
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

    def doctor(self) -> list[str]:
        lines: list[str] = []
        lines.append("Setup check results")

        if not self.cfg.zotero.data_dir.exists():
            raise RuntimeError(f"Zotero data_dir not found: {self.cfg.zotero.data_dir}")
        if not self.cfg.zotero.sqlite_path.exists():
            raise RuntimeError(f"Zotero sqlite_path not found: {self.cfg.zotero.sqlite_path}")
        if not self.cfg.zotero.storage_dir.exists():
            raise RuntimeError(f"Zotero storage_dir not found: {self.cfg.zotero.storage_dir}")
        lines.append("- Zotero folders were found.")

        try:
            sample = self.zotero.list_parent_items()
            lines.append(f"- Zotero library can be read. Found {len(sample)} parent items.")
        except Exception as exc:
            raise RuntimeError(f"Zotero DB read-only check failed: {exc}") from exc
        safety = self.zotero.read_only_guarantees()
        lines.append("- Zotero write safety: guaranteed (immutable read-only connection and read-only query guard).")
        if not all(safety.values()):
            raise RuntimeError("Zotero safety checks failed. Refusing to continue.")

        self.notion.ping()
        lines.append("- Notion login works.")

        ds_id = self.notion.resolve_data_source_id(
            database_id=self.cfg.notion.database_id,
            configured_data_source_id=self.cfg.notion.data_source_id,
        )
        self.data_source_id = ds_id
        lines.append(f"- Notion data source was resolved: {ds_id}")

        self.notion.validate_pdf_property(ds_id, self.cfg.notion.pdf_property_name)
        lines.append(f"- Target Notion property '{self.cfg.notion.pdf_property_name}' exists and is a files field.")

        has_uri = self.notion.has_property(ds_id, self.cfg.notion.zotero_uri_property_name)
        lines.append(
            f"- Optional match property '{self.cfg.notion.zotero_uri_property_name}' is "
            f"{'present' if has_uri else 'missing'}."
        )
        lines.append("- Safety scope: this app only updates the configured Notion files field.")
        return lines

    def estimate_parent_item_count(self) -> int:
        return len(self.zotero.list_parent_items())

    def estimate_known_page_count(self) -> int:
        return len(self.state.list_known_page_ids())

    def _resolve_match(self, item: ZoteroItem) -> MatchResult:
        assert self.data_source_id is not None
        # 1) Primary match: Notero page URL attachment.
        page_id = self.zotero.extract_notero_page_id(item)
        stale_primary = False
        if page_id:
            page = self.notion.get_page(page_id)
            if page is not None and not page.get("archived", False):
                return MatchResult(
                    status=Status.OK,
                    page_id=page_id,
                    page_url=page.get("url"),
                    message=None,
                )
            # If primary mapping is stale, continue to fallback checks.
            stale_primary = True

        # 2) Secondary match: exact Zotero URI property.
        if self.notion.has_property(self.data_source_id, self.cfg.notion.zotero_uri_property_name):
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
                return MatchResult(Status.OK, matches[0].page_id, matches[0].page_url, None)
            if len(matches) > 1:
                return MatchResult(
                    Status.MULTIPLE_NOTION_MATCHES,
                    None,
                    None,
                    "Multiple Notion rows matched exact Zotero URI",
                )

        # 3) Tertiary match: exact DOI when both sides expose it.
        if item.doi and self.notion.has_property(self.data_source_id, "DOI"):
            doi_prop_type = self.notion.get_property_type(self.data_source_id, "DOI") or "rich_text"
            matches = self.notion.query_by_doi(self.data_source_id, "DOI", item.doi, doi_prop_type)
            if len(matches) == 1:
                return MatchResult(Status.OK, matches[0].page_id, matches[0].page_url, None)
            if len(matches) > 1:
                return MatchResult(
                    Status.MULTIPLE_NOTION_MATCHES,
                    None,
                    None,
                    "Multiple Notion rows matched exact DOI",
                )

        msg = "No confident Notion match found"
        if stale_primary:
            msg = (
                "Notero page link exists but page is missing/inaccessible and no deterministic fallback match was found"
            )
        return MatchResult(Status.NO_NOTION_MATCH, None, None, msg)

    def _needs_upload(self, item_key: str, page_id: str, pdf: CandidatePdf, *, force: bool) -> tuple[bool, str, str]:
        rec = self.state.get(item_key)

        if force:
            digest = sha256_file(Path(pdf.absolute_path))
            return True, "forced", digest

        remote_files_count = self.notion.page_files_count(page_id, self.cfg.notion.pdf_property_name)
        if remote_files_count == 0:
            digest = sha256_file(Path(pdf.absolute_path))
            return True, "missing_remote_pdf", digest

        if rec is None:
            digest = sha256_file(Path(pdf.absolute_path))
            return True, "first_sync", digest

        if (
            rec.pdf_absolute_path == pdf.absolute_path
            and rec.pdf_size == pdf.size
            and rec.pdf_mtime_ns == pdf.mtime_ns
            and rec.notion_page_id == page_id
        ):
            return False, "quick_fingerprint_match", rec.pdf_sha256

        digest = sha256_file(Path(pdf.absolute_path))
        if rec.pdf_sha256 == digest and rec.notion_page_id == page_id and rec.pdf_size == pdf.size:
            return False, "hash_match", digest

        return True, "changed", digest

    def sync(self, *, force: bool = False) -> list[SyncRow]:
        ds_id = self.notion.resolve_data_source_id(
            database_id=self.cfg.notion.database_id,
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

            if not self.cfg.sync.continue_on_error and row.final_status not in (
                Status.OK.value,
                Status.UNCHANGED.value,
            ):
                break

        self._logger.info("Sync run completed: processed=%s", len(rows))
        return rows

    def _sync_one(self, item: ZoteroItem, *, force: bool) -> SyncRow:
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

        assert pdf is not None

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

        assert match.page_id is not None

        max_supported = self.cfg.sync.max_supported_mb * 1024 * 1024
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
                    f"File is {pdf.size} bytes, above configured max_supported_mb={self.cfg.sync.max_supported_mb}"
                ),
            )

        max_simple_upload = self.cfg.sync.max_simple_upload_mb * 1024 * 1024
        if pdf.size > max_simple_upload:
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
                    f"File is {pdf.size} bytes, above configured max_simple_upload_mb={self.cfg.sync.max_simple_upload_mb}; multipart upload is not implemented in v1"
                ),
            )

        try:
            needs_upload, reason, digest = self._needs_upload(item.key, match.page_id, pdf, force=force)
        except NotionApiError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="error",
                final_status=self._normalize_status_code(exc.code, Status.ATTACH_FAILED),
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
                final_status=self._normalize_status_code(exc.code, Status.UPLOAD_FAILED),
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
        except Exception as exc:
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

    def rebuild_page_files(self) -> list[SyncRow]:
        clear_rows: list[SyncRow] = []
        page_ids = self.state.list_known_page_ids()
        for page_id in page_ids:
            if self.cfg.sync.dry_run:
                self._logger.info(
                    "[DRY-RUN] would clear %s on page %s",
                    self.cfg.notion.pdf_property_name,
                    page_id,
                )
                clear_rows.append(
                    SyncRow(
                        zotero_item_key="",
                        title=None,
                        zotero_uri=None,
                        notion_page_id=page_id,
                        notion_page_url=None,
                        local_pdf_path=None,
                        action_taken="dry_run_clear_pdf_property",
                        final_status=Status.OK.value,
                        error_message=None,
                    )
                )
                continue
            try:
                self.notion.clear_page_files(page_id, self.cfg.notion.pdf_property_name)
                clear_rows.append(
                    SyncRow(
                        zotero_item_key="",
                        title=None,
                        zotero_uri=None,
                        notion_page_id=page_id,
                        notion_page_url=None,
                        local_pdf_path=None,
                        action_taken="clear_pdf_property",
                        final_status=Status.OK.value,
                        error_message=None,
                    )
                )
            except NotionApiError as exc:
                clear_rows.append(
                    SyncRow(
                        zotero_item_key="",
                        title=None,
                        zotero_uri=None,
                        notion_page_id=page_id,
                        notion_page_url=None,
                        local_pdf_path=None,
                        action_taken="clear_pdf_property",
                        final_status=self._normalize_status_code(exc.code, Status.ATTACH_FAILED),
                        error_message=self._format_api_error(exc),
                    )
                )
                self._logger.warning(
                    "Failed to clear files on page=%s property=%s reason=%s",
                    page_id,
                    self.cfg.notion.pdf_property_name,
                    exc,
                )
        return clear_rows + self.sync(force=True)

    def full_reset(self) -> list[SyncRow]:
        rows: list[SyncRow] = []
        page_ids = self.state.list_known_page_ids()
        all_clears_ok = True
        for page_id in page_ids:
            try:
                if not self.cfg.sync.dry_run:
                    self.notion.clear_page_files(page_id, self.cfg.notion.pdf_property_name)
                rows.append(
                    SyncRow(
                        zotero_item_key="",
                        title=None,
                        zotero_uri=None,
                        notion_page_id=page_id,
                        notion_page_url=None,
                        local_pdf_path=None,
                        action_taken="clear_pdf_property",
                        final_status=Status.OK.value,
                        error_message=None,
                    )
                )
            except NotionApiError as exc:
                all_clears_ok = False
                rows.append(
                    SyncRow(
                        zotero_item_key="",
                        title=None,
                        zotero_uri=None,
                        notion_page_id=page_id,
                        notion_page_url=None,
                        local_pdf_path=None,
                        action_taken="clear_pdf_property",
                        final_status=self._normalize_status_code(exc.code, Status.ATTACH_FAILED),
                        error_message=self._format_api_error(exc),
                    )
                )

        if not self.cfg.sync.dry_run and all_clears_ok:
            self.state.clear_all()
        elif not self.cfg.sync.dry_run and not all_clears_ok:
            self._logger.warning("Not clearing local sync state because one or more Notion clear operations failed")
        return rows
