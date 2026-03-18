from pathlib import Path
from types import SimpleNamespace

from noteropdf.cli import _build_parser, main
from noteropdf.models import SyncRow
from noteropdf.notion_client import NotionApiError, NotionTarget


def test_public_help_only_shows_setup_doctor_and_sync():
    parser = _build_parser()
    help_text = parser.format_help()

    assert "{setup,doctor,sync}" in help_text
    assert "support-bundle" not in help_text
    assert "rebuild-page-files" not in help_text
    assert "full-reset" not in help_text
    assert "--force" not in help_text


def test_sync_force_flag_is_parsed():
    parser = _build_parser()

    args = parser.parse_args(["sync", "--force"])

    assert args.command == "sync"
    assert args.force is True


def test_setup_command_is_parsed():
    parser = _build_parser()
    args = parser.parse_args(["setup", "--yes"])
    assert args.command == "setup"
    assert args.yes is True


def test_setup_writes_config_and_env(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "yes",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return [
                NotionTarget(
                    data_source_id="cc60e681-3c44-83c3-a31e-878c0824d6ac",
                    label="Research Library",
                    database_id="3180e681-3c44-8198-9a97-e4532809e30e",
                    url="https://www.notion.so/3180e6813c4481989a97e4532809e30e",
                )
            ]

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])
    assert code == 0
    assert cfg_path.exists()
    assert env_path.exists()

    cfg_text = cfg_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    assert "database_id: 3180e681-3c44-8198-9a97-e4532809e30e" in cfg_text
    assert "data_source_id: cc60e681-3c44-83c3-a31e-878c0824d6ac" in cfg_text
    assert "token_env: NOTION_TOKEN" in cfg_text
    assert "sqlite_path" not in cfg_text
    assert "storage_dir" not in cfg_text
    assert "NOTION_TOKEN=secret_test_token_that_is_long_enough_12345" in env_text


def test_setup_handles_multiple_discovered_data_sources(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "9",
            "abc",
            "2",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return [
                NotionTarget(
                    data_source_id="cc60e681-3c44-83c3-a31e-878c0824d6ac",
                    label="Alpha",
                    database_id="3180e681-3c44-8198-9a97-e4532809e30e",
                    url=None,
                ),
                NotionTarget(
                    data_source_id="dd70e681-3c44-83c3-a31e-878c0824d6ad",
                    label="Beta",
                    database_id="4180e681-3c44-8198-9a97-e4532809e30f",
                    url=None,
                ),
            ]

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "database_id: 4180e681-3c44-8198-9a97-e4532809e30f" in cfg_text
    assert "data_source_id: dd70e681-3c44-83c3-a31e-878c0824d6ad" in cfg_text


def test_setup_allows_manual_override_when_single_target_is_discovered(
    monkeypatch, tmp_path: Path
):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "no",
            "https://www.notion.so/4180e6813c4481989a97e4532809e30f",
            "",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return [
                NotionTarget(
                    data_source_id="cc60e681-3c44-83c3-a31e-878c0824d6ac",
                    label="Auto Pick",
                    database_id="3180e681-3c44-8198-9a97-e4532809e30e",
                    url=None,
                )
            ]

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "database_id: 4180e681-3c44-8198-9a97-e4532809e30f" in cfg_text
    assert "data_source_id: ''" in cfg_text


def test_setup_falls_back_to_manual_target_entry_when_discovery_returns_none(
    monkeypatch, tmp_path: Path
):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "https://www.notion.so/3180e6813c4481989a97e4532809e30e",
            "",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "database_id: 3180e681-3c44-8198-9a97-e4532809e30e" in cfg_text


def test_setup_accepts_collection_url_in_database_prompt(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "collection://cc60e681-3c44-83c3-a31e-878c0824d6ac",
            "",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "database_id: ''" in cfg_text
    assert "data_source_id: cc60e681-3c44-83c3-a31e-878c0824d6ac" in cfg_text


def test_setup_falls_back_to_manual_target_entry_on_discovery_error(
    monkeypatch, tmp_path: Path
):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "",
            "cc60e681-3c44-83c3-a31e-878c0824d6ac",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            raise NotionApiError("NOTION_API_ERROR", "search failed")

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    cfg_text = cfg_path.read_text(encoding="utf-8")
    assert "data_source_id: cc60e681-3c44-83c3-a31e-878c0824d6ac" in cfg_text


def test_setup_cancelled_when_existing_config_is_not_overwritten(
    monkeypatch, tmp_path: Path
):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("existing: true\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda *_: "no")

    code = main(["--config", str(cfg_path), "setup"])

    assert code == 2
    assert cfg_path.read_text(encoding="utf-8") == "existing: true\n"


def test_setup_replaces_existing_env_token_instead_of_appending_duplicate(
    monkeypatch, tmp_path: Path
):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NOTION_TOKEN=old_value\nOTHER_VAR=keep_me\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "https://www.notion.so/3180e6813c4481989a97e4532809e30e",
            "",
            "",
            "",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    env_text = env_path.read_text(encoding="utf-8")
    assert env_text.count("NOTION_TOKEN=") == 1
    assert "NOTION_TOKEN=secret_test_token_that_is_long_enough_12345" in env_text
    assert "OTHER_VAR=keep_me" in env_text


def test_setup_uses_keyring_when_available(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "secret_test_token_that_is_long_enough_12345",
            "https://www.notion.so/3180e6813c4481989a97e4532809e30e",
            "",
            "",
            "",
            "yes",
            "yes",
        ]
    )

    class FakeNotionClient:
        def __init__(self, *args, **kwargs):
            return None

        def list_accessible_data_sources(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: True)
    monkeypatch.setattr("noteropdf.cli.store_token_in_keyring", lambda *_: True)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotionClient)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])

    assert code == 0
    assert cfg_path.exists()
    assert not env_path.exists()


