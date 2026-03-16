from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Callable

from .config import (detect_zotero_data_dir, get_default_config_path,
                     get_default_env_path, get_default_sync_paths,
                     keyring_available, load_config, render_setup_config,
                     store_token_in_keyring)
from .logging_setup import setup_run_logging
from .reporting import write_reports
from .support_bundle import build_support_bundle
from .sync_engine import SyncEngine
from .util import zotero_maybe_open


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="noteropdf",
        description="Upload local Zotero PDFs to matching Notion rows in a safe, predictable way.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env file")

    sub = parser.add_subparsers(dest="command", required=True)

    setup_p = sub.add_parser("setup", help="Guided first-time configuration")
    setup_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip overwrite prompt if config already exists",
    )

    sub.add_parser("doctor", help="Check setup and access. Run this first.")
    sub.add_parser(
        "support-bundle",
        help="Create a sanitized diagnostics zip for support/debugging",
    )

    sync_p = sub.add_parser("sync", help="Upload only files that need updates")
    sync_p.add_argument(
        "--force", action="store_true", help="Force re-upload even when unchanged"
    )

    rebuild_p = sub.add_parser(
        "rebuild-page-files",
        help="Clear known PDF fields, then upload fresh files again",
    )
    rebuild_p.add_argument(
        "--yes", action="store_true", help="Required confirmation flag"
    )

    reset_p = sub.add_parser(
        "full-reset", help="Dangerous: clear known PDF fields and local sync state"
    )
    reset_p.add_argument(
        "--yes", action="store_true", help="Required confirmation flag"
    )

    return parser


def _status_help_text(status: str) -> str | None:
    guides = {
        "NO_PDF": "Some items have no usable PDF. Add one PDF attachment in Zotero and run sync again.",
        "MULTIPLE_PDFS": "Some items have multiple PDFs. Keep only one intended PDF per Zotero item.",
        "BROKEN_ATTACHMENT_PATH": "Some Zotero attachment paths are broken. Re-link the attachment in Zotero.",
        "NO_NOTION_MATCH": "Some items could not be matched in Notion. Check Notero link, Zotero URI, or DOI mapping.",
        "MULTIPLE_NOTION_MATCHES": "Multiple Notion rows matched one item. Make the mapping unique, then rerun.",
        "FILE_TOO_LARGE": "A file is above allowed upload size. Lower file size or adjust limits carefully.",
        "NOTION_AUTH_ERROR": "Notion access failed. Verify token and integration permissions.",
        "NOTION_SCHEMA_ERROR": "Notion schema mismatch. Confirm property names and types with doctor.",
        "NOTION_RATE_LIMIT": "Notion is rate limiting requests. Wait and rerun.",
        "NOTION_NETWORK_ERROR": "Network issue talking to Notion. Check connection and rerun.",
        "UPLOAD_FAILED": "File upload failed. Rerun sync; unchanged items are skipped automatically.",
        "ATTACH_FAILED": "Upload finished but attaching to page failed. Rerun sync after checking Notion access.",
        "STATE_SAVE_FAILED": "PDF was attached, but local state save failed. Next run may re-upload the file.",
    }
    return guides.get(status)


def _print_summary(rows) -> None:
    logger = logging.getLogger("noteropdf.cli")
    counts = Counter(r.final_status for r in rows)
    action_counts = Counter(r.action_taken for r in rows)

    logger.info("What happened")
    logger.info("- Processed items: %s", len(rows))
    logger.info("- Result counts:")
    for key in sorted(counts.keys()):
        logger.info("  - %s: %s", key, counts[key])

    logger.info("- Actions taken:")
    for key in sorted(action_counts.keys()):
        logger.info("  - %s: %s", key, action_counts[key])

    failure_rows = [r for r in rows if r.final_status not in ("OK", "UNCHANGED")]
    if failure_rows:
        reason_counts = Counter()
        for r in failure_rows:
            msg = (r.error_message or "").strip() or "n/a"
            reason_counts[f"{r.final_status} | {msg}"] += 1

        logger.info("- Top failure reasons:")
        for reason, count in reason_counts.most_common(10):
            logger.info("  - %s (count=%s)", reason, count)

        logger.info("What to do next")
        for status in sorted({r.final_status for r in failure_rows}):
            tip = _status_help_text(status)
            if tip:
                logger.info("- %s: %s", status, tip)
    else:
        logger.info("What to do next")
        logger.info("- Sync looks healthy. You can rerun the same command anytime.")


