from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import unquote
from uuid import UUID

from .config import get_default_state_db_path
from .state_store import FileLock


def _canonical_id(value: str, *, encoded: bool = False) -> str:
    value = value.strip()
    if encoded:
        value = unquote(value, errors="strict")
    if not value:
        raise ValueError("A Notion destination ID is required for safe sync.")
    try:
        return UUID(value).hex
    except ValueError:
        # Short property IDs are case-sensitive opaque strings.
        return value


def destination_key(data_source_id: str, property_id: str) -> str:
    """Return a canonical, non-secret identity for one files destination."""
    identity = json.dumps(
        [_canonical_id(data_source_id), _canonical_id(property_id, encoded=True)],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


@contextmanager
def lock_destination(
    data_source_id: str, property_id: str, *, apply: bool
) -> Iterator[None]:
    """Serialize local writers independently of their profile or credentials."""
    if not apply:
        yield
        return
    digest = destination_key(data_source_id, property_id)
    path = get_default_state_db_path().parent / "destination-locks" / f"{digest}.lock"
    try:
        lock = FileLock(path)
    except RuntimeError:
        raise RuntimeError(
            "Another NoteroPDF run is writing to this Notion destination. "
            "Wait for it to finish, then preview again."
        ) from None
    except OSError:
        raise RuntimeError(
            "Could not lock the Notion destination. Check access to local app "
            "storage and try again."
        ) from None
    try:
        yield
    finally:
        lock.close()
