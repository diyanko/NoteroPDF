from pathlib import Path
from types import SimpleNamespace

import pytest

from noteropdf import __version__
from noteropdf.auth import NOTION_TOKEN_PAGE_URL
from noteropdf.cli import (
    _authorize_notion,
    _build_parser,
    _print_summary,
    _run_connect,
    _run_doctor,
    _run_sync,
    _select_pdf_property,
    _select_target,
    _select_zotero_directory,
    main,
)
from noteropdf.models import SyncRow
from noteropdf.notion_client import (
    NotionApiError,
    NotionProperty,
    NotionTarget,
)
from noteropdf.settings import LocalSettings
from noteropdf.sync_engine import PreviewAction


def _row(action: str, status: str = "OK") -> SyncRow:
    return SyncRow(
        zotero_item_key="ABC",
        title="Paper",
        zotero_uri="zotero://select/library/items/ABC",
        notion_page_id="page",
        notion_page_url=None,
        local_pdf_path="paper.pdf",
        action_taken=action,
        final_status=status,
        error_message=None,
    )


def test_parser_has_small_public_surface_and_default_sync():
    parser = _build_parser()
    assert parser.parse_args([]).command is None
    assert parser.parse_args(["sync", "--apply"]).apply is True
    assert parser.parse_args(["--verbose", "sync"]).verbose is True
    assert parser.parse_args(["sync", "--verbose"]).verbose is True
    assert parser.parse_args(["connect"]).command == "connect"
    assert parser.parse_args(["doctor"]).command == "doctor"
    help_text = parser.format_help()
    assert "{sync,connect,doctor}" in help_text
    assert "cleanup" not in help_text
    assert "setup" not in help_text


def test_parser_reports_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out == f"noteropdf {__version__}\n"


def test_main_without_command_runs_sync(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(
        "noteropdf.cli._run_sync", lambda **kwargs: calls.append(kwargs) or 0
    )
    monkeypatch.setattr(
        "noteropdf.cli.setup_run_logging", lambda *args, **kwargs: tmp_path / "log"
    )
    assert main([]) == 0
    assert calls == [{"apply_without_prompt": False}]


def test_main_reports_generic_cancellation(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.setattr(
        "noteropdf.cli._run_connect",
        lambda: (_ for _ in ()).throw(EOFError()),
    )
    monkeypatch.setattr(
        "noteropdf.cli.setup_run_logging", lambda *args, **kwargs: tmp_path / "log"
    )

    assert main(["connect"]) == 130
    assert "Any operation already completed remains in place" in caplog.text
    assert "upload" not in caplog.text.casefold()


def test_print_summary_is_concise(capsys):
    _print_summary(
        [
            _row("preview_upload:first_sync"),
            _row("quick_fingerprint_match", "UNCHANGED"),
        ],
        preview=True,
    )
    output = capsys.readouterr().out
    assert "Sync preview" in output
    assert "PDFs to upload: 1" in output
    assert "Already current: 1" in output


def test_select_pdf_property_prefers_dedicated_property():
    pdf = NotionProperty("pdf-id", "NoteroPDF PDF", "files")
    notion = SimpleNamespace(find_files_property=lambda _id, _name: pdf)
    selected = _select_pdf_property(notion, NotionTarget("source", "Library"))
    assert selected == pdf


def test_select_pdf_property_reuses_renamed_connected_property():
    renamed = NotionProperty("pdf-id", "Renamed PDF", "files")

    class Notion:
        @staticmethod
        def resolve_property(_data_source_id, property_id):
            assert property_id == "pdf-id"
            return renamed

        @staticmethod
        def find_files_property(*_args):
            pytest.fail("the saved property ID should be reused")

    selected = _select_pdf_property(
        Notion(),
        NotionTarget("source", "Library"),
        LocalSettings(notion_data_source_id="source", pdf_property_id="pdf-id"),
    )

    assert selected == renamed


def test_duplicate_database_names_are_displayed_with_unique_context(monkeypatch):
    choices = []
    notion = SimpleNamespace(
        list_accessible_data_sources=lambda: [
            NotionTarget("source-one", "Library", "https://notion.so/one"),
            NotionTarget("source-two", "Library", "https://notion.so/two"),
        ]
    )
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._choose_number",
        lambda _prompt, labels: choices.extend(labels) or 1,
    )

    selected = _select_target(notion)

    assert selected.data_source_id == "source-two"
    assert choices == [
        "Library — https://notion.so/one",
        "Library — https://notion.so/two",
    ]


def test_connect_can_change_a_valid_saved_zotero_folder(monkeypatch, tmp_path: Path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    for directory in (old, new):
        directory.mkdir()
        (directory / "zotero.sqlite").touch()
        (directory / "storage").mkdir()
    prompts = iter((False,))
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._prompt_yes_no", lambda *_args, **_kwargs: next(prompts)
    )
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: new)

    assert _select_zotero_directory(LocalSettings(zotero_data_dir=old)) == new.resolve()


