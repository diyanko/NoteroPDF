from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import AppConfig, SetupRequired
from .destination_lock import destination_key
from .models import SourceFile, SyncRow, SyncScope, ZoteroItem, validate_zotero_key
from .notion_client import NotionApiError, NotionClient, NotionDataSourceSnapshot
from .status import Status
from .sync_engine import MatchResult, PreviewAction, SyncEngine
from .util import sha256_file


def source_files_config(
    cfg: AppConfig, property_name: str = "Source files"
) -> AppConfig:
    """Resolve an existing dedicated property without writing to Notion."""
    client = NotionClient(
        token=cfg.notion_token, notion_version=cfg.notion.notion_version
    )
    try:
        prop = client.find_files_property(cfg.notion.data_source_id, property_name)
        if prop is None:
            raise SetupRequired(
                "Create a separate Files & media property for supporting files "
                "in the connected Notion database, then run this command again."
            )
        source_key = destination_key(cfg.notion.data_source_id, prop.id)
        if source_key == destination_key(
            cfg.notion.data_source_id, cfg.notion.pdf_property_id
        ):
            raise ValueError(
                "Supporting files need a separate files property from PDF sync."
            )
        pdf = client.resolve_property(
            cfg.notion.data_source_id, cfg.notion.pdf_property_id
        )
        if pdf is None:
            raise SetupRequired(
                "The saved PDF field could not be verified. Run `noteropdf connect` before choosing supporting files."
            )
        if source_key == destination_key(cfg.notion.data_source_id, pdf.id):
            raise ValueError(
                "Supporting files need a separate files property from PDF sync."
            )
        return replace(cfg, notion=replace(cfg.notion, pdf_property_id=prop.id))
    finally:
        client.close()


@dataclass(frozen=True)
class _Plan:
    file: SourceFile
    key: str
    name: str
    digest: str
    action: PreviewAction


