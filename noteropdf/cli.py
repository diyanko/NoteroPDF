from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

from .config import (LATEST_NOTION_VERSION, detect_zotero_data_dir,
                     get_default_config_path, get_default_env_path,
                     keyring_available, load_config, render_setup_config,
                     store_token_in_keyring)
from .logging_setup import setup_run_logging
from .notion_client import NotionApiError, NotionClient
from .reporting import write_cleanup_reports, write_reports
from .sync_engine import SyncEngine
from .util import normalize_notion_target_inputs, zotero_maybe_open

MIN_PYTHON = (3, 11)
MAX_PYTHON_EXCLUSIVE = (3, 14)


class _CleanHelpParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        text = super().format_help()
        lines = [line for line in text.splitlines() if "==SUPPRESS==" not in line]
        return "\n".join(lines).rstrip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show detailed technical progress in the terminal",
    )
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Turn off colored terminal output",
    )
    parser = _CleanHelpParser(
        prog="noteropdf",
        description="Upload local Zotero PDFs to matching Notion rows in a safe, predictable way.",
        parents=[parent],
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env file")

    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{setup,doctor,sync,cleanup}",
        parser_class=_CleanHelpParser,
    )

    setup_p = sub.add_parser(
        "setup",
        help="Guided first-time configuration",
        description="Create a NoteroPDF config with guided Zotero and Notion prompts.",
        parents=[parent],
    )
    setup_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip overwrite prompt if config already exists",
    )

    sub.add_parser(
        "doctor",
        help="Check setup and access. Run this first.",
        description="Check that Zotero, Notion, reports, logs, and local state are ready.",
        parents=[parent],
    )
    sync_p = sub.add_parser(
        "sync",
        help="Upload only files that need updates",
        description=(
            "Attach Zotero PDFs to matching Notion rows. Respects "
            "sync.dry_run in config.yaml."
        ),
        parents=[parent],
    )
    sync_p.add_argument(
        "--force", action="store_true", help="Force re-upload even when unchanged"
    )
    cleanup_p = sub.add_parser(
        "cleanup",
        help="Preview or trash stale or duplicate Notion rows",
        description=(
            "Find Notion rows whose Zotero item no longer exists, plus duplicate "
            "rows when a canonical Notero-linked row exists. By default this is a "
            "preview only. Use --apply to move strongly classified rows to Notion "
            "trash after confirmation."
        ),
        parents=[parent],
    )
    cleanup_p.add_argument(
        "--apply",
        action="store_true",
        help="Move cleanup candidates to trash after confirmation",
    )
    cleanup_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the cleanup apply confirmation prompt",
    )

    return parser


def _status_help_text(status: str) -> str | None:
    guides = {
        "NO_PDF": "Some items have no usable PDF. Add one PDF attachment in Zotero and run sync again.",
        "MULTIPLE_PDFS": "Some items have multiple PDFs. Keep only one intended PDF per Zotero item.",
        "BROKEN_ATTACHMENT_PATH": "Some Zotero attachment paths are broken. Re-link the attachment in Zotero.",
        "NO_NOTION_MATCH": "Some items could not be matched in Notion. Check Notero link, Zotero URI, or DOI mapping.",
        "MULTIPLE_NOTION_MATCHES": "Multiple Notion rows matched one item. Make the mapping unique, then rerun.",
        "FILE_TOO_LARGE": "A file is above the current Notion workspace upload limit. Use a smaller PDF and rerun sync.",
        "NOTION_AUTH_ERROR": "Notion access failed. Verify token and integration permissions.",
        "NOTION_SCHEMA_ERROR": "Notion schema mismatch. Confirm property names and types with doctor.",
        "NOTION_RATE_LIMIT": "Notion is rate limiting requests. Wait and rerun.",
        "NOTION_NETWORK_ERROR": "Network issue talking to Notion. Check connection and rerun.",
        "UPLOAD_FAILED": "File upload failed. Rerun sync after checking the logs; sync repairs common Notion-side drift automatically.",
        "ATTACH_FAILED": "Upload finished but attaching to page failed. Rerun sync after checking Notion access.",
        "STATE_SAVE_FAILED": "PDF was attached, but local state save failed. Next run may re-upload the file.",
        "AMBIGUOUS_CLEANUP_MATCH": "Some Notion rows were skipped because multiple rows still point at the same live Zotero item.",
        "UNMANAGED_NOTION_ROW": "Some Notion rows were skipped because they do not have a usable Zotero URI and cannot be classified safely.",
    }
    return guides.get(status)


