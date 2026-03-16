from pathlib import Path
from types import SimpleNamespace

from noteropdf.cli import _build_parser, main
from noteropdf.models import SyncRow


def test_sync_force_flag_is_parsed():
    parser = _build_parser()

    args = parser.parse_args(["sync", "--force"])

    assert args.command == "sync"
    assert args.force is True


def test_full_reset_requires_explicit_confirmation_flag_at_parse_level():
    parser = _build_parser()

    args = parser.parse_args(["full-reset", "--yes"])

    assert args.command == "full-reset"
    assert args.yes is True


def test_rebuild_requires_yes_flag_at_parse_level():
    parser = _build_parser()
    args = parser.parse_args(["rebuild-page-files", "--yes"])
    assert args.command == "rebuild-page-files"
    assert args.yes is True


def test_setup_command_is_parsed():
    parser = _build_parser()
    args = parser.parse_args(["setup", "--yes"])
    assert args.command == "setup"
    assert args.yes is True


def test_support_bundle_command_is_parsed():
    parser = _build_parser()
    args = parser.parse_args(["support-bundle"])
    assert args.command == "support-bundle"


def test_support_bundle_command_runs(monkeypatch):
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

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr(
        "noteropdf.cli.build_support_bundle", lambda **_: Path("bundle.zip")
    )
    monkeypatch.setattr(
        "noteropdf.cli.get_default_config_path", lambda: Path("config.yaml")
    )
    monkeypatch.setattr("noteropdf.cli.get_default_env_path", lambda: Path(".env"))

    code = main(["support-bundle"])
    assert code == 0


def test_setup_writes_config_and_env(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    data_dir = tmp_path / "Zotero"

    answers = iter(
        [
            str(data_dir),
            "",
            "",
            "3180e681-3c44-8198-9a97-e4532809e30e",
            "",
            "",
            "",
            "",
            "secret_test_token_that_is_long_enough_12345",
            "yes",
        ]
    )

    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: data_dir)
    monkeypatch.setattr("noteropdf.cli.keyring_available", lambda: False)

    code = main(["--config", str(cfg_path), "--env", str(env_path), "setup", "--yes"])
    assert code == 0
    assert cfg_path.exists()
    assert env_path.exists()

    cfg_text = cfg_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    assert "database_id: 3180e681-3c44-8198-9a97-e4532809e30e" in cfg_text
    assert "token_env: NOTION_TOKEN" in cfg_text
    assert "NOTION_TOKEN=secret_test_token_that_is_long_enough_12345" in env_text


def test_full_reset_cancelled_when_confirmation_text_does_not_match(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_known_page_count(self):
            return 3

        def full_reset(self):
            raise AssertionError("full_reset should not run when confirmation fails")

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: "WRONG")

    code = main(["full-reset", "--yes"])
    assert code == 2


def test_full_reset_runs_when_confirmation_matches(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_known_page_count(self):
            return 2

        def full_reset(self):
            return []

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: "CONFIRM 2")
    monkeypatch.setattr(
        "noteropdf.cli.write_reports",
        lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")),
    )

    code = main(["full-reset", "--yes"])
    assert code == 0


def test_rebuild_cancelled_when_confirmation_text_does_not_match(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_known_page_count(self):
            return 4

        def rebuild_page_files(self):
            raise AssertionError(
                "rebuild_page_files should not run when confirmation fails"
            )

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(
            log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False
        ),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: "nope")

    code = main(["rebuild-page-files", "--yes"])
    assert code == 2


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

    def _fake_load_config(config_path, env_path):
        calls["env_path"] = env_path
        return cfg

    monkeypatch.setattr("noteropdf.cli.load_config", _fake_load_config)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    code = main(["doctor"])
    assert code == 0
    assert calls["env_path"] is None


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
    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr(
        "noteropdf.cli.write_reports",
        lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")),
    )

    code = main(["sync"])
    assert code == 0
