from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

from .config import get_default_state_db_path
from .state_store import FileLock
from .util import canonical_notion_id


def destination_key(data_source_id: str, property_id: str) -> str:
    """Return a canonical, non-secret identity for one files destination."""
    identity = json.dumps(
        [
            canonical_notion_id(data_source_id),
            canonical_notion_id(property_id, encoded=True),
        ],
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