def _status_label(status: str) -> str:
    labels = {
        "OK": "Uploaded",
        "UNCHANGED": "Already up to date",
        "NO_PDF": "Skipped: no PDF",
        "MULTIPLE_PDFS": "Skipped: multiple PDFs",
        "BROKEN_ATTACHMENT_PATH": "Skipped: missing local file",
        "NO_NOTION_MATCH": "Skipped: no Notion match",
        "MULTIPLE_NOTION_MATCHES": "Skipped: multiple Notion matches",
        "FILE_TOO_LARGE": "Skipped: file too large",
        "STALE_NOTION_ROW": "Stale Notion row",
        "AMBIGUOUS_CLEANUP_MATCH": "Skipped: needs review",
        "UNMANAGED_NOTION_ROW": "Skipped: not managed by NoteroPDF",
    }
    return labels.get(status, status.replace("_", " ").title())


def _action_label(action: str) -> str:
    if action.startswith("dry_run_upload:"):
        return f"Would upload ({_reason_label(action.split(':', 1)[1])})"
    if action.startswith("upload_attach:"):
        return f"Uploaded PDF ({_reason_label(action.split(':', 1)[1])})"
    if action.startswith("dry_run_trash:"):
        return f"Would move to Notion trash ({_reason_label(action.split(':', 1)[1])})"
    if action.startswith("trash:"):
        return f"Moved to Notion trash ({_reason_label(action.split(':', 1)[1])})"
    labels = {
        "skip": "Skipped",
        "error": "Error",
        "upload": "Upload failed",
        "attach": "Attached or attach failed",
        "quick_fingerprint_match": "Already up to date",
        "hash_match": "Already up to date",
        "keep:canonical_notero_page": "Kept linked Notion row",
        "keep:live_zotero_match": "Kept live Zotero row",
        "skip:unmanaged_missing_zotero_uri": "Skipped row missing Zotero URI",
        "skip:out_of_scope_zotero_uri": "Skipped row outside local library scope",
        "skip:stale_canonical_notero_page": "Skipped row with stale Notero link",
        "skip:ambiguous_duplicate_live_match": "Skipped duplicate rows for review",
    }
    return labels.get(action, action.replace("_", " ").replace(":", ": ").title())


def _reason_label(reason: str) -> str:
    labels = {
        "first_sync": "first sync",
        "changed": "PDF changed",
        "forced": "forced",
        "missing_remote_pdf": "missing PDF in Notion",
        "remote_drift_multiple_files": "multiple files in Notion",
        "remote_drift_name_mismatch": "file name changed in Notion",
        "missing_from_zotero_library": "missing from Zotero",
        "duplicate_of_canonical_notero_page": "duplicate of linked row",
    }
    return labels.get(reason, reason.replace("_", " "))


def _report_path_line(label: str, path: Path) -> str:
    return f"- {label}: {path}"


def _cleanup_stale_count(rows) -> int:
    return sum(
        1
        for r in rows
        if r.final_status == "STALE_NOTION_ROW"
        and (
            r.action_taken.startswith("dry_run_trash:")
            or r.action_taken.startswith("trash:")
        )
    )


