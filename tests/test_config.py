from pathlib import Path

import pytest

from noteropdf.config import detect_zotero_data_dir, load_config

CONFIG_TEMPLATE = """
zotero:
  data_dir: "./zotero"

notion:
  token_env: "NOTION_TOKEN"
  database_id: "3180e681-3c44-8198-9a97-e4532809e30e"
  data_source_id: ""
  pdf_property_name: "PDF"
  zotero_uri_property_name: "Zotero URI"

sync:
  dry_run: false
"""
VALID_TOKEN = "secret_test_token_that_is_long_enough_12345"


def test_load_config_resolves_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    cfg = load_config(cfg_path, env_path)

    assert cfg.zotero.data_dir == (tmp_path / "zotero").resolve()
    assert cfg.zotero.sqlite_path == (tmp_path / "zotero" / "zotero.sqlite").resolve()
    assert cfg.zotero.storage_dir == (tmp_path / "zotero" / "storage").resolve()
    assert cfg.sync.state_db_path.name == "sync-state.sqlite3"
    assert cfg.sync.report_dir.name == "reports"
    assert cfg.sync.log_dir.name == "runs"


def test_invalid_notion_id_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace("3180e681-3c44-8198-9a97-e4532809e30e", "not-a-uuid"),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="notion.database_id"):
        load_config(cfg_path, env_path)


def test_notion_database_url_is_normalized(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace(
            'database_id: "3180e681-3c44-8198-9a97-e4532809e30e"',
            'database_id: "https://www.notion.so/3180e6813c4481989a97e4532809e30e"',
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.notion.database_id == "3180e681-3c44-8198-9a97-e4532809e30e"


def test_notion_data_source_id_only_config_loads(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace(
            'database_id: "3180e681-3c44-8198-9a97-e4532809e30e"',
            'database_id: ""',
        ).replace(
            'data_source_id: ""',
            'data_source_id: "cc60e681-3c44-83c3-a31e-878c0824d6ac"',
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.notion.database_id == ""
    assert cfg.notion.data_source_id == "cc60e681-3c44-83c3-a31e-878c0824d6ac"


def test_notion_data_source_url_is_normalized(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace(
            'data_source_id: ""',
            'data_source_id: "collection://cc60e681-3c44-83c3-a31e-878c0824d6ac"',
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.notion.data_source_id == "cc60e681-3c44-83c3-a31e-878c0824d6ac"


def test_blank_database_and_data_source_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace(
            'database_id: "3180e681-3c44-8198-9a97-e4532809e30e"',
            'database_id: ""',
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="database_id or notion.data_source_id"):
        load_config(cfg_path, env_path)


def test_invalid_log_level_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE + '  log_level: "TRACE"\n',
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sync.log_level"):
        load_config(cfg_path, env_path)


def test_same_notion_property_names_fail(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE.replace(
            'zotero_uri_property_name: "Zotero URI"',
            'zotero_uri_property_name: "PDF"',
        ),
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be different fields"):
        load_config(cfg_path, env_path)


def test_string_boolean_values_are_parsed_for_sync_flags(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        CONFIG_TEMPLATE + '  dry_run: "true"\n',
        encoding="utf-8",
    )

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.sync.dry_run is True


def test_invalid_boolean_value_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE + '  dry_run: "maybe"\n', encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sync.dry_run"):
        load_config(cfg_path, env_path)


def test_load_config_uses_keyring_token_when_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setattr(
        "noteropdf.config.load_token_from_keyring",
        lambda token_env: VALID_TOKEN if token_env == "NOTION_TOKEN" else "",
    )

    cfg = load_config(cfg_path, env_path)

    assert cfg.notion_token == VALID_TOKEN
    assert cfg.notion_token_source == "keyring"


def test_detect_zotero_data_dir_prefers_custom_profile_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    custom_data_dir = tmp_path / "CustomZotero"
    custom_data_dir.mkdir()
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    profile_dir = profile_root / "abc.default"
    profile_dir.mkdir()
    (profile_dir / "prefs.js").write_text(
        '\n'.join(
            [
                'user_pref("extensions.zotero.useDataDir", true);',
                'user_pref("extensions.zotero.dataDir", "'
                + str(custom_data_dir).replace("\\", "\\\\")
                + '");',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "noteropdf.config.default_zotero_profile_root_candidates",
        lambda: [profile_root],
    )
    monkeypatch.setattr(
        "noteropdf.config.default_zotero_data_dir_candidates",
        lambda: [],
    )

    assert detect_zotero_data_dir() == custom_data_dir.resolve()


def test_detect_zotero_data_dir_reads_profiles_ini_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    custom_data_dir = tmp_path / "CustomZotero"
    custom_data_dir.mkdir()
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    profiles_dir = profile_root / "Profiles"
    profiles_dir.mkdir()
    profile_dir = profiles_dir / "abc.default"
    profile_dir.mkdir()
    (profile_root / "profiles.ini").write_text(
        "\n".join(
            [
                "[Profile0]",
                "Name=default",
                "IsRelative=1",
                "Path=Profiles/abc.default",
            ]
        ),
        encoding="utf-8",
    )
    (profile_dir / "prefs.js").write_text(
        '\n'.join(
            [
                'user_pref("extensions.zotero.useDataDir", true);',
                'user_pref("extensions.zotero.dataDir", "'
                + str(custom_data_dir).replace("\\", "\\\\")
                + '");',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "noteropdf.config.default_zotero_profile_root_candidates",
        lambda: [profile_root],
    )
    monkeypatch.setattr(
        "noteropdf.config.default_zotero_data_dir_candidates",
        lambda: [],
    )

    assert detect_zotero_data_dir() == custom_data_dir.resolve()


def test_load_config_uses_default_config_and_env_when_requested_path_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    default_config = tmp_path / "app-config" / "config.yaml"
    default_config.parent.mkdir(parents=True)
    default_config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    default_env = tmp_path / "app-config" / ".env"
    default_env.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    monkeypatch.setattr("noteropdf.config.get_default_config_path", lambda: default_config)
    monkeypatch.setattr("noteropdf.config.get_default_env_path", lambda: default_env)

    cfg = load_config(Path("config.yaml"), allow_default_config_fallback=True)

    assert cfg.notion_token == VALID_TOKEN
    assert cfg.zotero.data_dir == (default_config.parent / "zotero").resolve()


def test_load_config_does_not_use_default_config_for_explicit_missing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    default_config = tmp_path / "app-config" / "config.yaml"
    default_config.parent.mkdir(parents=True)
    default_config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    default_env = tmp_path / "app-config" / ".env"
    default_env.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")
    missing_config = tmp_path / "custom" / "config.yaml"

    monkeypatch.setattr("noteropdf.config.get_default_config_path", lambda: default_config)
    monkeypatch.setattr("noteropdf.config.get_default_env_path", lambda: default_env)

    with pytest.raises(FileNotFoundError, match="custom"):
        load_config(missing_config)
