from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig
from .models import CandidatePdf, SyncRow, ZoteroItem
from .notion_client import (
    NotionApiError,
    NotionClient,
    NotionDataSourceSnapshot,
)
from .state_store import StateRecord, StateStore
from .status import Status
from .util import parse_notion_page_id_from_url, sha256_file
from .zotero_repo import ZoteroRepository


@dataclass(frozen=True)
class MatchResult:
    status: Status
    page_id: str | None
    page_url: str | None
    message: str | None


@dataclass(frozen=True)
class PreviewAction:
    zotero_item_key: str
    notion_page_id: str
    pdf_absolute_path: str
    pdf_size: int
    pdf_mtime_ns: int
    pdf_sha256: str
    remote_files_signature: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class UploadDecision:
    needs_upload: bool
    reason: str
    pdf_sha256: str = ""
    conflict_message: str | None = None


class SyncEngine:
    DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024

    def __init__(
        self,
        cfg: AppConfig,
        *,
        open_state: bool = True,
    ):
        self.cfg = cfg
        self._logger = logging.getLogger("noteropdf.sync")
        self.zotero: ZoteroRepository | None = None
        self.notion: NotionClient | None = None
        self.state: StateStore | None = None
        self._hash_cache: dict[str, str] = {}  # Cache for file hashes
        try:
            self.zotero = ZoteroRepository(
                storage_dir=cfg.zotero.storage_dir,
                data_dir=cfg.zotero.data_dir,
            )
            self.notion = NotionClient(
                token=cfg.notion_token,
                notion_version=cfg.notion.notion_version,
            )
            if open_state:
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
        self._snapshot: NotionDataSourceSnapshot | None = None
        self._pdf_property_ref = ""
        self._apply_writes = False
        self._preview_actions: dict[str, PreviewAction] = {}
        self._approved_actions: dict[str, PreviewAction] | None = None
        self._match_cache: dict[str, MatchResult] | None = None

    def close(self) -> None:
        if self.zotero is not None:
            self.zotero.close()
        if self.notion is not None:
            self.notion.close()
        if self.state is not None:
            self.state.close()

    def _require_zotero(self) -> ZoteroRepository:
        if self.zotero is None:
            raise RuntimeError("The Zotero snapshot is no longer open.")
        return self.zotero

    def _require_notion(self) -> NotionClient:
        if self.notion is None:
            raise RuntimeError("The Notion client is closed.")
        return self.notion

    def _require_state(self) -> StateStore:
        if self.state is None:
            raise RuntimeError("Sync state is not open.")
        return self.state

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
        if not self.cfg.zotero.storage_dir.exists():
            raise RuntimeError(
                f"Zotero storage_dir not found: {self.cfg.zotero.storage_dir}"
            )
        lines.append("- Zotero folders were found.")
        return lines

    def doctor(self) -> list[str]:
        lines: list[str] = []
        lines.append("Setup check results")
        zotero = self._require_zotero()
        notion = self._require_notion()

        lines.extend(self._validate_zotero_paths())

        lines.append(f"- Notion token source: {self.cfg.notion_token_source}.")

        try:
            sample = zotero.list_parent_items()
            lines.append(
                f"- Zotero library can be read. Found {len(sample)} parent items."
            )
            notero_link_count = sum(1 for item in sample if item.notero_page_url)
            lines.append("- Zotero group libraries are not synced in this release.")
        except Exception as exc:
            raise RuntimeError(
                f"Zotero read-only snapshot check failed: {exc}"
            ) from exc
        lines.append("- Zotero read safety: using the local read-only API.")

        notion.ping()
        lines.append("- Notion login works.")
        upload_limit_bytes = notion.get_workspace_upload_limit_bytes()
        if upload_limit_bytes:
            lines.append(
                f"- Notion workspace upload limit: {upload_limit_bytes} bytes."
            )

        ds_id = self.cfg.notion.data_source_id
        self.data_source_id = ds_id
        lines.append(f"- Notion data source is accessible: {ds_id}")

        pdf_property = self._resolved_pdf_property_ref()
        self._pdf_property_ref = pdf_property
        notion.validate_pdf_property(ds_id, pdf_property)
        lines.append(
            f"- Target Notion PDF property '{pdf_property}' exists and is a files field."
        )

        lines.append(
            f"- Deterministic matching: {notero_link_count} exact Notero page links."
        )
        if notero_link_count == 0:
            raise RuntimeError(
                "No exact Notero page links were found. Sync an item with Notero "
                "before running NoteroPDF."
            )

        for label, path in (
            ("State DB folder", self.cfg.sync.state_db_path.parent),
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

    def _resolve_data_source(self) -> str:
        ds_id = self.cfg.notion.data_source_id
        self.data_source_id = ds_id
        return ds_id

    def _resolved_pdf_property_ref(self) -> str:
        configured = str(self.cfg.notion.pdf_property_id or "").strip()
        if not configured:
            raise NotionApiError(
                "NOTION_SCHEMA_ERROR",
                "No NoteroPDF PDF property is connected.",
                hint="Run `noteropdf connect` to select the database again.",
            )
        if self.data_source_id is None:
            return configured
        prop = self._require_notion().resolve_property(self.data_source_id, configured)
        if prop is not None:
            return prop.id
        raise NotionApiError(
            "NOTION_SCHEMA_ERROR",
            f"Missing required Notion property: {configured}",
        )

    def _preview_only(self) -> bool:
        return not self._apply_writes

    def _resolve_match(self, item: ZoteroItem) -> MatchResult:
        if self.data_source_id is None:
            raise RuntimeError("data_source_id must be set before matching")
        page_id = (
            parse_notion_page_id_from_url(item.notero_page_url)
            if item.notero_page_url
            else None
        )
        if not page_id:
            return MatchResult(
                Status.NO_NOTION_MATCH,
                None,
                None,
                "No exact Notero page link found",
            )

        snapshot = self._snapshot
        page = snapshot.get_page(page_id) if snapshot is not None else None
        if page is None or page.get("in_trash", page.get("archived", False)):
            return MatchResult(
                Status.NO_NOTION_MATCH,
                None,
                None,
                "Notero page link points to a missing, inaccessible, or trashed page",
            )
        return MatchResult(
            status=Status.OK,
            page_id=page_id,
            page_url=page.get("url"),
            message=None,
        )

    def _build_match_cache(self, items: list[ZoteroItem]) -> dict[str, MatchResult]:
        """Resolve every target before writes and reject non-bijective plans."""
        matches = {item.key: self._resolve_match(item) for item in items}
        items_by_page: dict[str, list[str]] = {}
        for item in items:
            match = matches[item.key]
            if match.status == Status.OK and match.page_id:
                page_key = NotionDataSourceSnapshot.page_id_key(match.page_id)
                items_by_page.setdefault(page_key, []).append(item.key)

        for item_keys in items_by_page.values():
            if len(item_keys) < 2:
                continue
            message = (
                "Multiple Zotero items resolved to the same Notion page; "
                "none of them were changed."
            )
            for item_key in item_keys:
                matches[item_key] = MatchResult(
                    Status.MULTIPLE_NOTION_MATCHES, None, None, message
                )
        return matches

    def _get_cached_hash(self, pdf_path: str) -> str:
        """Get file hash from cache or compute and cache it."""
        if pdf_path not in self._hash_cache:
            self._hash_cache[pdf_path] = sha256_file(Path(pdf_path))
        return self._hash_cache[pdf_path]

    def _needs_upload(
        self, item_key: str, page_id: str, pdf: CandidatePdf
    ) -> UploadDecision:
        rec = self._require_state().get(item_key)
        expected_name = self._require_notion().normalize_attachment_filename(
            Path(pdf.absolute_path).name
        )
        remote_signature = self._snapshot_files_signature(page_id)
        if len(remote_signature) > 1:
            return UploadDecision(
                needs_upload=False,
                reason="remote_drift_multiple_files",
                conflict_message=(
                    "The managed Notion files property contains multiple entries. "
                    "NoteroPDF will not replace them."
                ),
            )

        recorded_remote_signature = (
            (
                rec.remote_file_name,
                rec.remote_file_type,
                rec.remote_file_identity,
            )
            if rec is not None
            and rec.remote_file_name
            and rec.remote_file_type
            and rec.remote_file_identity
            else None
        )
        same_page = rec is not None and (
            NotionDataSourceSnapshot.page_id_key(rec.notion_page_id)
            == NotionDataSourceSnapshot.page_id_key(page_id)
        )
        managed_record = (
            same_page and recorded_remote_signature == remote_signature[0]
            if len(remote_signature) == 1
            else False
        )
        if remote_signature:
            if not managed_record:
                return UploadDecision(
                    needs_upload=False,
                    reason="remote_pdf_not_managed",
                    conflict_message=(
                        "The dedicated Notion files property already contains a file, "
                        "and local state cannot prove that NoteroPDF uploaded this exact "
                        "remote file. NoteroPDF will not replace it."
                    ),
                )
            if remote_signature[0][0] != expected_name:
                return UploadDecision(
                    needs_upload=True,
                    reason="remote_drift_name_mismatch",
                    pdf_sha256=self._get_cached_hash(pdf.absolute_path),
                )

        if not remote_signature:
            return UploadDecision(
                True, "missing_remote_pdf", self._get_cached_hash(pdf.absolute_path)
            )

        if rec is None:
            raise RuntimeError("Managed remote files require a local state record")

        if (
            rec.pdf_absolute_path == pdf.absolute_path
            and rec.pdf_size == pdf.size
            and rec.pdf_mtime_ns == pdf.mtime_ns
            and same_page
        ):
            return UploadDecision(False, "quick_fingerprint_match", rec.pdf_sha256)

        digest = self._get_cached_hash(pdf.absolute_path)
        if rec.pdf_sha256 == digest and same_page and rec.pdf_size == pdf.size:
            return UploadDecision(False, "hash_match", digest)

        return UploadDecision(True, "changed", digest)

    def _snapshot_files_signature(
        self, page_id: str
    ) -> tuple[tuple[str, str, str], ...]:
        pdf_property = self._pdf_property_ref
        snapshot = self._snapshot
        if snapshot is None:
            raise RuntimeError("Notion snapshot must be loaded before syncing")
        page = snapshot.get_page(page_id)
        return NotionClient.files_property_signature(page or {}, pdf_property)

    def _fresh_files_signature(
        self, page_id: str
    ) -> tuple[tuple[str, str, str], ...] | None:
        page = self._require_notion().get_page(page_id)
        if page is None or page.get("in_trash", page.get("archived", False)):
            return None
        pdf_property = self._pdf_property_ref
        return NotionClient.files_property_signature(page, pdf_property)

    def _revalidate_before_upload(
        self,
        pdf: CandidatePdf,
        page_id: str,
        expected_digest: str,
        expected_remote_signature: tuple[tuple[str, str, str], ...],
    ) -> str | None:
        path = Path(pdf.absolute_path)
        try:
            before = path.stat()
        except OSError:
            return "The selected PDF is no longer available. Run sync again."
        if before.st_size != pdf.size or before.st_mtime_ns != pdf.mtime_ns:
            return "The selected PDF changed after preview. Run sync again."
        try:
            current_digest = sha256_file(path)
            after = path.stat()
        except OSError:
            return (
                "The selected PDF changed while it was being checked. Run sync again."
            )
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or current_digest != expected_digest
        ):
            return "The selected PDF changed after preview. Run sync again."

        fresh_signature = self._fresh_files_signature(page_id)
        if fresh_signature is None:
            return "The target Notion page is missing or in the trash. Run sync again."
        if fresh_signature != expected_remote_signature:
            return "The target Notion files property changed after preview. Run sync again."
        return None

    def sync(
        self,
        *,
        apply: bool = False,
        approved_actions: tuple[PreviewAction, ...] | None = None,
    ) -> list[SyncRow]:
        if apply and approved_actions is None:
            raise ValueError("Applying a sync requires approved preview actions.")

        # Materialize every local decision from one Zotero API response before any
        # Notion upload work.
        zotero = self._require_zotero()
        notion = self._require_notion()
        items = list(zotero.all_items())
        selected_pdfs = {item.key: zotero.select_candidate_pdf(item) for item in items}
        zotero.close()
        self.zotero = None

        ds_id = self._resolve_data_source()
        self._pdf_property_ref = self._resolved_pdf_property_ref()
        notion.validate_pdf_property(ds_id, self._pdf_property_ref)
        self._snapshot = notion.build_data_source_snapshot(
            ds_id,
            pdf_property=self._pdf_property_ref,
        )
        self._apply_writes = apply
        self._preview_actions = {}
        self._approved_actions = (
            {action.zotero_item_key: action for action in approved_actions}
            if approved_actions is not None
            else None
        )
        self._match_cache = None
        self._hash_cache = {}

        rows: list[SyncRow] = []
        eligible_items = [
            item for item in items if selected_pdfs[item.key][0] == Status.OK.value
        ]
        self._match_cache = self._build_match_cache(eligible_items)
        total = len(items)
        self._logger.debug("Starting sync run: total_parent_items=%s", total)
        for idx, item in enumerate(items, start=1):
            row = self._sync_one(
                item,
                selected_pdf=selected_pdfs[item.key],
            )
            rows.append(row)
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

    def preview_actions(self) -> tuple[PreviewAction, ...]:
        return tuple(self._preview_actions.values())

    def _sync_one(
        self,
        item: ZoteroItem,
        *,
        selected_pdf: tuple[str, CandidatePdf | None, str | None] | None = None,
    ) -> SyncRow:
        if selected_pdf is None:
            selected_pdf = self._require_zotero().select_candidate_pdf(item)
        pdf_status, pdf, pdf_msg = selected_pdf
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
            match_cache = self._match_cache
            match = (
                match_cache[item.key]
                if match_cache is not None and item.key in match_cache
                else self._resolve_match(item)
            )
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

        approved: PreviewAction | None = None
        approved_actions = self._approved_actions
        if approved_actions is not None:
            approved = approved_actions.get(item.key)
            if approved is not None:
                try:
                    current_hash = self._get_cached_hash(pdf.absolute_path)
                except OSError:
                    return self._stale_preview_row(item, pdf, match)
                matches_preview = (
                    NotionDataSourceSnapshot.page_id_key(approved.notion_page_id)
                    == NotionDataSourceSnapshot.page_id_key(match.page_id)
                    and approved.pdf_absolute_path == pdf.absolute_path
                    and approved.pdf_size == pdf.size
                    and approved.pdf_mtime_ns == pdf.mtime_ns
                    and approved.pdf_sha256 == current_hash
                    and approved.remote_files_signature
                    == self._snapshot_files_signature(match.page_id)
                )
                if not matches_preview:
                    return self._stale_preview_row(item, pdf, match)

        try:
            workspace_limit = self._require_notion().get_workspace_upload_limit_bytes()
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
            decision = self._needs_upload(item.key, match.page_id, pdf)
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
        except OSError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="skip",
                final_status=Status.BROKEN_ATTACHMENT_PATH.value,
                error_message=f"The selected PDF could not be read: {exc}",
            )

        if decision.conflict_message is not None:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken=f"skip:{decision.reason}",
                final_status=Status.REMOTE_PDF_CONFLICT.value,
                error_message=decision.conflict_message,
            )

        needs_upload = decision.needs_upload
        reason = decision.reason
        digest = decision.pdf_sha256

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

        if self._preview_only():
            self._preview_actions[item.key] = PreviewAction(
                zotero_item_key=item.key,
                notion_page_id=match.page_id,
                pdf_absolute_path=pdf.absolute_path,
                pdf_size=pdf.size,
                pdf_mtime_ns=pdf.mtime_ns,
                pdf_sha256=digest,
                remote_files_signature=self._snapshot_files_signature(match.page_id),
            )
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken=f"preview_upload:{reason}",
                final_status=Status.OK.value,
                error_message=None,
            )

        if approved_actions is not None and approved is None:
            return self._stale_preview_row(item, pdf, match)

        expected_remote_signature = (
            approved.remote_files_signature
            if approved is not None
            else self._snapshot_files_signature(match.page_id)
        )

        try:
            stale_message = self._revalidate_before_upload(
                pdf,
                match.page_id,
                digest,
                expected_remote_signature,
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
        if stale_message is not None:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="skip:stale_preview",
                final_status=Status.STALE_PREVIEW.value,
                error_message=stale_message,
            )

        try:
            notion = self._require_notion()
            create = notion.create_file_upload(
                filename=Path(pdf.absolute_path).name,
                content_type="application/pdf",
                file_size=pdf.size,
            )
            upload_id = notion.send_file_bytes(create, Path(pdf.absolute_path))
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
        except OSError as exc:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="upload",
                final_status=Status.STALE_PREVIEW.value,
                error_message=(
                    f"The selected PDF became unavailable during upload: {exc}. "
                    "Run sync again."
                ),
            )

        try:
            stale_message = self._revalidate_before_upload(
                pdf,
                match.page_id,
                digest,
                expected_remote_signature,
            )
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
                    exc.code, Status.ATTACH_FAILED
                ),
                error_message=self._format_api_error(exc),
            )
        if stale_message is not None:
            return SyncRow(
                zotero_item_key=item.key,
                title=item.title,
                zotero_uri=item.zotero_uri,
                notion_page_id=match.page_id,
                notion_page_url=match.page_url,
                local_pdf_path=pdf.absolute_path,
                action_taken="skip:stale_preview",
                final_status=Status.STALE_PREVIEW.value,
                error_message=(f"{stale_message} Uploaded bytes were not attached."),
            )

        try:
            attached_page = self._require_notion().attach_file_upload_to_page(
                page_id=match.page_id,
                property_id=self._pdf_property_ref,
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

        attached_signature = NotionClient.files_property_signature(
            attached_page, self._pdf_property_ref
        )
        if (
            len(attached_signature) != 1
            or not attached_signature[0][0]
            or not attached_signature[0][1]
            or not attached_signature[0][2]
        ):
            self._logger.error(
                "Upload attached but Notion did not return a stable remote identity "
                "for item=%s page=%s",
                item.key,
                match.page_id,
            )
            return self._state_save_failed_row(item, pdf, match)
        remote_name, remote_type, remote_identity = attached_signature[0]

        now = datetime.now(tz=timezone.utc).isoformat()
        try:
            self._require_state().upsert(
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
                    remote_file_name=remote_name,
                    remote_file_type=remote_type,
                    remote_file_identity=remote_identity,
                )
            )
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            self._logger.error(
                "Upload attached but local state update failed for item=%s page=%s: %s",
                item.key,
                match.page_id,
                exc,
            )
            return self._state_save_failed_row(item, pdf, match)

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

    @staticmethod
    def _state_save_failed_row(
        item: ZoteroItem,
        pdf: CandidatePdf,
        match: MatchResult,
    ) -> SyncRow:
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
                "The file was attached, but its local ownership record could not be "
                "saved. A later run will treat the remote file as unverified and will "
                "not replace it."
            ),
        )

    @staticmethod
    def _stale_preview_row(
        item: ZoteroItem,
        pdf: CandidatePdf,
        match: MatchResult,
    ) -> SyncRow:
        return SyncRow(
            zotero_item_key=item.key,
            title=item.title,
            zotero_uri=item.zotero_uri,
            notion_page_id=match.page_id,
            notion_page_url=match.page_url,
            local_pdf_path=pdf.absolute_path,
            action_taken="skip:stale_preview",
            final_status=Status.STALE_PREVIEW.value,
            error_message=(
                "This upload changed or was not part of the confirmed preview. "
                "Run sync again."
            ),
        )