def _print_summary(rows, *, dry_run: bool) -> None:
    logger = logging.getLogger("noteropdf.cli")
    counts = Counter(r.final_status for r in rows)
    action_counts = Counter(r.action_taken for r in rows)

    logger.info("What happened")
    logger.info("- Processed items: %s", len(rows))
    if dry_run:
        logger.info("- Mode: Preview only. Notion was not changed.")
    else:
        logger.info("- Mode: Live sync. Matching Notion PDF fields may have changed.")
    logger.info("- Result counts:")
    for key in sorted(counts.keys()):
        logger.info("  - %s: %s", _status_label(key), counts[key])

    logger.info("- Actions taken:")
    for key in sorted(action_counts.keys()):
        logger.info("  - %s: %s", _action_label(key), action_counts[key])

    failure_rows = [r for r in rows if r.final_status not in ("OK", "UNCHANGED")]
    if failure_rows:
        reason_counts = Counter()
        for r in failure_rows:
            msg = (r.error_message or "").strip() or "n/a"
            reason_counts[f"{_status_label(r.final_status)} | {msg}"] += 1

        logger.info("- Top failure reasons:")
        for reason, count in reason_counts.most_common(10):
            logger.info("  - %s (count=%s)", reason, count)

        logger.info("What to do next")
        for status in sorted({r.final_status for r in failure_rows}):
            tip = _status_help_text(status)
            if tip:
                logger.info("- %s: %s", _status_label(status), tip)
    else:
        logger.info("[NEXT] What to do next")
        logger.info("- Sync looks healthy. You can rerun the same command anytime.")


def _print_cleanup_summary(rows, *, apply: bool) -> None:
    logger = logging.getLogger("noteropdf.cli")
    counts = Counter(r.final_status for r in rows)
    action_counts = Counter(r.action_taken for r in rows)

    logger.info("What happened")
    logger.info("- Processed Notion rows: %s", len(rows))
    if apply:
        logger.info("- Mode: Apply. Stale rows were moved to Notion trash.")
    else:
        logger.info("- Mode: Preview only. Notion was not changed.")
    logger.info("- Result counts:")
    for key in sorted(counts.keys()):
        logger.info("  - %s: %s", _status_label(key), counts[key])

    logger.info("- Actions taken:")
    for key in sorted(action_counts.keys()):
        logger.info("  - %s: %s", _action_label(key), action_counts[key])

    failure_rows = [
        r
        for r in rows
        if r.final_status
        not in (
            "UNCHANGED",
            "STALE_NOTION_ROW",
            "AMBIGUOUS_CLEANUP_MATCH",
            "UNMANAGED_NOTION_ROW",
        )
    ]
    if failure_rows:
        reason_counts = Counter()
        for r in failure_rows:
            msg = (r.error_message or "").strip() or "n/a"
            reason_counts[f"{_status_label(r.final_status)} | {msg}"] += 1

        logger.info("- Top failure reasons:")
        for reason, count in reason_counts.most_common(10):
            logger.info("  - %s (count=%s)", reason, count)

    logger.info("[NEXT] What to do next")
    stale_count = _cleanup_stale_count(rows)
    if not apply and stale_count:
        logger.info(
            "- Review the cleanup report. To move these %s stale rows to "
            "Notion trash, run `noteropdf cleanup --apply`.",
            stale_count,
        )
    elif not apply:
        logger.info("- No stale rows were found. You can rerun cleanup anytime.")
    else:
        logger.info(
            "- Cleanup completed. Rerun `noteropdf cleanup` anytime to "
            "preview additional stale rows."
        )

    for status in sorted({r.final_status for r in rows}):
        tip = _status_help_text(status)
        if tip:
            logger.info("- %s: %s", _status_label(status), tip)


def _print_write_preflight(
    command_name: str, property_name: str, candidate_count: int, dry_run: bool
) -> None:
    logger = logging.getLogger("noteropdf.cli")
    logger.info("Before starting")
    logger.info("- Command: %s", command_name)
    logger.info("- Target Notion property: %s", property_name)
    logger.info("- Zotero items to check: %s", candidate_count)
    if dry_run:
        logger.info("- Mode: Preview only. Notion will not be changed.")
    else:
        logger.info("- Mode: Live sync. Matching Notion rows may be updated.")


