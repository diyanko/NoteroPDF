from __future__ import annotations

import argparse
import getpass
import logging
import re
import shlex
import sqlite3
import sys
import webbrowser
from collections import Counter
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from . import __version__
from .auth import (
    NOTION_TOKEN_PAGE_URL,
    CredentialStore,
    CredentialStoreError,
    resolve_access_token,
)
from .config import (
    LATEST_NOTION_VERSION,
    AppConfig,
    SetupRequired,
    detect_zotero_data_dir,
    get_profile_log_dir,
    get_profile_state_db_path,
    load_app_config,
    load_local_settings,
    validate_zotero_data_dir,
)
from .logging_setup import setup_run_logging
from .models import PdfSelection, SyncRow, SyncScope, ZoteroLibrary, validate_zotero_key
from .notion_client import NotionApiError, NotionClient, NotionProperty, NotionTarget
from .profiles import connect_command as _connect_command
from .profiles import normalize_profile, recovery_guidance, validate_profile_name
from .settings import LocalSettings, save_settings
from .source_engine import SourceFilesEngine, source_files_config
from .state_store import StateStore
from .status import Status
from .sync_engine import SyncEngine
from .zotero_repo import ZoteroRepository

_BLOCKING_SYNC_STATUSES = {
    Status.MULTIPLE_PDFS.value,
    Status.INVALID_PDF_SELECTION.value,
    Status.BROKEN_ATTACHMENT_PATH.value,
    Status.MULTIPLE_NOTION_MATCHES.value,
    Status.NOTION_SCHEMA_ERROR.value,
    Status.NOTION_AUTH_ERROR.value,
    Status.NOTION_RATE_LIMIT.value,
    Status.NOTION_NETWORK_ERROR.value,
    Status.FILE_TOO_LARGE.value,
    Status.UPLOAD_FAILED.value,
    Status.ATTACH_FAILED.value,
    Status.STATE_SAVE_FAILED.value,
    Status.STALE_PREVIEW.value,
    Status.REMOTE_PDF_CONFLICT.value,
}


def _add_output_flags(
    parser: argparse.ArgumentParser, *, suppress_defaults: bool = False
) -> None:
    default = argparse.SUPPRESS if suppress_defaults else False
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=default,
        help="Show technical details in the terminal",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=default,
        help="Disable colored terminal output",
    )