def _print_write_preflight(
    command_name: str, property_name: str, candidate_count: int, dry_run: bool
) -> None:
    logger = logging.getLogger("noteropdf.cli")
    logger.info("Write preflight")
    logger.info("- Command: %s", command_name)
    logger.info("- Target Notion property: %s", property_name)
    logger.info("- Candidate items/pages in scope: %s", candidate_count)
    if dry_run:
        logger.info("- Dry run is ON. No changes will be written to Notion.")
    else:
        logger.info("- Dry run is OFF. Matching rows may be updated in Notion.")


def _confirm_destructive_action(
    *,
    action_label: str,
    property_name: str,
    page_count: int,
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    logger = logging.getLogger("noteropdf.cli")
    expected = f"CONFIRM {page_count}"
    logger.warning(
        "%s will clear '%s' on %s page(s).",
        action_label,
        property_name,
        page_count,
    )
    logger.warning("Type '%s' to continue.", expected)
    fn = input_fn or input
    try:
        typed = fn("> ").strip()
    except EOFError:
        return False
    if typed != expected:
        logger.error("Confirmation text did not match. Operation cancelled.")
        return False
    return True


def _prompt_value(
    prompt: str, default: str | None = None, required: bool = True
) -> str:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        if not required:
            return ""
        print("This value is required.")


def _run_setup(config_path: Path, env_path: Path, force_overwrite: bool) -> int:
    default_cfg_path = get_default_config_path()
    default_env_path = get_default_env_path()
    state_db_path, report_dir, log_dir = get_default_sync_paths()

    effective_config_path = config_path
    effective_env_path = env_path
    if config_path == Path("config.yaml"):
        effective_config_path = default_cfg_path
    if env_path == Path(".env"):
        effective_env_path = default_env_path

    if effective_config_path.exists() and not force_overwrite:
        print(f"Config already exists at: {effective_config_path}")
        yn = _prompt_value("Overwrite existing config? (yes/no)", default="no")
        if yn.strip().lower() not in {"y", "yes"}:
            print("Setup cancelled.")
            return 2

    detected = detect_zotero_data_dir()
    default_data_dir = str(detected) if detected else str(Path.home() / "Zotero")
    data_dir = Path(
        _prompt_value("Zotero data directory", default=default_data_dir)
    ).expanduser()
    sqlite_path = Path(
        _prompt_value("Zotero sqlite path", default=str(data_dir / "zotero.sqlite"))
    ).expanduser()
    storage_dir = Path(
        _prompt_value("Zotero storage directory", default=str(data_dir / "storage"))
    ).expanduser()

    database_id = _prompt_value("Notion database ID (UUID)")
    data_source_id = _prompt_value(
        "Notion data source ID (UUID, optional)", default="", required=False
    )
    pdf_property_name = _prompt_value("Notion files property name", default="PDF")
    zotero_uri_property_name = _prompt_value(
        "Notion Zotero URI property name", default="Zotero URI"
    )
    token_env = _prompt_value("Notion token env var name", default="NOTION_TOKEN")

    token_value = _prompt_value("Notion token (starts with secret_)")
    use_keyring = False
    if keyring_available():
        ans = _prompt_value("Store token in OS keychain? (yes/no)", default="yes")
        use_keyring = ans.strip().lower() in {"y", "yes"}

    dry_run_default = "yes"
    dry_run_answer = _prompt_value(
        "Start with dry-run enabled? (yes/no)", default=dry_run_default
    )
    dry_run = dry_run_answer.strip().lower() in {"y", "yes"}

    text = render_setup_config(
        zotero_data_dir=data_dir,
        sqlite_path=sqlite_path,
        storage_dir=storage_dir,
        token_env=token_env,
        database_id=database_id,
        data_source_id=data_source_id,
        pdf_property_name=pdf_property_name,
        zotero_uri_property_name=zotero_uri_property_name,
        dry_run=dry_run,
        state_db_path=state_db_path,
        report_dir=report_dir,
        log_dir=log_dir,
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

    print("Next steps:")
    print("1) Run: noteropdf doctor")
    print("2) Run: noteropdf sync")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

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
        cfg = load_config(Path(args.config), env_for_load)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    logger = logging.getLogger("noteropdf.cli")

    log_path = setup_run_logging(cfg.sync.log_dir, args.command, cfg.sync.log_level)
    logger.info("Run log: %s", log_path)
    started = perf_counter()

    if zotero_maybe_open() and args.command in {
        "sync",
        "rebuild-page-files",
        "full-reset",
    }:
        logger.warning(
            "Zotero appears to be running. Read-only mode is used, but close Zotero for best consistency."
        )

    engine = SyncEngine(cfg)
    try:
        try:
            if args.command == "doctor":
                lines = engine.doctor()
                for line in lines:
                    logger.info(line)
                return 0

            if args.command == "support-bundle":
                doctor_lines: list[str] | None = None
                doctor_error: str | None = None
                try:
                    doctor_lines = engine.doctor()
                except Exception as exc:
                    doctor_error = str(exc)

                requested_config = Path(args.config).expanduser().resolve()
                requested_env = (
                    env_for_load.expanduser().resolve()
                    if env_for_load is not None
                    else (Path(args.config).expanduser().resolve().parent / ".env")
                )
                fallback_config = get_default_config_path().expanduser().resolve()
                fallback_env = get_default_env_path().expanduser().resolve()
                effective_config = (
                    requested_config
                    if requested_config.exists()
                    else (fallback_config if fallback_config.exists() else None)
                )
                effective_env = (
                    requested_env
                    if requested_env.exists()
                    else (fallback_env if fallback_env.exists() else None)
                )

                bundle_path = build_support_bundle(
                    cfg=cfg,
                    output_dir=cfg.sync.report_dir,
                    config_path=effective_config,
                    env_path=effective_env,
                    doctor_lines=doctor_lines,
                    doctor_error=doctor_error,
                    current_run_log=log_path,
                )
                logger.info("Support bundle created: %s", bundle_path)
                logger.info("Share this zip for debugging. Secrets are redacted.")
                return 0

            if args.command == "sync":
                parent_count = engine.estimate_parent_item_count()
                _print_write_preflight(
                    "sync", cfg.notion.pdf_property_name, parent_count, cfg.sync.dry_run
                )
                rows = engine.sync(force=bool(args.force))
                json_path, csv_path, summary_path = write_reports(
                    cfg.sync.report_dir,
                    "sync-force" if args.force else "sync",
                    rows,
                )
                _print_summary(rows)
                logger.info("JSON report: %s", json_path)
                logger.info("CSV report: %s", csv_path)
                logger.info("Summary report: %s", summary_path)
                logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                return 0

            if args.command == "rebuild-page-files":
                if not args.yes:
                    logger.error("Refusing rebuild-page-files without --yes")
                    return 2
                page_count = engine.estimate_known_page_count()
                _print_write_preflight(
                    "rebuild-page-files",
                    cfg.notion.pdf_property_name,
                    page_count,
                    cfg.sync.dry_run,
                )
                if not _confirm_destructive_action(
                    action_label="rebuild-page-files",
                    property_name=cfg.notion.pdf_property_name,
                    page_count=page_count,
                ):
                    return 2
                rows = engine.rebuild_page_files()
                json_path, csv_path, summary_path = write_reports(
                    cfg.sync.report_dir, "rebuild-page-files", rows
                )
                _print_summary(rows)
                logger.info("JSON report: %s", json_path)
                logger.info("CSV report: %s", csv_path)
                logger.info("Summary report: %s", summary_path)
                logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                return 0

            if args.command == "full-reset":
                if not args.yes:
                    logger.error("Refusing full-reset without --yes")
                    return 2
                page_count = engine.estimate_known_page_count()
                _print_write_preflight(
                    "full-reset",
                    cfg.notion.pdf_property_name,
                    page_count,
                    cfg.sync.dry_run,
                )
                if not _confirm_destructive_action(
                    action_label="full-reset",
                    property_name=cfg.notion.pdf_property_name,
                    page_count=page_count,
                ):
                    return 2
                rows = engine.full_reset()
                json_path, csv_path, summary_path = write_reports(
                    cfg.sync.report_dir, "full-reset", rows
                )
                _print_summary(rows)
                logger.info("Local sync state cleared. Run 'sync' for a fresh rebuild.")
                logger.info("JSON report: %s", json_path)
                logger.info("CSV report: %s", csv_path)
                logger.info("Summary report: %s", summary_path)
                logger.info("Elapsed seconds: %.2f", perf_counter() - started)
                return 0

            logger.error("Unknown command: %s", args.command)
            return 2
        except Exception as exc:
            logger.exception("Command failed: %s", exc)
            logger.info("Elapsed seconds: %.2f", perf_counter() - started)
            return 1
    finally:
        engine.close()