def test_rejected_saved_zotero_folder_is_not_auto_selected(monkeypatch, tmp_path: Path):
    saved = tmp_path / "saved"
    replacement = tmp_path / "replacement"
    for directory in (saved, replacement):
        directory.mkdir()
        (directory / "zotero.sqlite").touch()
        (directory / "storage").mkdir()
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr("noteropdf.cli._prompt_yes_no", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: saved)
    monkeypatch.setattr(
        "noteropdf.cli._prompt_value",
        lambda _prompt, **_kwargs: str(replacement),
    )

    selected = _select_zotero_directory(LocalSettings(zotero_data_dir=saved))

    assert selected == replacement.resolve()


def test_interactive_sync_applies_only_actions_from_preview(monkeypatch):
    preview_row = _row("preview_upload:first_sync")
    result_row = _row("upload_attach:first_sync")
    approved = (PreviewAction("ABC", "page", "paper.pdf", 10, 20, "digest"),)
    calls = []

    class FakeEngine:
        def __init__(self, _cfg):
            self.index = len(calls)
            calls.append(self)

        def sync(self, **kwargs):
            self.kwargs = kwargs
            return [preview_row] if self.index == 0 else [result_row]

        def preview_actions(self):
            return approved

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda: object())
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr("noteropdf.cli._prompt_yes_no", lambda *args, **kwargs: True)

    assert _run_sync(apply_without_prompt=False) == 0
    assert calls[0].kwargs == {"apply": False}
    assert calls[1].kwargs == {
        "apply": True,
        "approved_actions": approved,
    }


def test_noninteractive_sync_is_preview_only_without_apply(monkeypatch):
    engines = []

    class FakeEngine:
        def __init__(self, _cfg):
            engines.append(self)

        def sync(self, **kwargs):
            return [_row("preview_upload:first_sync")]

        def preview_actions(self):
            return (PreviewAction("ABC", "page", "paper.pdf", 10, 20, "digest"),)

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda: object())
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: False)

    assert _run_sync(apply_without_prompt=False) == 2
    assert len(engines) == 1


def test_apply_returns_failure_when_an_upload_was_not_completed(monkeypatch):
    engines = []

    class FakeEngine:
        def __init__(self, _cfg):
            self.index = len(engines)
            engines.append(self)

        def sync(self, **_kwargs):
            if self.index == 0:
                return [_row("preview_upload:first_sync")]
            return [_row("upload_failed", "UPLOAD_FAILED")]

        def preview_actions(self):
            return (PreviewAction("ABC", "page", "paper.pdf", 10, 20, "digest"),)

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda: object())
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: False)

    assert _run_sync(apply_without_prompt=True) == 1


def test_preview_returns_failure_when_only_action_is_blocked(monkeypatch):
    blocked = _row("skip:remote_pdf_not_managed", "REMOTE_PDF_CONFLICT")

    class FakeEngine:
        def __init__(self, _cfg):
            pass

        def sync(self, **_kwargs):
            return [blocked]

        def preview_actions(self):
            return ()

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda: object())
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    assert _run_sync(apply_without_prompt=False) == 1


