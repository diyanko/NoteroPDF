from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from ast import literal_eval
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_notion_page_id_from_url(url: str) -> str | None:
    # Accept canonical UUIDs or Notion page URLs that contain a 32-hex page id.
    canonical = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        url,
    )
    if canonical:
        return canonical.group(1).lower()

    compact = re.search(r"([0-9a-fA-F]{32})(?:[/?#]|$)", url)
    if not compact:
        return None

    value = compact.group(1).lower()
    return f"{value[0:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"


def normalize_notion_id_input(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    parsed = parse_notion_page_id_from_url(raw)
    return parsed or raw


def is_notion_data_source_input(value: str) -> bool:
    return value.strip().lower().startswith("collection://")


def normalize_notion_target_inputs(
    database_input: str, data_source_input: str
) -> tuple[str, str]:
    raw_database = database_input.strip()
    raw_data_source = data_source_input.strip()

    if not raw_data_source and is_notion_data_source_input(raw_database):
        raw_database, raw_data_source = "", raw_database

    return normalize_notion_id_input(raw_database), normalize_notion_id_input(
        raw_data_source
    )


def unescape_js_string_literal(value: str) -> str:
    try:
        return literal_eval(f'"{value}"')
    except (SyntaxError, ValueError):
        return value


def zotero_maybe_open() -> bool:
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Zotero.exe", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
            return "zotero.exe" in result.stdout.lower()
        else:
            result = subprocess.run(
                ["pgrep", "-x", "Zotero"],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
    except Exception:
        return False