def _make_sync_progress_logger():
    logger = logging.getLogger("noteropdf.cli")

    def _progress(done: int, total: int) -> None:
        if total == 0:
            logger.info("[OK] No Zotero items were found to check.")
            return
        if done == 0:
            logger.info("[INFO] Sync progress: checking %s Zotero items.", total)
            return
        if done == total:
            logger.info("[OK] Sync progress: checked all %s Zotero items.", total)
            return
        logger.info("[INFO] Sync progress: checked %s of %s Zotero items.", done, total)

    return _progress


def _print_cleanup_preflight(candidate_count: int, apply: bool) -> None:
    logger = logging.getLogger("noteropdf.cli")
    logger.info("Before starting")
    logger.info("- Command: cleanup")
    logger.info("- Live Zotero items in scope: %s", candidate_count)
    if apply:
        logger.info(
            "- Mode: Apply requested. You will confirm before Notion rows "
            "are moved to trash."
        )
    else:
        logger.info("- Mode: Preview only. Notion will not be changed.")


def _prompt_value(
    prompt: str,
    default: str | None = None,
    required: bool = True,
    options: tuple[str, ...] | None = None,
) -> str:
    normalized_options = tuple(options) if options else None
    valid_options = {opt.lower() for opt in normalized_options} if normalized_options else None
    while True:
        option_suffix = f" ({'/'.join(normalized_options)})" if normalized_options else ""
        default_suffix = (
            f" [default: {default}]" if default is not None else ""
        )

        raw = input(f"{prompt}{option_suffix}{default_suffix}: ").strip()
        if normalized_options and raw:
            lowered = raw.lower()
            if lowered in valid_options:
                return raw

            # Be forgiving for common shorthand inputs and unique prefixes.
            if valid_options == {"yes", "no"} and lowered in {"y", "n"}:
                return "yes" if lowered == "y" else "no"

            matches = [opt for opt in normalized_options if opt.lower().startswith(lowered)]
            if len(matches) == 1:
                return matches[0]

            print(f"Please enter one of: {', '.join(normalized_options)}.")
            continue
        if raw:
            return raw
        if default is not None:
            return default
        if not required:
            return ""
        print("This value is required.")


def _prompt_secret(prompt: str) -> str:
    while True:
        raw = getpass.getpass(f"{prompt}: ").strip()
        if raw:
            return raw
        print("This value is required.")


def _stdin_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _prompt_yes_no(
    prompt: str,
    *,
    default_yes: bool = True,
    yes_means: str | None = None,
    no_means: str | None = None,
) -> bool:
    default = "yes" if default_yes else "no"
    display = prompt
    if yes_means or no_means:
        parts: list[str] = []
        if yes_means:
            parts.append(f"yes = {yes_means}")
        if no_means:
            parts.append(f"no = {no_means}")
        display = f"{prompt} ({'; '.join(parts)})"
    while True:
        answer = _prompt_value(
            display,
            default=default,
            options=("yes", "no"),
        ).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer 'yes' or 'no'.")


