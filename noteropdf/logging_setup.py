from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


class _UserConsoleFormatter(logging.Formatter):
    COLORS = {
        "[OK]": "\033[32m",
        "[WARN]": "\033[33m",
        "[ERROR]": "\033[31m",
        "[NEXT]": "\033[36m",
        "[INFO]": "\033[36m",
        "[DEBUG]": "\033[2m",
    }
    RESET = "\033[0m"

    def __init__(self, *, use_color: bool, verbose: bool):
        super().__init__()
        self.use_color = use_color
        self.verbose = verbose

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
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
                return f"{color}{label}{self.RESET}{message[len(label):]}"
        return message


def _should_use_color(no_color: bool) -> bool:
    if no_color or os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "").strip().lower() == "dumb":
        return False
    stream = sys.stderr
    return bool(getattr(stream, "isatty", lambda: False)())


def setup_run_logging(
    log_dir: Path,
    command_name: str,
    log_level: str,
    no_color: bool = False,
    verbose: bool = False,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"{command_name}-{ts}.log"

    level_name = (log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else level)
    stream_handler.setFormatter(
        _UserConsoleFormatter(use_color=_should_use_color(no_color), verbose=verbose)
    )
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return log_path