def _add_profile_flag(
    parser: argparse.ArgumentParser, *, suppress_defaults: bool = False
) -> None:
    def parse_name(value: str) -> str:
        try:
            return validate_profile_name(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    parser.add_argument(
        "--profile",
        type=parse_name,
        default=argparse.SUPPRESS if suppress_defaults else None,
        metavar="NAME",
        help="Use a saved connection (default: the original connection)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="noteropdf",
        description=(
            "Connect Zotero to Notion, preview PDF changes, and sync them safely. "
            "Running noteropdf with no command starts sync."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    _add_output_flags(parser)
    _add_profile_flag(parser)
    sub = parser.add_subparsers(
        dest="command",
        metavar="{sync,sources,connect,doctor,pdf}",
    )

    sync = sub.add_parser(
        "sync",
        help="Preview and sync Zotero PDFs",
        description="Preview required uploads, then confirm before Notion changes.",
    )
    _add_output_flags(sync, suppress_defaults=True)
    _add_profile_flag(sync, suppress_defaults=True)
    sync.add_argument(
        "--apply",
        action="store_true",
        help="Apply the preview without an interactive confirmation",
    )
    sync.add_argument(
        "--details",
        action="store_true",
        help="Show every item key and its outcome, including unchanged items",
    )
    sync.add_argument(
        "--collection-key",
        action="append",
        type=validate_zotero_key,
        default=[],
        metavar="KEY",
        help="Include direct members of this collection; repeat for more collections",
    )
    sync.add_argument(
        "--item-key",
        action="append",
        type=validate_zotero_key,
        default=[],
        metavar="KEY",
        help="Include this parent item; repeat for more items",
    )
    sources = sub.add_parser(
        "sources", help="Preview and upload supporting files for selected papers"
    )
    _add_output_flags(sources, suppress_defaults=True)
    _add_profile_flag(sources, suppress_defaults=True)
    sources.add_argument(
        "--details",
        action="store_true",
        help="Show every selected supporting file and its outcome",
    )
    sources.add_argument(
        "--item-key",
        action="append",
        type=validate_zotero_key,
        required=True,
        metavar="KEY",
        help="Parent item to include; repeat for more papers",
    )
    sources.add_argument(
        "--files-property",
        default="Source files",
        metavar="NAME",
        help="Existing dedicated Notion files property (default: Source files)",
    )
    sources.add_argument(
        "--apply",
        action="store_true",
        help="Apply the preview without an interactive confirmation",
    )
    connect = sub.add_parser(
        "connect",
        help="Connect or reconnect Zotero and Notion",
        description=(
            "Detect Zotero, save a Notion personal access token, and select the "
            "Notero database."
        ),
    )
    _add_output_flags(connect, suppress_defaults=True)
    _add_profile_flag(connect, suppress_defaults=True)

    doctor = sub.add_parser(
        "doctor",
        help="Check Zotero, Notion, credentials, and local storage",
    )
    _add_output_flags(doctor, suppress_defaults=True)
    _add_profile_flag(doctor, suppress_defaults=True)
    pdf = sub.add_parser(
        "pdf", help="Inspect or remember an exact PDF attachment choice"
    )
    _add_output_flags(pdf, suppress_defaults=True)
    _add_profile_flag(pdf, suppress_defaults=True)
    pdf.add_argument("item", help="Exact parent item key in the selected library")
    pdf.add_argument(
        "attachment", nargs="?", help="Exact PDF attachment key to remember"
    )
    pdf.add_argument(
        "--clear", action="store_true", help="Forget this item's PDF choice"
    )
    return parser


def _stdin_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


class _SetupBack(Exception):
    pass


class _SetupCancelled(Exception):
    pass


def _setup_input(value: str) -> str:
    if value.casefold() in {"cancel", "quit", "q"}:
        raise _SetupCancelled
    if value.casefold() == "back":
        raise _SetupBack
    return value


def _prompt_value(prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = _setup_input(input(f"{prompt}{suffix}: ").strip())
        value = value or default
        if value:
            return value
        print("Please enter a value.")


def _prompt_yes_no(prompt: str, *, default_yes: bool = False) -> bool:
    choices = "[Y/n]" if default_yes else "[y/N]"
    while True:
        value = _setup_input(input(f"{prompt} {choices}: ").strip().lower())
        if not value:
            return default_yes
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _choose_number(prompt: str, choices: list[str]) -> int:
    if not choices:
        raise ValueError("No choices are available.")
    for index, label in enumerate(choices, start=1):
        print(f"  {index}) {label}")
    while True:
        value = _prompt_value(prompt)
        try:
            selected = int(value)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= selected <= len(choices):
            return selected - 1
        print("Enter a number from the list.")


def _credential_store(
    profile: str | None = None, *, credential_id: str | None = None
) -> CredentialStore:
    if credential_id is not None:
        return CredentialStore(profile=profile, credential_id=credential_id)
    return CredentialStore(profile=profile)


def _select_zotero_directory(existing: LocalSettings) -> Path:
    declined_saved: Path | None = None
    if existing.zotero_data_dir is not None:
        try:
            saved = validate_zotero_data_dir(existing.zotero_data_dir)
        except ValueError:
            pass
        else:
            if not _stdin_interactive() or _prompt_yes_no(
                f"Use the saved Zotero data folder ({saved})?", default_yes=True
            ):
                return saved
            declined_saved = saved

    detected = detect_zotero_data_dir()
    if detected is not None:
        try:
            validated = validate_zotero_data_dir(detected)
        except ValueError as exc:
            print(f"[WARN] {exc}")
        else:
            if declined_saved is None or validated != declined_saved:
                print(f"[OK] Found Zotero: {validated}")
                return validated
    if not _stdin_interactive():
        raise SetupRequired(
            "Zotero could not be detected in a non-interactive session. "
            "Run `noteropdf connect` in a terminal."
        )
    default = "" if declined_saved is not None else str(Path.home() / "Zotero")
    while True:
        try:
            return validate_zotero_data_dir(
                Path(_prompt_value("Zotero data folder", default=default))
            )
        except ValueError as exc:
            print(f"[WARN] {exc}")
            default = ""


def _select_zotero_library(
    existing: LocalSettings, *, revisit: bool = False
) -> ZoteroLibrary:
    libraries = ZoteroRepository.list_libraries()
    if len(libraries) == 1 and existing.zotero_group_id is None:
        if revisit:
            raise _SetupBack
        print("[OK] Using Zotero: My Library")
        return libraries[0]
    if existing.zotero_group_id is not None and not any(
        library.group_id == existing.zotero_group_id for library in libraries
    ):
        print("[WARN] The saved group is unavailable. Choose a library explicitly.")
    labels = [
        (
            f"{library.name} (group {library.group_id})"
            if library.group_id is not None
            else library.name
        )
        + (" [saved]" if library.group_id == existing.zotero_group_id else "")
        for library in libraries
    ]
    return libraries[_choose_number("Choose the Zotero library Notero manages", labels)]


def _prompt_notion_token() -> str:
    while True:
        token = _setup_input(
            getpass.getpass("Paste the Notion personal access token: ").strip()
        )
        if token:
            return token
        print("Please paste a token.")


def _authorize_notion(previous_token: str | None) -> tuple[str, str | None]:
    if previous_token is not None and _prompt_yes_no(
        "Use the saved Notion token?", default_yes=True
    ):
        return previous_token, None

    print("Create a Notion personal access token with the Notion API capability:")
    print(NOTION_TOKEN_PAGE_URL)
    print("Notion shows the token only once. Copy it, then return here.")
    try:
        opened = webbrowser.open(NOTION_TOKEN_PAGE_URL, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print("[WARN] The browser did not open automatically; use the address above.")
    token = _prompt_notion_token()
    return token, token


def _select_target(notion: NotionClient, *, revisit: bool = False) -> NotionTarget:
    targets = notion.list_accessible_data_sources()
    if not targets:
        raise SetupRequired(
            "No Notion data sources are available to this token. Confirm that "
            "the token belongs to the workspace containing the Notero database."
        )
    if len(targets) == 1:
        if revisit:
            raise _SetupBack
        print(f"[OK] Using Notion database: {_target_choice_label(targets[0])}")
        return targets[0]
    if not _stdin_interactive():
        raise SetupRequired(
            "Several Notion databases are available. Run `noteropdf connect` in a "
            "terminal to choose one."
        )
    index = _choose_number(
        "Choose the database Notero manages",
        [_target_choice_label(target) for target in targets],
    )
    return targets[index]


def _target_choice_label(target: NotionTarget) -> str:
    short_id = target.data_source_id.replace("-", "")[:8]
    location = target.url or f"ID {short_id}"
    return f"{target.label} — {location}"


def _select_pdf_property(
    notion: NotionClient,
    target: NotionTarget,
    existing: LocalSettings | None = None,
    *,
    revisit: bool = False,
) -> NotionProperty:
    if (
        existing is not None
        and existing.notion_data_source_id == target.data_source_id
        and existing.pdf_property_id
    ):
        connected = notion.resolve_property(
            target.data_source_id, existing.pdf_property_id
        )
        if connected is not None and connected.type == "files":
            if revisit:
                raise _SetupBack
            return connected

    pdf = notion.find_files_property(target.data_source_id, "NoteroPDF PDF")
    if pdf is not None and revisit:
        raise _SetupBack
    if pdf is None:
        if not _stdin_interactive():
            raise SetupRequired(
                "The selected database has no dedicated 'NoteroPDF PDF' files "
                "property. Run connect in a terminal to create it."
            )
        print(
            "Creating the property changes Notion immediately. "
            "Cancelling setup later leaves that property in place."
        )
        if not _prompt_yes_no(
            "Create a dedicated Notion files property named 'NoteroPDF PDF' in "
            f"{_target_choice_label(target)}?",
            default_yes=True,
        ):
            raise SetupRequired(
                "A dedicated NoteroPDF PDF property is required for safe sync."
            )
        pdf = notion.create_files_property(target.data_source_id, "NoteroPDF PDF")
        print("[OK] Created the dedicated NoteroPDF PDF property.")
    return pdf


def _save_connection(settings: LocalSettings, profile: str | None = None) -> None:
    store = StateStore(get_profile_state_db_path(profile))
    try:
        save_settings(store, settings)
    finally:
        store.close()


def _review_connection(
    library: ZoteroLibrary, target: NotionTarget, pdf: NotionProperty
) -> bool:
    print("\nReview connection")
    print(f"- Zotero library: {library.name}")
    print(f"- Notion destination: {target.label} -> {pdf.name}")
    while True:
        answer = _prompt_value(
            "Save this connection? yes / edit / cancel", default="yes"
        )
        if answer.casefold() in {"yes", "y"}:
            return True
        if answer.casefold() in {"edit", "no", "n"}:
            return False
        print("Enter yes to save, edit to start again, or cancel to leave setup.")


def _run_connect(profile: str | None = None) -> int:
    if not _stdin_interactive():
        raise SetupRequired("Connect requires an interactive terminal.")
    profile = normalize_profile(profile)
    print(f"NoteroPDF connection: {profile or 'default'}")
    print("At any prompt: back returns to the previous step; cancel leaves setup.")
    print("Your saved connection changes only after you review and save it.")
    try:
        existing = load_local_settings(profile=profile, allow_missing_profile=True)
    except ValueError:
        print(
            "[WARN] Local settings were unreadable and will be replaced only when saved."
        )
        existing = LocalSettings()
    credential_store = (
        _credential_store(profile, credential_id=existing.notion_credential_id)
        if existing.notion_credential_id is not None
        else _credential_store(profile)
    )
    previous_token = credential_store.load()
    saved_token = previous_token
    draft = existing
    step = 0
    revisiting = False
    notion = None
    new_token = None
    staged_id = None
    committed = False
    try:
        while step < 6:
            try:
                if step == 0:
                    zotero_dir = _select_zotero_directory(draft)
                    draft = replace(draft, zotero_data_dir=zotero_dir)
                elif step == 1:
                    library = (
                        _select_zotero_library(draft, revisit=True)
                        if revisiting
                        else _select_zotero_library(draft)
                    )
                    draft = replace(draft, zotero_group_id=library.group_id)
                elif step == 2:
                    if notion is not None:
                        notion.close()
                    token, new_token = _authorize_notion(previous_token)
                    notion = NotionClient(
                        token=token, notion_version=LATEST_NOTION_VERSION
                    )
                    try:
                        notion.ping()
                    except NotionApiError as exc:
                        if exc.status_code != 401:
                            raise
                        print(
                            "[WARN] Notion rejected this token. Try a replacement, back, or cancel."
                        )
                        previous_token = None
                        continue
                elif step == 3:
                    assert notion is not None
                    target = (
                        _select_target(notion, revisit=True)
                        if revisiting
                        else _select_target(notion)
                    )
                elif step == 4:
                    assert notion is not None
                    pdf = (
                        _select_pdf_property(notion, target, draft, revisit=True)
                        if revisiting
                        else _select_pdf_property(notion, target, draft)
                    )
                elif not _review_connection(library, target, pdf):
                    step = 0
                    continue
                revisiting = False
                step += 1
            except _SetupBack:
                # Walk past automatic choices until a real prompt can change
                # the previous decision, rather than bouncing forward again.
                revisiting = True
                step = max(0, step - 1)
                print("[INFO] Returned to the previous setup step.")
        if new_token is not None and new_token != saved_token:
            staged_id = credential_store.stage(new_token)
        settings = replace(
            draft,
            notion_data_source_id=target.data_source_id,
            pdf_property_id=pdf.id,
            notion_credential_id=staged_id or existing.notion_credential_id,
        )
        _save_connection(settings, profile)
        committed = True
    except _SetupCancelled:
        print("[OK] Setup cancelled. The saved connection was not changed.")
        return 0
    finally:
        if notion is not None:
            notion.close()
        if staged_id is not None and not committed:
            # An interruption can arrive just after SQLite commits. Never delete
            # a generation that the saved connection may already reference.
            try:
                active = load_local_settings(
                    profile=profile, allow_missing_profile=True
                )
                if active.notion_credential_id != staged_id:
                    credential_store.discard(staged_id)
            except (CredentialStoreError, OSError, sqlite3.Error, ValueError):
                print(
                    "[WARN] An unused token may remain in the credential store. "
                    "No previously saved token was removed."
                )
    print(f"[OK] PDF destination: {target.label} -> {pdf.name}")
    command = f"noteropdf sync --profile {profile}" if profile else "noteropdf"
    print(f"[NEXT] Run `{command}` to preview your first sync.")
    return 0


def _load_ready_config(profile: str | None = None) -> AppConfig:
    # Resolve settings and their credential reference from one snapshot.
    return load_app_config(profile=profile)


def _run_pdf(
    item_key: str,
    attachment_key: str | None,
    *,
    clear: bool,
    profile: str | None = None,
) -> int:
    profile = normalize_profile(profile)
    if clear and attachment_key:
        raise ValueError("Use an attachment key or --clear, not both.")
    cfg = _load_ready_config(profile)
    print(f"[INFO] Connection: {profile or 'default'}")
    profile_flag = f" --profile {profile}" if profile else ""
    store = StateStore(cfg.sync.state_db_path)
    repo = None
    try:
        repo = ZoteroRepository(
            cfg.zotero.storage_dir, cfg.zotero.data_dir, group_id=cfg.zotero.group_id
        )
        parents = [item for item in repo.all_items() if item.key == item_key]
        if len(parents) != 1:
            raise ValueError(
                "That parent item key is unavailable in the selected library."
            )
        parent = parents[0]
        if clear:
            store.set_pdf_selection(parent.sync_key, None)
            print("[OK] PDF choice cleared. Multiple PDFs will be skipped again.")
            return 0
        attachments = repo.list_child_attachments(parent.item_id, parent.key)
        if attachment_key is None:
            saved = store.get_pdf_selection(parent.sync_key)
            print("PDF choices in the selected library (attachment keys):")
            for att in attachments:
                choice = PdfSelection(att.attachment_key, att.path_raw or "")
                status, pdf, _ = repo.select_candidate_pdf(parent, selection=choice)
                if status == Status.OK.value:
                    marker = " [saved]" if saved == choice else ""
                    print(
                        f"- {att.attachment_key}: PDF, {_file_size(pdf.size) if pdf else 'size unavailable'}{_local_attachment_name(pdf.absolute_path) if pdf else ''}{marker}"
                    )
            print(
                f"[NEXT] Run `noteropdf pdf{profile_flag} ITEM ATTACHMENT` to save an exact choice."
            )
            return 0
        matches = [att for att in attachments if att.attachment_key == attachment_key]
        if len(matches) != 1:
            raise ValueError("That attachment does not belong to this parent item.")
        choice = PdfSelection(attachment_key, matches[0].path_raw or "")
        status, _, _ = repo.select_candidate_pdf(parent, selection=choice)
        if status != Status.OK.value:
            raise ValueError("That attachment is not an available local PDF.")
        store.set_pdf_selection(parent.sync_key, choice)
        print(
            f"[OK] PDF choice saved for this library. Run `noteropdf sync{profile_flag}` to review its preview."
        )
        return 0
    finally:
        if repo is not None:
            repo.close()
        store.close()


_UPLOAD_REASONS = {
    "first_sync": "Add a PDF to an empty Notion field",
    "missing_remote_pdf": "Add a PDF to an empty Notion field",
    "changed": "Update the PDF previously uploaded by NoteroPDF",
    "remote_drift_name_mismatch": "Restore the name of the PDF previously uploaded by NoteroPDF",
}

_ATTENTION_REASONS = {
    "NO_PDF": "No local PDF is available. Download or attach a PDF in Zotero.",
    "MULTIPLE_PDFS": "Several PDFs are available. Choose the intended PDF explicitly before syncing.",
    "INVALID_PDF_SELECTION": "The saved PDF choice is unavailable or moved. Run `noteropdf pdf` to choose it again.",
    "BROKEN_ATTACHMENT_PATH": "The PDF cannot be read. Check that it is downloaded in Zotero.",
    "NO_NOTION_MATCH": "No accessible exact Notero page link was found. Sync this item with Notero first.",
    "MULTIPLE_NOTION_MATCHES": "Several items point to one Notion page. Correct their Notero links.",
    "NOTION_SCHEMA_ERROR": "The connected database or PDF field is unavailable. Run `noteropdf connect`.",
    "NOTION_AUTH_ERROR": "Notion access failed. Check permissions or run `noteropdf connect`.",
    "NOTION_RATE_LIMIT": "Notion is busy. Wait, then run sync again.",
    "NOTION_NETWORK_ERROR": "Notion could not be reached. Check the connection and retry.",
    "FILE_TOO_LARGE": "The PDF exceeds Notion's upload limit. Use a smaller PDF.",
    "UPLOAD_FAILED": "The PDF could not be uploaded. Run sync again after checking the connection.",
    "ATTACH_FAILED": "The uploaded PDF could not be attached. Run sync again.",
    "STATE_SAVE_FAILED": "The PDF may be attached, but its history could not be saved. Run doctor before retrying.",
    "STALE_PREVIEW": "The file or destination changed after preview. Run sync again to review it.",
    "REMOTE_PDF_CONFLICT": "Notion contains unknown or multiple files. Review the field; nothing was replaced.",
}


def _item_label(row: SyncRow) -> str:
    # Never echo arbitrary API strings or terminal controls as item identifiers.
    key = row.zotero_item_key.rsplit("/", 1)[-1]
    return f"Item {key}" if re.fullmatch(r"[A-Za-z0-9]{1,32}", key) else "Item"


def _upload_reason(row: SyncRow) -> str:
    reason = row.action_taken.partition(":")[2]
    return _UPLOAD_REASONS.get(
        reason, "Upload the selected PDF to its exact Notero page"
    )


def _print_summary(
    rows: list[SyncRow],
    *,
    preview: bool,
    cfg: AppConfig | None = None,
    details: bool = False,
    profile: str | None = None,
) -> None:
    counts = Counter(row.final_status for row in rows)
    upload_rows = [
        row
        for row in rows
        if row.action_taken.startswith(
            "preview_upload:" if preview else "upload_attach:"
        )
    ]
    attention_rows = [
        row for row in rows if row.final_status not in {"OK", "UNCHANGED"}
    ]
    print("\nSync preview" if preview else "\nSync result")
    if cfg is not None:
        source = (
            "selected Zotero group library"
            if cfg.zotero.group_id is not None
            else "personal Zotero library"
        )
        print(f"- Source: {source}")
        print("- Destination: connected Notion database, dedicated PDF files field")
    print(f"- Items checked: {len(rows)}")
    print(f"- PDFs {'to upload' if preview else 'uploaded'}: {len(upload_rows)}")
    print(f"- Already current: {counts.get('UNCHANGED', 0)}")
    print(f"- Skipped or needing attention: {len(attention_rows)}")
    if preview:
        print("- Preview only. Notion has not changed.")
    limit = len(rows) if details else 5
    if upload_rows:
        print("- Planned uploads:" if preview else "- Uploaded:")
        for row in upload_rows[:limit]:
            print(f"  - {_item_label(row)}: {_upload_reason(row)}")
        if len(upload_rows) > limit:
            print(
                f"  - ...and {len(upload_rows) - limit} more; use `sync --details` to see every item"
            )
    if attention_rows:
        print("- Needs attention by reason:")
        for status, count in sorted(
            Counter(row.final_status for row in attention_rows).items()
        ):
            reason = _ATTENTION_REASONS.get(
                status,
                "The item could not be processed safely. Run `noteropdf doctor`.",
            )
            print(f"  - {count}: {recovery_guidance(reason, profile)}")
        print("- Skipped items:")
        for row in attention_rows[:limit]:
            reason = _ATTENTION_REASONS.get(
                row.final_status, "Could not process this item safely. Run doctor."
            )
            guidance = recovery_guidance(reason, profile, item_key=row.zotero_item_key)
            print(f"  - {_item_label(row)}: {guidance}")
        if len(attention_rows) > limit:
            print(
                f"  - ...and {len(attention_rows) - limit} more; use `sync --details` to see every item"
            )
    if details:
        unchanged = [row for row in rows if row.final_status == "UNCHANGED"]
        if unchanged:
            print("- Already current items:")
            for row in unchanged:
                print(
                    f"  - {_item_label(row)}: The existing PDF is current; no upload needed"
                )


def _preview_upload_count(rows) -> int:
    return sum(1 for row in rows if row.action_taken.startswith("preview_upload:"))


def _blocking_issue_count(rows) -> int:
    return sum(1 for row in rows if row.final_status in _BLOCKING_SYNC_STATUSES)


def _run_sync(
    *,
    apply_without_prompt: bool,
    details: bool = False,
    scope: SyncScope | None = None,
    profile: str | None = None,
) -> int:
    scope = scope or SyncScope()
    profile = normalize_profile(profile)
    try:
        preview_cfg = _load_ready_config(profile)
    except SetupRequired:
        if profile is not None or not _stdin_interactive():
            raise
        print("[INFO] First, connect Zotero and Notion.")
        _run_connect(profile)
        preview_cfg = _load_ready_config(profile)

    print(f"[INFO] Connection: {profile or 'default'}")
    engine = SyncEngine(preview_cfg)
    try:
        print("[INFO] Scanning Zotero and Notion...")
        preview = engine.sync(apply=False, scope=scope)
        approved_actions = engine.preview_actions()
    finally:
        engine.close()
    _print_summary(
        preview, preview=True, cfg=preview_cfg, details=details, profile=profile
    )
    pending = _preview_upload_count(preview)
    preview_blockers = _blocking_issue_count(preview)
    if pending == 0:
        if preview_blockers:
            print(
                f"[WARN] Nothing can be uploaded until {preview_blockers} "
                f"issue{'s are' if preview_blockers != 1 else ' is'} resolved."
            )
            return 1
        print("[OK] Nothing needs uploading.")
        return 0

    should_apply = apply_without_prompt
    if not should_apply and _stdin_interactive():
        should_apply = _prompt_yes_no(
            f"Upload {pending} PDF{'s' if pending != 1 else ''} to Notion now?",
            default_yes=False,
        )
    if not should_apply:
        if not _stdin_interactive():
            command = "noteropdf sync" + (f" --profile {profile}" if profile else "")
            if details:
                command += " --details"
            for key in scope.collection_keys:
                command += f" --collection-key {key}"
            for key in scope.item_keys:
                command += f" --item-key {key}"
            print(f"[NEXT] Preview only. Run `{command} --apply` to upload these PDFs.")
            if preview_blockers:
                return 1
            # A scheduled job without --apply must not look successful while
            # silently leaving uploads pending.
            return 2
        else:
            print("[OK] Sync cancelled. Notion was not changed.")
        return 0

    apply_cfg = _load_ready_config(profile)
    if apply_cfg != preview_cfg:
        print("[WARN] The connection changed after preview. Run the preview again.")
        return 1
    engine = SyncEngine(apply_cfg)
    try:
        print("[INFO] Applying the confirmed uploads...")
        result = engine.sync(
            apply=True,
            approved_actions=approved_actions,
            scope=scope,
        )
    finally:
        engine.close()
    _print_summary(
        result, preview=False, cfg=apply_cfg, details=details, profile=profile
    )
    approved_keys = {action.zotero_item_key for action in approved_actions}
    completed_keys = {
        row.zotero_item_key for row in result if row.final_status in {"OK", "UNCHANGED"}
    }
    failed = len(approved_keys - completed_keys)
    blockers = _blocking_issue_count(result)
    if failed or blockers:
        issue_count = max(failed, blockers)
        print(
            f"[WARN] Sync finished with {issue_count} issue"
            f"{'s' if issue_count != 1 else ''} needing attention."
        )
        return 1
    print("[OK] Sync finished.")
    return 0


def _run_doctor(profile: str | None = None) -> int:
    profile = normalize_profile(profile)
    print("Setup check results")
    ready = True
    try:
        settings = load_local_settings(profile=profile)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(
            f"- [FAIL] Local settings cannot be read: {recovery_guidance(str(exc), profile)}"
        )
        settings = LocalSettings()
        ready = False

    zotero_dir = settings.zotero_data_dir or detect_zotero_data_dir()
    if zotero_dir is None:
        print("- [MISSING] Zotero data folder was not found.")
        ready = False
    else:
        try:
            validated = validate_zotero_data_dir(Path(zotero_dir))
            print(f"- [OK] Zotero data folder: {validated}")
        except ValueError as exc:
            print(f"- [FAIL] {recovery_guidance(str(exc), profile)}")
            ready = False

    try:
        credentials = (
            _credential_store(profile, credential_id=settings.notion_credential_id)
            if settings.notion_credential_id is not None
            else _credential_store(profile)
        )
        token, token_source = resolve_access_token(credentials)
    except CredentialStoreError as exc:
        print(
            f"- [FAIL] Notion credentials cannot be read: {recovery_guidance(str(exc), profile)}"
        )
        token = ""
        token_source = "missing"
        ready = False
    if token:
        print(f"- [OK] Notion token is available ({token_source}).")
    else:
        print("- [MISSING] A Notion personal access token has not been saved.")
        ready = False

    if settings.notion_data_source_id and settings.pdf_property_id:
        print("- [OK] A Notion database and PDF property are selected.")
    else:
        print("- [MISSING] A Notion database and PDF property are not selected.")
        ready = False

    print(f"- [INFO] Settings and sync state: {get_profile_state_db_path(profile)}")
    print("- [INFO] Notion tokens are stored in the operating-system credential store.")
    print(f"- [INFO] Diagnostic logs: {get_profile_log_dir(profile)}")

    if not ready:
        print(
            f"[NEXT] Run `{_connect_command(profile)}` in a terminal to finish setup."
        )
        return 2

    cfg = _load_ready_config(profile)
    engine = SyncEngine(cfg, open_state=False)
    try:
        for line in engine.doctor()[1:]:
            print(recovery_guidance(line, profile))
    finally:
        engine.close()
    print("[OK] Zotero and Notion are ready.")
    return 0


def _source_label(key: str) -> str:
    parent, separator, attachment = key.partition(":")
    parent = parent.rsplit("/", 1)[-1]
    label = f"Item {parent}" if re.fullmatch(r"[A-Z0-9]{8}", parent) else "Item"
    if separator and re.fullmatch(r"[A-Z0-9]{8}", attachment):
        label += f", attachment {attachment}"
    return label


def _local_attachment_name(path: str) -> str:
    """Show recognizable basenames only when both terminal streams are local."""
    if not _stdin_interactive() or not getattr(sys.stdout, "isatty", lambda: False)():
        return ""
    # Handle both path conventions, including Windows paths in fixtures or imports.
    name = re.split(r"[/\\]", path)[-1]
    name = "".join(char if char.isprintable() else "?" for char in name)
    name = name[:120] or "File"
    return f" ({name})"


def _file_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable file size")


def _sources_command(
    item_keys: tuple[str, ...],
    property_name: str,
    profile: str | None,
    *,
    details: bool,
    apply: bool,
) -> str:
    args = ["noteropdf", "sources"]
    if profile:
        args.extend(("--profile", profile))
    for key in item_keys:
        args.extend(("--item-key", key))
    args.extend(("--files-property", property_name))
    if details:
        args.append("--details")
    if apply:
        args.append("--apply")
    if sys.platform == "win32":
        # This is explicitly PowerShell guidance; Command Prompt has different
        # quoting rules for arbitrary user-chosen property names.
        return " ".join(
            argument
            if re.fullmatch(r"[A-Za-z0-9_./:-]+", argument)
            else "'" + argument.replace("'", "''") + "'"
            for argument in args
        )
    return shlex.join(args)


def _source_shell() -> str:
    return "PowerShell" if sys.platform == "win32" else "a POSIX shell"


def _print_source_outcomes(
    rows: list[SyncRow], *, preview: bool, details: bool, profile: str | None
) -> None:
    visible = rows if details else rows[:10]
    for row in visible:
        if row.error_message:
            reason = recovery_guidance(row.error_message, profile)
        elif row.final_status == Status.UNCHANGED.value:
            reason = "Already current; no upload needed."
        elif row.action_taken.startswith("upload_attach:"):
            reason = "File update verified."
        else:
            continue
        print(f"  - {_source_label(row.zotero_item_key)}: {reason}")
    if len(rows) > len(visible):
        print(
            f"  - {len(rows) - len(visible)} more outcomes; repeat the same command with --details."
        )
    if not preview and any(
        row.final_status == Status.STATE_SAVE_FAILED.value for row in rows
    ):
        print(
            "[WARN] Some files may already be attached. Existing files stay protected until their history can be verified."
        )


def _run_sources(
    *,
    item_keys: tuple[str, ...],
    property_name: str,
    apply_without_prompt: bool,
    profile: str | None = None,
    details: bool = False,
) -> int:
    profile = normalize_profile(profile)
    print(f"[INFO] Connection: {profile or 'default'}")
    cfg = source_files_config(_load_ready_config(profile), property_name)
    engine = SourceFilesEngine(cfg, item_keys=item_keys)
    try:
        preview = engine.sync(apply=False)
        approved = engine.preview_actions()
    finally:
        engine.close()
    pending = len(approved)
    print("Supporting files preview")
    print("- Destination: connected Notion database, separate supporting files field")
    print(f"- Files to upload: {pending}")
    for action in approved[: len(approved) if details else 10]:
        suffix = Path(action.pdf_absolute_path).suffix.lower()
        kind = (
            suffix[1:].upper()
            if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix)
            else "Supporting"
        )
        print(
            f"  - {_source_label(action.zotero_item_key)}: {kind} file, {_file_size(action.pdf_size)}{_local_attachment_name(action.pdf_absolute_path)}"
        )
    blockers = _blocking_issue_count(preview)
    print(f"- Issues needing attention: {blockers}")
    print("- Preview only. Notion has not changed.")
    _print_source_outcomes(preview, preview=True, details=details, profile=profile)
    if not pending:
        return 1 if blockers else 0
    should_apply = apply_without_prompt
    if not should_apply and _stdin_interactive():
        should_apply = _prompt_yes_no(
            f"Upload {pending} supporting files to Notion now?", default_yes=False
        )
    if not should_apply:
        if _stdin_interactive():
            print(
                "[OK] Cancelled. Notion was not changed. Adjust your selection and preview again when ready."
            )
            return 0
        command = _sources_command(
            item_keys, property_name, profile, details=details, apply=True
        )
        print(
            f"[NEXT] Preview only. In {_source_shell()}, run `{command}` to apply this selection."
        )
        return 1 if blockers else 2
    apply_cfg = source_files_config(_load_ready_config(profile), property_name)
    if apply_cfg != cfg:
        print("[WARN] The connection changed after preview. Run the preview again.")
        return 1
    engine = SourceFilesEngine(apply_cfg, item_keys=item_keys)
    try:
        result = engine.sync(apply=True, approved_actions=approved)
    finally:
        engine.close()
    completed = {
        row.zotero_item_key for row in result if row.final_status in {"OK", "UNCHANGED"}
    }
    failed = len({action.zotero_item_key for action in approved} - completed)
    result_blockers = _blocking_issue_count(result)
    print("Supporting files result")
    print(
        f"- File updates verified: {sum(row.action_taken.startswith('upload_attach:') for row in result)}"
    )
    print(f"- Issues needing attention: {max(failed, result_blockers)}")
    _print_source_outcomes(result, preview=False, details=details, profile=profile)
    if failed or result_blockers:
        command = _sources_command(
            item_keys, property_name, profile, details=details, apply=False
        )
        print(
            f"[NEXT] In {_source_shell()}, run `{command}` to review the current state before trying again."
        )
    return 1 if failed or result_blockers or blockers else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command or "sync"
    profile = normalize_profile(args.profile)
    verbose = bool(getattr(args, "verbose", False))
    no_color = bool(getattr(args, "no_color", False))

    try:
        log_path = setup_run_logging(
            get_profile_log_dir(profile),
            no_color=no_color,
            verbose=verbose,
            profile=profile,
        )
    except OSError as exc:
        if command != "doctor":
            print(f"[ERROR] Could not open the diagnostic log: {exc}", file=sys.stderr)
            return 1
        print(
            f"[WARN] The diagnostic log is unavailable; continuing doctor in the terminal: {exc}",
            file=sys.stderr,
        )
        logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
        log_path = None
    logger = logging.getLogger("noteropdf.cli")
    if log_path is not None:
        logger.debug("Diagnostic log: %s", log_path)
    started = perf_counter()

    try:
        if command == "connect":
            return _run_connect(profile)
        if command == "doctor":
            return _run_doctor(profile)
        if command == "pdf":
            return _run_pdf(
                args.item, args.attachment, clear=args.clear, profile=profile
            )
        if command == "sync":
            return _run_sync(
                apply_without_prompt=bool(getattr(args, "apply", False)),
                details=bool(getattr(args, "details", False)),
                scope=SyncScope(
                    collection_keys=tuple(getattr(args, "collection_key", ())),
                    item_keys=tuple(getattr(args, "item_key", ())),
                ),
                profile=profile,
            )
        if command == "sources":
            return _run_sources(
                item_keys=tuple(args.item_key),
                property_name=args.files_property,
                apply_without_prompt=args.apply,
                profile=profile,
                details=bool(getattr(args, "details", False)),
            )
        parser.error(f"unknown command: {command}")
        return 2
    except (
        SetupRequired,
        CredentialStoreError,
        NotionApiError,
        ValueError,
    ) as exc:
        logger.error("%s", recovery_guidance(str(exc), profile))
        hint = getattr(exc, "hint", None)
        if hint:
            logger.info("[NEXT] %s", recovery_guidance(hint, profile))
        elif isinstance(exc, SetupRequired):
            logger.info("[NEXT] Run `%s` in a terminal.", _connect_command(profile))
        return 2
    except (_SetupCancelled, _SetupBack):
        logger.info("Cancelled. Run connect to change your saved connection.")
        return 0
    except (KeyboardInterrupt, EOFError):
        logger.warning("Cancelled. Any operation already completed remains in place.")
        return 130
    except Exception as exc:
        if verbose:
            logger.exception("Command failed")
        else:
            logger.error("Command failed: %s", recovery_guidance(str(exc), profile))
            logger.info("[NEXT] Run again with --verbose for technical details.")
        return 1
    finally:
        logger.debug("Elapsed seconds: %.2f", perf_counter() - started)


__all__ = ["main"]