def _run_setup(config_path: Path, env_path: Path, force_overwrite: bool) -> int:
    default_cfg_path = get_default_config_path()
    default_env_path = get_default_env_path()

    effective_config_path = config_path
    effective_env_path = env_path
    if config_path == Path("config.yaml"):
        effective_config_path = default_cfg_path
    if env_path == Path(".env"):
        effective_env_path = default_env_path

    print("NoteroPDF setup")
    print("This wizard helps you connect Zotero and Notion in a few short steps.")
    print("Press Enter to accept the default shown in [brackets].")

    if effective_config_path.exists() and not force_overwrite:
        print(f"Config already exists at: {effective_config_path}")
        if not _prompt_yes_no("Replace the existing saved setup?", default_yes=False):
            print("Setup cancelled.")
            return 2

    detected = detect_zotero_data_dir()
    default_data_dir = str(detected) if detected else str(Path.home() / "Zotero")
    print("\nStep 1 of 4: Zotero")
    data_dir = Path(
        _prompt_value("Zotero data folder", default=default_data_dir)
    ).expanduser()

    print("\nStep 2 of 4: Notion token")
    token_env = _prompt_value(
        "Name to save your Notion token under",
        default="NOTION_TOKEN",
    )
    token_value = _prompt_secret(
        "Notion integration token (paste the full token, starts with ntn_)"
    )

    database_id = ""
    data_source_id = ""
    print("\nStep 3 of 4: Notion target")
    try:
        notion = NotionClient(token=token_value, notion_version=LATEST_NOTION_VERSION)
        try:
            targets = notion.list_accessible_data_sources()
        finally:
            notion.close()
        if len(targets) == 1:
            selected = targets[0]
            print(f"Found one accessible Notion target: {selected.label}")
            if _prompt_yes_no("Use this target?", default_yes=True):
                data_source_id = selected.data_source_id
                database_id = selected.database_id
        elif targets:
            print("Choose one of the Notion targets this integration can access:")
            for idx, target in enumerate(targets, start=1):
                print(f"{idx}) {target.label} [{target.data_source_id}]")
            target_numbers = tuple(str(idx) for idx in range(1, len(targets) + 1))
            while not data_source_id:
                raw_choice = _prompt_value(
                    f"Choose a target number (1-{len(targets)})",
                    options=target_numbers,
                )
                try:
                    selection_index = int(raw_choice)
                except ValueError:
                    print("Enter a number from the list.")
                    continue
                if not (1 <= selection_index <= len(targets)):
                    print("Enter a number from the list.")
                    continue
                selected = targets[selection_index - 1]
                data_source_id = selected.data_source_id
                database_id = selected.database_id
        else:
            print(
                "No Notion targets were discovered automatically for this integration. "
                "Paste a Notion database URL/ID, or a data source URL/ID if you already have one."
            )
    except NotionApiError as exc:
        print(f"Automatic Notion target discovery was skipped: {exc}")

    while not database_id and not data_source_id:
        print(
            "Tip: in Notion, open the database you use with Notero and copy its browser URL."
        )
        raw_database_id = _prompt_value(
            "Notion database URL or ID", default="", required=False
        )
        raw_data_source_id = _prompt_value(
            "Notion data source URL or ID (optional)", default="", required=False
        )
        database_id, data_source_id = normalize_notion_target_inputs(
            raw_database_id, raw_data_source_id
        )
        if not database_id and not data_source_id:
            print("Paste a Notion database URL/ID, or a data source URL/ID.")

    print("\nStep 4 of 4: Field mapping")
    pdf_property_name = _prompt_value(
        "Notion files property name for uploaded PDFs", default="PDF"
    )
    zotero_uri_property_name = _prompt_value(
        "Notion property name used for Zotero URI matching", default="Zotero URI"
    )
    use_keyring = False
    if keyring_available():
        use_keyring = _prompt_yes_no(
            "Store the token securely in your OS keychain?",
            default_yes=True,
        )

    dry_run = _prompt_yes_no(
        "Start in preview mode?",
        default_yes=True,
        yes_means="safe preview (no Notion changes)",
        no_means="live mode (writes to Notion)",
    )

    text = render_setup_config(
        zotero_data_dir=data_dir,
        token_env=token_env,
        database_id=database_id,
        data_source_id=data_source_id,
        pdf_property_name=pdf_property_name,
        zotero_uri_property_name=zotero_uri_property_name,
        dry_run=dry_run,
    )

    effective_config_path.parent.mkdir(parents=True, exist_ok=True)
    effective_config_path.write_text(text, encoding="utf-8")

    if use_keyring and store_token_in_keyring(token_env, token_value):
        print(f"Saved config: {effective_config_path}")
        print(f"Stored token in OS keychain under key: {token_env}")
    else:
        effective_env_path.parent.mkdir(parents=True, exist_ok=True)
        line = f"{token_env}={token_value}\n"

        def _write_env_file_with_perms(path: Path, content: str) -> None:
            """Write env file and try to keep it private for the current user."""
            path.write_text(content, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                # Some platforms/filesystems may not support chmod semantics.
                pass

        if effective_env_path.exists():
            old = effective_env_path.read_text(encoding="utf-8")
            if f"{token_env}=" in old:
                updated = []
                for current in old.splitlines():
                    if current.startswith(f"{token_env}="):
                        updated.append(line.strip())
                    else:
                        updated.append(current)
                _write_env_file_with_perms(
                    effective_env_path, "\n".join(updated).rstrip() + "\n"
                )
            else:
                _write_env_file_with_perms(
                    effective_env_path, old.rstrip() + "\n" + line
                )
        else:
            _write_env_file_with_perms(effective_env_path, line)
        print(f"Saved config: {effective_config_path}")
        print(f"Saved token in env file: {effective_env_path}")

    print("\nNext steps:")
    print("1) Run: noteropdf doctor")
    print("2) Run: noteropdf sync")
    if dry_run:
        print(
            "3) If the preview looks correct, set dry_run: false in "
            f"{effective_config_path} and run sync again"
        )
    else:
        print(
            "3) You are live now (dry_run is off). Re-run sync anytime to apply new changes."
        )
    print("4) Run: noteropdf cleanup to preview stale Notion rows before deleting anything")
    return 0


def _check_supported_python() -> str | None:
    major, minor = sys.version_info[:2]
    version = (major, minor)
    if version < MIN_PYTHON:
        return (
            "Unsupported Python version: "
            f"{major}.{minor}. "
            "Use Python 3.11, 3.12, or 3.13."
        )
    if version >= MAX_PYTHON_EXCLUSIVE:
        return (
            "Unsupported Python version: "
            f"{major}.{minor}. "
            "Use Python 3.11, 3.12, or 3.13."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    python_error = _check_supported_python()
    if python_error:
        print(python_error, file=sys.stderr)
        return 2

    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    explicit_config_arg = any(
        arg == "--config" or arg.startswith("--config=") for arg in raw_argv
    )
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    verbose = bool(getattr(args, "verbose", False))
    no_color = bool(getattr(args, "no_color", False))

    if args.command == "setup":
        config_path = Path(args.config)
        env_path = Path(args.env)
        try:
            return _run_setup(config_path, env_path, force_overwrite=bool(args.yes))
        except KeyboardInterrupt:
            print("\nSetup cancelled.", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Setup failed: {exc}", file=sys.stderr)
            return 1

    env_for_load: Path | None = None if args.env == ".env" else Path(args.env)

    try:
        cfg = load_config(
            Path(args.config),
            env_for_load,
            allow_default_config_fallback=(
                args.config == "config.yaml" and not explicit_config_arg
            ),
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"[ERROR] Configuration problem: {exc}", file=sys.stderr)
        return 2
    logger = logging.getLogger("noteropdf.cli")

    try:
        log_path = setup_run_logging(
            cfg.sync.log_dir,
            args.command,
            cfg.sync.log_level,
            no_color,
            verbose,
        )
    except OSError as exc:
        print(
            "[ERROR] Could not create the run log folder: "
            f"{cfg.sync.log_dir} ({exc}). "
            "Set sync.log_dir in config.yaml to a folder you can write to.",
            file=sys.stderr,
        )
        return 1

    logger.info("[INFO] Detailed log: %s", log_path)
    started = perf_counter()

    if zotero_maybe_open() and args.command in {"sync", "cleanup"}:
        logger.warning(
            "Zotero appears to be running. Read-only mode is used, but close "
            "Zotero for best consistency."
        )

    engine = None
    try:
        open_state = args.command == "sync"
        acquire_lock = args.command == "sync" or (
            args.command == "cleanup" and bool(getattr(args, "apply", False))
        )
        engine = SyncEngine(cfg, open_state=open_state, acquire_lock=acquire_lock)

        if args.command == "doctor":
            lines = engine.doctor()
            for line in lines:
                logger.info(line)
            logger.info("[OK] Setup check finished.")
            return 0

        if args.command == "sync":
            parent_count = engine.estimate_parent_item_count()
            _print_write_preflight(
                "sync", cfg.notion.pdf_property_name, parent_count, cfg.sync.dry_run
            )
            rows = engine.sync(
                force=bool(args.force),
                progress_callback=_make_sync_progress_logger(),
            )
            json_path, csv_path, summary_path = write_reports(
                cfg.sync.report_dir, "sync-force" if args.force else "sync", rows
            )
            _print_summary(rows, dry_run=cfg.sync.dry_run)
            logger.info("Reports")
            logger.info(_report_path_line("JSON report", json_path))
            logger.info(_report_path_line("CSV report", csv_path))
            logger.info(_report_path_line("Summary report", summary_path))
            logger.info("Elapsed seconds: %.2f", perf_counter() - started)
            return 0

        if args.command == "cleanup":
            parent_count = engine.estimate_parent_item_count()
            _print_cleanup_preflight(parent_count, bool(args.apply))
            if args.apply and not args.yes:
                preview_rows = engine.cleanup_deleted_pages(apply=False)
                json_path, csv_path, summary_path = write_cleanup_reports(
                    cfg.sync.report_dir,
                    "cleanup",
                    preview_rows,
                )
                _print_cleanup_summary(preview_rows, apply=False)
                logger.info("Reports")
                logger.info(_report_path_line("JSON report", json_path))
                logger.info(_report_path_line("CSV report", csv_path))
                logger.info(_report_path_line("Summary report", summary_path))

                stale_count = _cleanup_stale_count(preview_rows)
                if stale_count == 0:
                    logger.info("[OK] No stale rows to move to trash.")
                    logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                    return 0
                if not _stdin_interactive():
                    logger.error(
                        "Cleanup apply needs confirmation. Run this command in an "
                        "interactive terminal, or use `noteropdf cleanup --apply --yes` "
                        "after reviewing the preview report."
                    )
                    logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                    return 2
                confirmed = _prompt_yes_no(
                    f"Move {stale_count} stale Notion rows to trash now?",
                    default_yes=False,
                )
                if not confirmed:
                    logger.info("[OK] Cleanup apply cancelled. Notion was not changed.")
                    logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                    return 0

            rows = engine.cleanup_deleted_pages(apply=bool(args.apply))
            json_path, csv_path, summary_path = write_cleanup_reports(
                cfg.sync.report_dir,
                "cleanup-apply" if args.apply else "cleanup",
                rows,
            )
            _print_cleanup_summary(rows, apply=bool(args.apply))
            logger.info("Reports")
            logger.info(_report_path_line("JSON report", json_path))
            logger.info(_report_path_line("CSV report", csv_path))
            logger.info(_report_path_line("Summary report", summary_path))
            logger.info("Elapsed seconds: %.2f", perf_counter() - started)
            return 0

        logger.error("Unknown command: %s", args.command)
        return 2
    except OSError as exc:
        logger.error(
            "%s. Check that the configured state, report, and log folders are writable.",
            exc,
        )
        logger.info("Elapsed seconds: %.2f", perf_counter() - started)
        return 1
    except Exception as exc:
        if verbose:
            logger.exception("Command failed: %s", exc)
        else:
            logger.error("Command failed: %s", exc)
            logger.info("- Run again with `--verbose` for technical details.")
        logger.info("Elapsed seconds: %.2f", perf_counter() - started)
        return 1
    finally:
        if engine is not None:
            engine.close()
