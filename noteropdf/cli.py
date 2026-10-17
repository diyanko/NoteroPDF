from __future__ import annotations

import argparse
import getpass
import logging
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
from .models import PdfSelection, SyncScope, ZoteroLibrary, validate_zotero_key
from .notion_client import NotionApiError, NotionClient, NotionProperty, NotionTarget
from .profiles import normalize_profile, validate_profile_name
from .settings import LocalSettings, save_settings
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


def _connect_command(profile: str | None) -> str:
    return f"noteropdf connect --profile {profile}" if profile else "noteropdf connect"


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
        metavar="{sync,connect,doctor,pdf}",
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


def _prompt_value(prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        value = value or default
        if value:
            return value
        print("Please enter a value.")


def _prompt_yes_no(prompt: str, *, default_yes: bool = False) -> bool:
    choices = "[Y/n]" if default_yes else "[y/N]"
    while True:
        value = input(f"{prompt} {choices}: ").strip().lower()
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


def _credential_store(profile: str | None = None) -> CredentialStore:
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


def _select_zotero_library(existing: LocalSettings) -> ZoteroLibrary:
    libraries = ZoteroRepository.list_libraries()
    if len(libraries) == 1 and existing.zotero_group_id is None:
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
        token = getpass.getpass("Paste the Notion personal access token: ").strip()
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


def _select_target(notion: NotionClient) -> NotionTarget:
    targets = notion.list_accessible_data_sources()
    if not targets:
        raise SetupRequired(
            "No Notion data sources are available to this token. Confirm that "
            "the token belongs to the workspace containing the Notero database."
        )
    if len(targets) == 1:
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
            return connected

    pdf = notion.find_files_property(target.data_source_id, "NoteroPDF PDF")
    if pdf is None:
        if not _stdin_interactive():
            raise SetupRequired(
                "The selected database has no dedicated 'NoteroPDF PDF' files "
                "property. Run connect in a terminal to create it."
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


def _run_connect(profile: str | None = None) -> int:
    if not _stdin_interactive():
        raise SetupRequired("Connect requires an interactive terminal.")

    profile = normalize_profile(profile)
    print(f"NoteroPDF connection: {profile or 'default'}")
    try:
        existing = load_local_settings(profile=profile, allow_missing_profile=True)
    except ValueError:
        print("[WARN] Local settings were unreadable and will be replaced.")
        existing = LocalSettings()
    zotero_dir = _select_zotero_directory(existing)
    library = _select_zotero_library(existing)
    credential_store = _credential_store(profile)
    previous_token = credential_store.load()
    token, new_token = _authorize_notion(previous_token)

    notion = NotionClient(token=token, notion_version=LATEST_NOTION_VERSION)
    try:
        try:
            notion.ping()
        except NotionApiError as exc:
            can_replace_saved_token = (
                exc.status_code == 401
                and previous_token is not None
                and new_token is None
                and _stdin_interactive()
            )
            if not can_replace_saved_token:
                raise
            notion.close()
            print("[WARN] The saved Notion token was rejected; paste a replacement.")
            token, new_token = _authorize_notion(None)
            notion = NotionClient(token=token, notion_version=LATEST_NOTION_VERSION)
            notion.ping()
        if new_token is not None:
            credential_store.save(new_token)
            print("[OK] Notion token saved in the operating-system credential store.")
        target = _select_target(notion)
        pdf = _select_pdf_property(notion, target, existing)
    finally:
        notion.close()

    settings = replace(
        existing,
        zotero_data_dir=zotero_dir,
        zotero_group_id=library.group_id,
        notion_data_source_id=target.data_source_id,
        pdf_property_id=pdf.id,
    )
    _save_connection(settings, profile)
    print(f"[OK] PDF destination: {target.label} -> {pdf.name}")
    command = f"noteropdf sync --profile {profile}" if profile else "noteropdf"
    print(f"[NEXT] Run `{command}` to preview your first sync.")
    return 0


def _load_ready_config(profile: str | None = None) -> AppConfig:
    return load_app_config(credential_store=_credential_store(profile), profile=profile)


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
                status, _, _ = repo.select_candidate_pdf(parent, selection=choice)
                if status == Status.OK.value:
                    marker = " [saved]" if saved == choice else ""
                    print(f"- {att.attachment_key}{marker}")
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


def _print_summary(rows, *, preview: bool) -> None:
    counts = Counter(row.final_status for row in rows)
    uploads = sum(
        1
        for row in rows
        if row.action_taken.startswith(("preview_upload:", "upload_attach:"))
    )
    skipped = sum(
        count for status, count in counts.items() if status not in {"OK", "UNCHANGED"}
    )
    print("\nSync preview" if preview else "\nSync result")
    print(f"- Items checked: {len(rows)}")
    print(f"- PDFs {'to upload' if preview else 'uploaded'}: {uploads}")
    print(f"- Already current: {counts.get('UNCHANGED', 0)}")
    print(f"- Skipped or needing attention: {skipped}")
    upload_rows = [
        row
        for row in rows
        if row.action_taken.startswith(
            "preview_upload:" if preview else "upload_attach:"
        )
    ]
    if upload_rows:
        label = "Planned uploads" if preview else "Uploaded"
        print(f"- {label}:")
        for row in upload_rows[:20]:
            title = row.title or row.zotero_item_key
            filename = Path(row.local_pdf_path).name if row.local_pdf_path else "PDF"
            target = row.notion_page_url or row.notion_page_id or "Notion page"
            print(f"  - {title} ({filename}) -> {target}")
        if len(upload_rows) > 20:
            print(f"  - ...and {len(upload_rows) - 20} more")
    attention_rows = [
        row for row in rows if row.final_status not in {"OK", "UNCHANGED"}
    ]
    if attention_rows:
        print("- Needs attention by reason:")
        for status, count in sorted(
            Counter(row.final_status for row in attention_rows).items()
        ):
            print(f"  - {status.replace('_', ' ').title()}: {count}")
        print("- Examples:")
        for row in attention_rows[:10]:
            title = row.title or row.zotero_item_key
            reason = row.error_message or row.final_status.replace("_", " ").lower()
            print(f"  - {title}: {reason}")
        if len(attention_rows) > 10:
            print(f"  - ...and {len(attention_rows) - 10} more; see the diagnostic log")


def _preview_upload_count(rows) -> int:
    return sum(1 for row in rows if row.action_taken.startswith("preview_upload:"))


def _blocking_issue_count(rows) -> int:
    return sum(1 for row in rows if row.final_status in _BLOCKING_SYNC_STATUSES)


def _run_sync(
    *,
    apply_without_prompt: bool,
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
    _print_summary(preview, preview=True)
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
    _print_summary(result, preview=False)
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
        print(f"- [FAIL] Local settings cannot be read: {exc}")
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
            print(f"- [FAIL] {exc}")
            ready = False

    try:
        token, token_source = resolve_access_token(_credential_store(profile))
    except CredentialStoreError as exc:
        print(f"- [FAIL] Notion credentials cannot be read: {exc}")
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
            print(line)
    finally:
        engine.close()
    print("[OK] Zotero and Notion are ready.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command or "sync"
    profile = normalize_profile(args.profile)
    verbose = bool(getattr(args, "verbose", False))
    no_color = bool(getattr(args, "no_color", False))

    try:
        log_path = setup_run_logging(
            get_profile_log_dir(profile), no_color=no_color, verbose=verbose
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
                scope=SyncScope(
                    collection_keys=tuple(getattr(args, "collection_key", ())),
                    item_keys=tuple(getattr(args, "item_key", ())),
                ),
                profile=profile,
            )
        parser.error(f"unknown command: {command}")
        return 2
    except (
        SetupRequired,
        CredentialStoreError,
        NotionApiError,
        ValueError,
    ) as exc:
        logger.error("%s", exc)
        hint = getattr(exc, "hint", None)
        if hint:
            logger.info("[NEXT] %s", hint)
        elif isinstance(exc, SetupRequired):
            logger.info("[NEXT] Run `%s` in a terminal.", _connect_command(profile))
        return 2
    except (KeyboardInterrupt, EOFError):
        logger.warning("Cancelled. Any operation already completed remains in place.")
        return 130
    except Exception as exc:
        if verbose:
            logger.exception("Command failed")
        else:
            logger.error("Command failed: %s", exc)
            logger.info("[NEXT] Run again with --verbose for technical details.")
        return 1
    finally:
        logger.debug("Elapsed seconds: %.2f", perf_counter() - started)


__all__ = ["main"]
