from pathlib import Path
from types import SimpleNamespace

from noteropdf.models import SyncRow
from noteropdf.cli import _build_parser, main


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
        sync=SimpleNamespace(log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False),
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
        sync=SimpleNamespace(log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )

    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: "CONFIRM 2")
    monkeypatch.setattr("noteropdf.cli.write_reports", lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")))

    code = main(["full-reset", "--yes"])
    assert code == 0


def test_rebuild_cancelled_when_confirmation_text_does_not_match(monkeypatch):
    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def estimate_known_page_count(self):
            return 4

        def rebuild_page_files(self):
            raise AssertionError("rebuild_page_files should not run when confirmation fails")

        def close(self):
            return None

    cfg = SimpleNamespace(
        sync=SimpleNamespace(log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False),
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
        sync=SimpleNamespace(log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    code = main(["doctor"])
    assert code == 0


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
        sync=SimpleNamespace(log_dir=Path("."), log_level="INFO", report_dir=Path("."), dry_run=False),
        notion=SimpleNamespace(pdf_property_name="PDF"),
    )
    monkeypatch.setattr("noteropdf.cli.load_config", lambda *_: cfg)
    monkeypatch.setattr("noteropdf.cli.setup_run_logging", lambda *_: Path("log.txt"))
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli.zotero_maybe_open", lambda: False)
    monkeypatch.setattr("noteropdf.cli.write_reports", lambda *_: (Path("a.json"), Path("a.csv"), Path("a-summary.json")))

    code = main(["sync"])
    assert code == 0
