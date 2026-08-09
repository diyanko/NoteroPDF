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