def test_doctor_reports_incomplete_setup_without_requiring_config(monkeypatch, capsys):
    monkeypatch.setattr("noteropdf.cli.load_local_settings", LocalSettings)
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: None)
    monkeypatch.setattr("noteropdf.cli._credential_store", lambda: object())
    monkeypatch.setattr(
        "noteropdf.cli.resolve_access_token", lambda _store: ("", "missing")
    )

    assert _run_doctor() == 2
    output = capsys.readouterr().out
    assert "Zotero data folder was not found" in output
    assert "Notion personal access token has not been saved" in output
    assert "noteropdf connect" in output


def test_authorize_notion_opens_pat_page_and_returns_new_token(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(
        "noteropdf.cli.webbrowser.open",
        lambda url, new: opened.append((url, new)) or True,
    )
    monkeypatch.setattr(
        "noteropdf.cli._prompt_notion_token", lambda: "new-personal-token"
    )

    assert _authorize_notion(None) == ("new-personal-token", "new-personal-token")
    assert opened == [(NOTION_TOKEN_PAGE_URL, 2)]
    assert NOTION_TOKEN_PAGE_URL in capsys.readouterr().out


def test_authorize_notion_can_reuse_saved_token(monkeypatch):
    monkeypatch.setattr("noteropdf.cli._prompt_yes_no", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "noteropdf.cli.webbrowser.open",
        lambda *args, **kwargs: pytest.fail("browser should not open"),
    )

    assert _authorize_notion("saved-token") == ("saved-token", None)


def test_validated_token_is_saved_before_database_selection(
    monkeypatch, tmp_path: Path
):
    previous = "old-personal-token"
    replacement = "new-personal-token"
    saved = []

    class FakeStore:
        def load(self):
            return previous

        def save(self, credentials):
            saved.append(credentials)
            return "keyring"

    class FakeNotion:
        def __init__(self, **_kwargs):
            pass

        def ping(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli.load_local_settings", LocalSettings)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_directory", lambda _settings: tmp_path
    )
    monkeypatch.setattr("noteropdf.cli._credential_store", FakeStore)
    monkeypatch.setattr(
        "noteropdf.cli._authorize_notion",
        lambda _previous: (replacement, replacement),
    )
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotion)
    monkeypatch.setattr(
        "noteropdf.cli._select_target",
        lambda _notion: (_ for _ in ()).throw(ValueError("selection cancelled")),
    )

    with pytest.raises(ValueError, match="selection cancelled"):
        _run_connect()

    assert saved == [replacement]


def test_connect_replaces_a_rejected_saved_token_in_one_run(
    monkeypatch, tmp_path: Path
):
    saved = []
    authorize_calls = []

    class Store:
        def load(self):
            return "expired-token"

        def save(self, token):
            saved.append(token)

    class FakeNotion:
        def __init__(self, *, token, **_kwargs):
            self.token = token

        def ping(self):
            if self.token == "expired-token":
                raise NotionApiError("NOTION_AUTH_ERROR", "expired", 401)

        def close(self):
            pass

    def authorize(previous):
        authorize_calls.append(previous)
        if previous is not None:
            return previous, None
        return "replacement-token", "replacement-token"

    pdf = NotionProperty("pdf-id", "NoteroPDF PDF", "files")
    monkeypatch.setattr("noteropdf.cli.load_local_settings", LocalSettings)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_directory", lambda _settings: tmp_path
    )
    monkeypatch.setattr("noteropdf.cli._credential_store", Store)
    monkeypatch.setattr("noteropdf.cli._authorize_notion", authorize)
    monkeypatch.setattr("noteropdf.cli.NotionClient", FakeNotion)
    monkeypatch.setattr(
        "noteropdf.cli._select_target",
        lambda _notion: NotionTarget("source", "Library"),
    )
    monkeypatch.setattr(
        "noteropdf.cli._select_pdf_property",
        lambda _notion, _target, _existing: pdf,
    )
    monkeypatch.setattr("noteropdf.cli._save_connection", lambda _settings: None)

    assert _run_connect() == 0
    assert authorize_calls == ["expired-token", None]
    assert saved == ["replacement-token"]
