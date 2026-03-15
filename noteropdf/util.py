from __future__ import annotations

from pathlib import Path
import hashlib
import re
import subprocess


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


def zotero_maybe_open() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Zotero"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False
