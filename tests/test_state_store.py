from pathlib import Path

import pytest

from noteropdf.state_store import StateStore


def test_state_store_lock_prevents_second_instance(tmp_path: Path):
    db_path = tmp_path / "state.sqlite3"

    first = StateStore(db_path)
    try:
        with pytest.raises(RuntimeError, match="Sync state lock exists"):
            StateStore(db_path)
    finally:
        first.close()

    second = StateStore(db_path)
    second.close()
