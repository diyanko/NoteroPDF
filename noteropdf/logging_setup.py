from __future__ import annotations

import logging
import os
import sys
from copy import copy
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from .profiles import normalize_profile, recovery_guidance


class _ApplicationRecordsOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTP libraries can log request URLs containing private object IDs.
        return record.name == "noteropdf" or record.name.startswith("noteropdf.")


class _DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Tracebacks include local source paths and arbitrary exception text.
        safe = copy(record)
        safe.exc_info = None
        safe.exc_text = None
        safe.stack_info = None
        return super().format(safe)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Create diagnostic logs with owner-only permissions where supported."""

    def handleError(self, record: logging.LogRecord) -> None:
        # Logging failures must not invoke the standard traceback/argument dump.
        try:
            sys.stderr.write(
                "[WARN] The diagnostic log could not be written. Check local folder permissions.\n"
            )
        except OSError:
            pass

    def _open(self):
        stream = super()._open()
        try:
            Path(self.baseFilename).chmod(0o600)
        except OSError:
            pass
        return stream


class _UserConsoleFormatter(logging.Formatter):
    COLORS: ClassVar[dict[str, str]] = {
        "[OK]": "\033[32m",
        "[WARN]": "\033[33m",
        "[ERROR]": "\033[31m",
        "[NEXT]": "\033[36m",
        "[INFO]": "\033[36m",
        "[DEBUG]": "\033[2m",
    }
    RESET = "\033[0m"

    def __init__(self, *, use_color: bool, verbose: bool, profile: str | None = None):
        super().__init__()
        self.use_color = use_color
        self.verbose = verbose
        self.profile = normalize_profile(profile)

    def format(self, record: logging.LogRecord) -> str:
        message = recovery_guidance(record.getMessage(), self.profile)
        if record.levelno >= logging.ERROR and not message.startswith("[ERROR]"):
            message = f"[ERROR] {message}"
        elif record.levelno >= logging.WARNING and not message.startswith("[WARN]"):
            message = f"[WARN] {message}"
        elif self.verbose and record.levelno <= logging.DEBUG:
            message = f"[DEBUG] {record.name}: {message}"

        if not self.use_color:
            return message

        for label, color in self.COLORS.items():
            if message.startswith(label):
                return f"{color}{label}{self.RESET}{message[len(label) :]}"
        return message


def _should_use_color(no_color: bool) -> bool:
    if no_color or os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "").strip().lower() == "dumb":
        return False
    stream = sys.stderr
    return bool(getattr(stream, "isatty", lambda: False)())


def setup_console_logging(
    no_color: bool = False,
    verbose: bool = False,
    *,
    profile: str | None = None,
) -> None:
    """Configure the same private output policy even when no log file opens."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.addFilter(_ApplicationRecordsOnly())
    stream_handler.setFormatter(
        _UserConsoleFormatter(
            use_color=_should_use_color(no_color), verbose=verbose, profile=profile
        )
    )
    root.addHandler(stream_handler)


def setup_run_logging(
    log_dir: Path,
    no_color: bool = False,
    verbose: bool = False,
    *,
    profile: str | None = None,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "noteropdf.log"
    setup_console_logging(no_color, verbose, profile=profile)
    file_handler = _PrivateRotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(_ApplicationRecordsOnly())
    file_handler.setFormatter(
        _DiagnosticFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    return log_path