def test_doctor_command_runs_and_returns_zero(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def doctor(self):
            return ["ok"]

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    calls: dict[str, object] = {}

    def _fake_load_config(
        config_path, env_path, *, allow_default_config_fallback=False
    ):
        calls["env_path"] = env_path
        calls["allow_default_config_fallback"] = allow_default_config_fallback
        return cfg

    monkeypatch.setattr("noteropdf.cli.load_config", _fake_load_config)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    code = main(["doctor"])
    assert code == 0
    assert calls["env_path"] is None
    assert calls["allow_default_config_fallback"] is True


def test_doctor_command_treats_explicit_config_flag_as_explicit_path(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def doctor(self):
            return ["ok"]

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    calls: dict[str, object] = {}

    def _fake_load_config(
        config_path, env_path, *, allow_default_config_fallback=False
    ):
        calls["allow_default_config_fallback"] = allow_default_config_fallback
        return cfg

    monkeypatch.setattr("noteropdf.cli.load_config", _fake_load_config)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    code = main(["--config", "config.yaml", "doctor"])

    assert code == 0
    assert calls["allow_default_config_fallback"] is False


def test_sync_command_runs_with_preflight_and_reports(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_parent_item_count(self):
            return 5

        def sync(self, *, force=False):
            assert force is False
            return [
                SyncRow(
                    zotero_item_key="A",
                    title="Paper",
                    zotero_uri="zotero://select/library/items/A",
                    notion_page_id="page-1",
                    notion_page_url="https://notion.so/page-1",
                    local_pdf_path="/tmp/a.pdf",
                    action_taken="upload_attach:first_sync",
                    final_status="OK",
                    error_message=None,
                )
            ]

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_, **__: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr(
        "noteropdf.cli.write_reports",
        lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")),
    )

    code = main(["sync"])
    assert code == 0


def test_sync_command_passes_force_to_engine(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_parent_item_count(self):
            return 2

        def sync(self, *, force=False):
            assert force is True
            return []

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_, **__: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr(
        "noteropdf.cli.write_reports",
        lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")),
    )

    code = main(["sync", "--force"])

    assert code == 0


def test_main_rejects_python_below_supported_range(monkeypatch):
    monkeypatch.setattr("sys.version_info", (3, 10, 12, "final", 0))

    code = main(["doctor"])

    assert code == 2


def test_main_rejects_python_above_supported_range(monkeypatch):
    monkeypatch.setattr("sys.version_info", (3, 14, 0, "final", 0))

    code = main(["doctor"])

    assert code == 2
