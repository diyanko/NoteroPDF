from pathlib import Path

import pytest

from noteropdf.config import (
    SetupRequired,
    build_app_config,
    detect_zotero_data_dir,
    get_default_state_db_path,
    load_app_config,
    validate_zotero_data_dir,
)
from noteropdf.settings import LocalSettings


def _zotero_dir(tmp_path: Path) -> Path:
    path = tmp_path / "Zotero"
    path.mkdir(parents=True)
    (path / "zotero.sqlite").write_bytes(b"")
    (path / "storage").mkdir()
    return path


def test_validate_zotero_data_dir_requires_database_and_storage(tmp_path: Path):
    path = tmp_path / "Zotero"
    path.mkdir()
    with pytest.raises(ValueError, match="database not found"):
        validate_zotero_data_dir(path)
    (path / "zotero.sqlite").write_bytes(b"")
    with pytest.raises(ValueError, match="storage folder"):
        validate_zotero_data_dir(path)


def test_build_app_config_uses_internal_ids(tmp_path: Path):
    zotero = _zotero_dir(tmp_path)
    settings = LocalSettings(
        zotero_data_dir=zotero,
        notion_data_source_id="source",
        pdf_property_id="pdf-id",
    )
    cfg = build_app_config(
        settings,
        token="token-value",
        token_source="keyring",
        state_db_path=tmp_path / "state.sqlite3",
    )
    assert cfg.zotero.data_dir == zotero
    assert cfg.notion.data_source_id == "source"
    assert cfg.notion.pdf_property_id == "pdf-id"
    assert cfg.notion_token_source == "keyring"


def test_default_state_path_uses_the_0_4_database_name():
    assert get_default_state_db_path().name == "noteropdf.sqlite3"


def test_build_app_config_requires_completed_connection(tmp_path: Path):
    zotero = _zotero_dir(tmp_path)
    with pytest.raises(SetupRequired, match="Notion"):
        build_app_config(
            LocalSettings(zotero_data_dir=zotero),
            token="token",
            token_source="keyring",
        )


def test_detect_zotero_data_dir_reads_custom_profile(monkeypatch, tmp_path: Path):
    profile_root = tmp_path / "profiles"
    profile = profile_root / "abc.default"
    profile.mkdir(parents=True)
    custom = _zotero_dir(tmp_path / "custom-parent")
    (profile / "prefs.js").write_text(
        'user_pref("extensions.zotero.useDataDir", true);\n'
        f'user_pref("extensions.zotero.dataDir", "{custom}");\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "noteropdf.config.default_zotero_profile_root_candidates",
        lambda: [profile_root],
    )
    monkeypatch.setattr("noteropdf.config.default_zotero_data_dir_candidates", list)
    assert detect_zotero_data_dir() == custom.resolve()


def test_load_app_config_uses_saved_personal_access_token(monkeypatch, tmp_path: Path):
    zotero = _zotero_dir(tmp_path)
    settings = LocalSettings(
        zotero_data_dir=zotero,
        notion_data_source_id="source",
        pdf_property_id="pdf-id",
    )

    class Store:
        def load(self):
            return "personal-access-token"

    monkeypatch.setattr("noteropdf.config.load_local_settings", lambda _path: settings)

    cfg = load_app_config(
        state_db_path=tmp_path / "state.sqlite3",
        credential_store=Store(),
    )

    assert cfg.notion_token == "personal-access-token"
    assert cfg.notion_token_source == "keyring"
