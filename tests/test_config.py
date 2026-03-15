from pathlib import Path

import pytest

from noteropdf.config import load_config


CONFIG_TEMPLATE = """
zotero:
  data_dir: "./zotero"
  sqlite_path: "./zotero/zotero.sqlite"
  storage_dir: "./zotero/storage"

notion:
  token_env: "NOTION_TOKEN"
  notion_version: "2026-03-11"
  database_id: "3180e681-3c44-8198-9a97-e4532809e30e"
  data_source_id: ""
  pdf_property_name: "PDF"
  zotero_uri_property_name: "Zotero URI"

sync:
  state_db_path: "./state.sqlite3"
  report_dir: "./reports"
  log_dir: "./logs"
  log_level: "INFO"
  max_simple_upload_mb: 20
  max_supported_mb: 25
"""
VALID_TOKEN = "secret_test_token_that_is_long_enough_12345"


def test_load_config_resolves_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    cfg = load_config(cfg_path, env_path)

    assert cfg.sync.state_db_path == (tmp_path / "state.sqlite3").resolve()
    assert cfg.sync.report_dir == (tmp_path / "reports").resolve()
    assert cfg.sync.log_dir == (tmp_path / "logs").resolve()
    assert cfg.zotero.data_dir == (tmp_path / "zotero").resolve()


def test_invalid_notion_id_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG_TEMPLATE.replace("3180e681-3c44-8198-9a97-e4532809e30e", "not-a-uuid"), encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="notion.database_id"):
        load_config(cfg_path, env_path)


def test_upload_limits_validation(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    bad = CONFIG_TEMPLATE.replace("max_simple_upload_mb: 20", "max_simple_upload_mb: 30")
    bad = bad.replace("max_supported_mb: 25", "max_supported_mb: 20")
    cfg_path.write_text(bad, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="max_simple_upload_mb"):
        load_config(cfg_path, env_path)


def test_continue_on_error_defaults_to_false(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    no_continue_flag = CONFIG_TEMPLATE.replace("  continue_on_error: true\n", "")
    cfg_path.write_text(no_continue_flag, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.sync.continue_on_error is False


def test_invalid_log_level_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    bad = CONFIG_TEMPLATE.replace('log_level: "INFO"', 'log_level: "TRACE"')
    cfg_path.write_text(bad, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sync.log_level"):
        load_config(cfg_path, env_path)


def test_same_notion_property_names_fail(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    bad = CONFIG_TEMPLATE.replace('zotero_uri_property_name: "Zotero URI"', 'zotero_uri_property_name: "PDF"')
    cfg_path.write_text(bad, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be different fields"):
        load_config(cfg_path, env_path)


def test_string_boolean_values_are_parsed_for_sync_flags(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    custom = CONFIG_TEMPLATE + '  dry_run: "true"\n  continue_on_error: "no"\n'
    cfg_path.write_text(custom, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    cfg = load_config(cfg_path, env_path)

    assert cfg.sync.dry_run is True
    assert cfg.sync.continue_on_error is False


def test_invalid_boolean_value_fails(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    custom = CONFIG_TEMPLATE + '  dry_run: "maybe"\n'
    cfg_path.write_text(custom, encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(f"NOTION_TOKEN={VALID_TOKEN}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sync.dry_run"):
        load_config(cfg_path, env_path)
