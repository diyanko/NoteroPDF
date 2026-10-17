import logging
import os
import stat
from pathlib import Path

from noteropdf.logging_setup import setup_run_logging


def test_diagnostic_log_is_rotating_and_owner_only(tmp_path: Path):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        log_path = setup_run_logging(tmp_path, no_color=True)
        logging.getLogger("noteropdf.test").info("hello")
        for handler in root.handlers:
            handler.flush()

        assert log_path.read_text(encoding="utf-8").endswith("hello\n")
        file_handler = next(
            handler
            for handler in root.handlers
            if getattr(handler, "baseFilename", None)
        )
        assert file_handler.maxBytes == 1_000_000
        assert file_handler.backupCount == 3
        if os.name != "nt":
            assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root.addHandler(handler)


def test_named_console_scopes_engine_recovery_without_mutating_record():
    from noteropdf.logging_setup import _UserConsoleFormatter

    record = logging.LogRecord(
        "noteropdf.sync",
        logging.WARNING,
        "",
        0,
        "Sync failed: %s",
        ("Run `noteropdf connect` to replace the token.",),
        None,
    )
    formatter = _UserConsoleFormatter(use_color=False, verbose=False, profile="lab")
    assert formatter.format(record) == (
        "[WARN] Sync failed: Run `noteropdf connect --profile lab` to replace the token."
    )
    assert (
        record.getMessage()
        == "Sync failed: Run `noteropdf connect` to replace the token."
    )
    default = _UserConsoleFormatter(use_color=False, verbose=False)
    assert default.format(record) == f"[WARN] {record.getMessage()}"