class SourceFilesEngine(SyncEngine):
    """Sync reviewed attachment bundles, preserving proven-owned remote files."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        item_keys: tuple[str, ...],
    ):
        if not item_keys:
            raise ValueError("Choose at least one item for supporting-file sync.")
        for key in item_keys:
            validate_zotero_key(key)
        self.item_keys = item_keys
        super().__init__(cfg)

    def _strict_signature(
        self, page_id: str, page: dict[str, Any] | None = None
    ) -> tuple[tuple[str, str, str], ...]:
        if page is None:
            page = self._snapshot.get_page(page_id) if self._snapshot else None
        return NotionClient.verified_files_signature(
            page,
            page_id=page_id,
            data_source_id=self.cfg.notion.data_source_id,
            property_ref=self._pdf_property_ref,
        )

    def _fresh_files_signature(
        self, page_id: str
    ) -> tuple[tuple[str, str, str], ...] | None:
        return self._strict_signature(
            page_id, self._require_notion().get_page(page_id) or {}
        )

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any], owner: str) -> None:
        if not manifest:
            return
        signature = manifest.get("signature")
        files = manifest.get("files")
        if (
            not isinstance(signature, list)
            or not isinstance(files, dict)
            or len(signature) != len(files)
            or len(files) > 100
        ):
            raise ValueError("Supporting-file history is incomplete or invalid.")
        for value in signature:
            if (
                not isinstance(value, (list, tuple))
                or len(value) != 3
                or any(not isinstance(part, str) or not part for part in value)
                or value[1] != "file"
            ):
                raise ValueError(
                    "Supporting-file history has an invalid remote signature."
                )
        for key, value in files.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(value, dict)
                or any(
                    not isinstance(value.get(field), str) or not value[field]
                    for field in ("name", "digest", "content_type", "upload_id")
                )
                or not re.fullmatch(r"[0-9a-f]{64}", value["digest"])
            ):
                raise ValueError("Supporting-file ownership could not be verified.")
            if not key.startswith(f"{owner}:"):
                raise ValueError("Supporting-file history belongs to a different item.")
            validate_zotero_key(key[len(owner) + 1 :])
        names = [files[key]["name"] for key in sorted(files)]
        if names != [value[0] for value in signature] or len(
            {name.casefold() for name in names}
        ) != len(names):
            raise ValueError("Supporting-file history has ambiguous names.")

    @staticmethod
    def _row(
        item: ZoteroItem,
        status: str,
        action: str = "skip",
        error: str | None = None,
        plan: _Plan | None = None,
        match: MatchResult | None = None,
    ) -> SyncRow:
        return SyncRow(
            plan.key if plan else item.sync_key,
            item.title,
            item.zotero_uri,
            match.page_id if match else None,
            match.page_url if match else None,
            plan.file.absolute_path if plan else None,
            action,
            status,
            error,
        )

    @staticmethod
    def _checked_digest(file: SourceFile) -> str:
        path = Path(file.absolute_path)
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
        if (
            before.st_size != file.size
            or before.st_mtime_ns != file.mtime_ns
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise ValueError(
                "A source file changed during scanning. Run the preview again."
            )
        return digest

    def sync(
        self,
        *,
        apply: bool = False,
        approved_actions: tuple[PreviewAction, ...] | None = None,
        scope: SyncScope | None = None,
    ) -> list[SyncRow]:
        if apply and approved_actions is None:
            raise ValueError("Applying source files requires approved preview actions.")
        zotero = self._require_zotero()
        all_items = zotero.all_items()
        available = {item.key for item in all_items}
        if set(self.item_keys) - available:
            raise ValueError(
                "No references matched one or more selected item keys. Check the selected library."
            )
        selected = [item for item in all_items if item.key in self.item_keys]
        if scope is not None:
            scoped_keys = {item.sync_key for item in scope.select(all_items)}
            selected = [item for item in selected if item.sync_key in scoped_keys]
        files = {item.sync_key: zotero.select_source_files(item) for item in selected}
        zotero.close()
        self.zotero = None
        notion = self._require_notion()
        self.data_source_id = self.cfg.notion.data_source_id
        self._pdf_property_ref = self._resolved_pdf_property_ref()
        with self._destination_lock(
            self.data_source_id, self._pdf_property_ref, apply=apply
        ):
            notion.validate_pdf_property(self.data_source_id, self._pdf_property_ref)
            self._snapshot = notion.build_data_source_snapshot(
                self.data_source_id, pdf_property=self._pdf_property_ref
            )
            self._preview_actions = {}
            matches = self._build_match_cache(all_items)
            approved = {
                action.zotero_item_key: action for action in approved_actions or ()
            }
            rows: list[SyncRow] = []
            for item in selected:
                status, attachments, error = files[item.sync_key]
                if status != "OK":
                    rows.append(self._row(item, status, error=error))
                    continue
                match = matches[item.sync_key]
                if match.status != Status.OK or match.page_id is None:
                    rows.append(
                        self._row(item, match.status.value, error=match.message)
                    )
                    continue
                try:
                    rows.extend(
                        self._sync_bundle(item, attachments, match, apply, approved)
                    )
                except NotionApiError as exc:
                    rows.append(
                        self._row(
                            item,
                            self._normalize_status_code(exc.code, Status.UPLOAD_FAILED),
                            error="The source-file operation failed. Run doctor and review the source again.",
                            match=match,
                        )
                    )
                except (OSError, ValueError, TypeError, sqlite3.Error, RuntimeError):
                    rows.append(
                        self._row(
                            item,
                            Status.STALE_PREVIEW.value,
                            error="The source or ownership record changed or could not be verified. Run the preview again.",
                            match=match,
                        )
                    )
            return rows

    def _sync_bundle(
        self,
        item: ZoteroItem,
        files: list[SourceFile],
        match: MatchResult,
        apply: bool,
        approved: dict[str, PreviewAction],
    ) -> list[SyncRow]:
        assert match.page_id is not None
        page_id = match.page_id
        notion = self._require_notion()
        state = self._require_state()
        scope = sha256(
            f"{self.cfg.notion.data_source_id}:{self._pdf_property_ref}:{item.sync_key}:{page_id}".encode()
        ).hexdigest()
        manifest = state.get_source_manifest(scope)
        signature = self._strict_signature(page_id)
        self._validate_manifest(manifest, item.sync_key)
        saved_signature = tuple(tuple(value) for value in manifest.get("signature", []))
        if signature != saved_signature:
            return [
                self._row(
                    item,
                    Status.REMOTE_PDF_CONFLICT.value,
                    error="Source files contains unknown or changed files. Nothing will be replaced.",
                    match=match,
                )
            ]
        stored = manifest.get("files", {})
        if not isinstance(stored, dict):
            raise TypeError("Invalid source ownership record.")
        limit = (
            notion.get_workspace_upload_limit_bytes() or self.DEFAULT_MAX_UPLOAD_BYTES
        )
        if any(file.size > limit for file in files):
            return [
                self._row(
                    item,
                    Status.FILE_TOO_LARGE.value,
                    error="A supporting file exceeds the workspace upload limit.",
                    match=match,
                )
            ]
        fingerprints = tuple((file, self._checked_digest(file)) for file in files)
        bundle_identity = sha256(
            (
                repr(fingerprints)
                + json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        plans: list[_Plan] = []
        changed: list[_Plan] = []
        names: set[str] = set()
        for file, digest in fingerprints:
            filename = Path(file.absolute_path).name
            if filename != filename.strip():
                return [
                    self._row(
                        item,
                        Status.BROKEN_ATTACHMENT_PATH.value,
                        error="A filename starts or ends with spaces. Rename it before syncing supporting files.",
                        match=match,
                    )
                ]
            name = notion.normalize_attachment_filename(filename)
            if name.casefold() in names:
                return [
                    self._row(
                        item,
                        Status.MULTIPLE_PDFS.value,
                        error="Source filenames become ambiguous after normalization.",
                        match=match,
                    )
                ]
            names.add(name.casefold())
            key = f"{item.sync_key}:{file.attachment_key}"
            action = PreviewAction(
                key,
                page_id,
                file.absolute_path,
                file.size,
                file.mtime_ns,
                sha256(f"{digest}:{bundle_identity}:{scope}".encode()).hexdigest(),
                signature,
            )
            plan = _Plan(file, key, name, digest, action)
            plans.append(plan)
            old = stored.get(key, {})
            if (
                old.get("digest") != digest
                or old.get("name") != name
                or old.get("content_type") != file.content_type
                or not old.get("upload_id")
            ):
                changed.append(plan)
        prospective = {key: value["name"] for key, value in stored.items()}
        prospective.update({plan.key: plan.name for plan in plans})
        if len(prospective) > 100 or len(
            {name.casefold() for name in prospective.values()}
        ) != len(prospective):
            raise ValueError(
                "The retained supporting files would exceed the file limit or have ambiguous names."
            )
        if not changed:
            return [
                self._row(
                    item, Status.UNCHANGED.value, "hash_match", plan=plan, match=match
                )
                for plan in plans
            ]
        if not apply:
            for plan in changed:
                self._preview_actions[plan.key] = plan.action
            return [
                self._row(
                    item,
                    Status.OK.value if plan in changed else Status.UNCHANGED.value,
                    "preview_upload:source_file" if plan in changed else "hash_match",
                    plan=plan,
                    match=match,
                )
                for plan in plans
            ]
        current = {plan.key: plan.action for plan in changed}
        approved_for_page = {
            key: action
            for key, action in approved.items()
            if action.notion_page_id == page_id
        }
        if current != approved_for_page:
            return [
                self._row(
                    item,
                    Status.STALE_PREVIEW.value,
                    error="The attachment bundle differs from the approved preview.",
                    match=match,
                )
            ]
        if self._fresh_files_signature(page_id) != signature:
            return [
                self._row(
                    item,
                    Status.STALE_PREVIEW.value,
                    error="Notion source files changed after preview.",
                    match=match,
                )
            ]
        updated: dict[str, Any] = dict(stored)
        for plan in changed:
            create = notion.create_file_upload(
                plan.name, plan.file.content_type, file_size=plan.file.size
            )
            upload_id = notion.send_file_bytes(
                create,
                Path(plan.file.absolute_path),
                content_type=plan.file.content_type,
            )
            updated[plan.key] = {
                "name": plan.name,
                "digest": plan.digest,
                "content_type": plan.file.content_type,
                "upload_id": upload_id,
            }
        # Recheck every local file and the full remote field immediately before attachment.
        if (
            any(self._checked_digest(plan.file) != plan.digest for plan in plans)
            or self._fresh_files_signature(page_id) != signature
        ):
            return [
                self._row(
                    item,
                    Status.STALE_PREVIEW.value,
                    error="The source bundle changed during upload; bytes were not attached.",
                    match=match,
                )
            ]
        if len(updated) > 100:
            raise ValueError("Too many source files for one Notion property.")
        remote = [
            {
                "name": entry["name"],
                "type": "file_upload",
                "file_upload": {"id": entry["upload_id"]},
            }
            for _, entry in sorted(updated.items())
        ]
        attached = notion.attach_source_uploads_to_page(
            page_id, self._pdf_property_ref, remote
        )
        try:
            result_signature = self._strict_signature(page_id, attached)
        except (ValueError, TypeError):
            return [
                self._row(
                    item,
                    Status.STATE_SAVE_FAILED.value,
                    error="Files may already be attached, but the response could not be verified. Preview again; existing files will be protected.",
                    match=match,
                )
            ]
        if (
            len(result_signature) != len(remote)
            or any(
                not name or kind != "file" or not identity
                for name, kind, identity in result_signature
            )
            or [value[0] for value in result_signature]
            != [entry["name"] for entry in remote]
        ):
            return [
                self._row(
                    item,
                    Status.STATE_SAVE_FAILED.value,
                    error="The attached files could not be verified; they will not be replaced automatically.",
                    match=match,
                )
            ]
        try:
            state.save_source_manifest(
                scope,
                {
                    "signature": result_signature,
                    "files": updated,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except (OSError, sqlite3.Error, RuntimeError):
            return [
                self._row(
                    item,
                    Status.STATE_SAVE_FAILED.value,
                    error="Files were attached but ownership could not be saved; they will not be replaced automatically.",
                    match=match,
                )
            ]
        return [
            self._row(
                item,
                Status.OK.value if plan in changed else Status.UNCHANGED.value,
                "upload_attach:source_file" if plan in changed else "hash_match",
                plan=plan,
                match=match,
            )
            for plan in plans
        ]
