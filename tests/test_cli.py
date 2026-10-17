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
    _run_sources,
    _run_sync,
    _select_pdf_property,
    _select_target,
    _select_zotero_directory,
    _select_zotero_library,
    main,
)
from noteropdf.models import SyncRow, SyncScope, ZoteroLibrary
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
    assert "{sync,sources,connect,doctor,pdf}" in help_text
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
    assert calls == [
        {
            "apply_without_prompt": False,
            "details": False,
            "scope": SyncScope(),
            "profile": None,
        }
    ]


def test_main_reports_generic_cancellation(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.setattr(
        "noteropdf.cli._run_connect",
        lambda *_args: (_ for _ in ()).throw(EOFError()),
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


@pytest.mark.parametrize(
    "scope",
    [SyncScope(), SyncScope(collection_keys=("ABCD1234",), item_keys=("EFGH5678",))],
)
def test_interactive_sync_applies_only_actions_from_preview(monkeypatch, scope):
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

    cfg = SimpleNamespace(zotero=SimpleNamespace(group_id=None))
    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda _profile: cfg)
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr("noteropdf.cli._prompt_yes_no", lambda *args, **kwargs: True)

    assert _run_sync(apply_without_prompt=False, scope=scope) == 0
    assert calls[0].kwargs == {"apply": False, "scope": scope}
    assert calls[1].kwargs == {
        "apply": True,
        "approved_actions": approved,
        "scope": scope,
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

    cfg = SimpleNamespace(zotero=SimpleNamespace(group_id=None))
    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda _profile: cfg)
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

    cfg = SimpleNamespace(zotero=SimpleNamespace(group_id=None))
    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda _profile: cfg)
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

    cfg = SimpleNamespace(zotero=SimpleNamespace(group_id=None))
    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda _profile: cfg)
    monkeypatch.setattr("noteropdf.cli.SyncEngine", FakeEngine)

    assert _run_sync(apply_without_prompt=False) == 1


def test_doctor_reports_incomplete_setup_without_requiring_config(monkeypatch, capsys):
    monkeypatch.setattr(
        "noteropdf.cli.load_local_settings", lambda **_kwargs: LocalSettings()
    )
    monkeypatch.setattr("noteropdf.cli.detect_zotero_data_dir", lambda: None)
    monkeypatch.setattr("noteropdf.cli._credential_store", lambda _profile: object())
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


def test_replacement_token_is_not_saved_before_database_selection(
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

    monkeypatch.setattr(
        "noteropdf.cli.load_local_settings", lambda **_kwargs: LocalSettings()
    )
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_directory", lambda _settings: tmp_path
    )
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_library", lambda _settings: ZoteroLibrary()
    )
    monkeypatch.setattr("noteropdf.cli._credential_store", lambda _profile: FakeStore())
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

    assert saved == []


@pytest.mark.parametrize("group_id", [None, 123])
def test_connect_replaces_a_rejected_saved_token_in_one_run(
    monkeypatch, tmp_path: Path, group_id
):
    saved = []
    connections = []
    authorize_calls = []

    class Store:
        def load(self):
            return "expired-token"

        def stage(self, token):
            saved.append(token)
            return "a" * 32

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
    monkeypatch.setattr(
        "noteropdf.cli.load_local_settings", lambda **_kwargs: LocalSettings()
    )
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: True)
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_directory", lambda _settings: tmp_path
    )
    monkeypatch.setattr(
        "noteropdf.cli._select_zotero_library",
        lambda _settings: ZoteroLibrary(group_id),
    )
    monkeypatch.setattr("noteropdf.cli._credential_store", lambda _profile: Store())
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
    monkeypatch.setattr(
        "noteropdf.cli._save_connection",
        lambda settings, _profile: connections.append(settings),
    )

    monkeypatch.setattr("noteropdf.cli._review_connection", lambda *args: True)
    assert _run_connect() == 0
    assert authorize_calls == ["expired-token", None]
    assert saved == ["replacement-token"]
    assert connections[0].zotero_group_id == group_id


def test_library_selection_offers_personal_and_groups(monkeypatch):
    libraries = [ZoteroLibrary(), ZoteroLibrary(123, "Research")]
    monkeypatch.setattr(
        "noteropdf.cli.ZoteroRepository.list_libraries", lambda: libraries
    )
    choices = []

    def choose(_prompt, labels):
        choices.extend(labels)
        return 1

    monkeypatch.setattr("noteropdf.cli._choose_number", choose)
    assert _select_zotero_library(LocalSettings()) == libraries[1]
    assert choices == ["My Library [saved]", "Research (group 123)"]


def test_missing_saved_group_requires_explicit_choice(monkeypatch, capsys):
    monkeypatch.setattr(
        "noteropdf.cli.ZoteroRepository.list_libraries", lambda: [ZoteroLibrary()]
    )
    prompts = []
    monkeypatch.setattr(
        "noteropdf.cli._choose_number",
        lambda prompt, _labels: prompts.append(prompt) or 0,
    )
    assert _select_zotero_library(LocalSettings(zotero_group_id=123)).group_id is None
    assert len(prompts) == 1
    assert "saved group is unavailable" in capsys.readouterr().out


def test_only_personal_library_needs_no_selection(monkeypatch):
    monkeypatch.setattr(
        "noteropdf.cli.ZoteroRepository.list_libraries", lambda: [ZoteroLibrary()]
    )
    assert _select_zotero_library(LocalSettings()) == ZoteroLibrary()


@pytest.mark.parametrize("changed_field", ["zotero", "notion", "sync", "token"])
def test_changed_connection_blocks_apply_before_opening_engine(
    monkeypatch, changed_field
):
    from dataclasses import replace

    from noteropdf.config import AppConfig, NotionConfig, SyncConfig, ZoteroConfig

    cfg = AppConfig(
        ZoteroConfig(Path("data"), Path("data/storage")),
        NotionConfig("version", "source", "pdf"),
        SyncConfig(Path("state.sqlite3"), Path("logs")),
        "test-token",
        "keyring",
    )
    changed = {
        "zotero": replace(cfg, zotero=replace(cfg.zotero, group_id=123)),
        "notion": replace(cfg, notion=replace(cfg.notion, pdf_property_id="other-pdf")),
        "sync": replace(
            cfg, sync=replace(cfg.sync, state_db_path=Path("other-profile.db"))
        ),
        "token": replace(cfg, notion_token="different-profile-token"),
    }[changed_field]
    configs = iter([cfg, changed])
    engines = []

    class Engine:
        def __init__(self, config):
            engines.append(config)

        def sync(self, *, apply, scope):
            assert not apply
            assert scope == SyncScope()
            return [_row("preview_upload:first_sync")]

        def preview_actions(self):
            return (PreviewAction("ABC", "page", "paper.pdf", 10, 20, "digest"),)

        def close(self):
            pass

    selected_profiles = []

    def load_config(profile):
        selected_profiles.append(profile)
        return next(configs)

    monkeypatch.setattr("noteropdf.cli._load_ready_config", load_config)
    monkeypatch.setattr("noteropdf.cli.SyncEngine", Engine)
    assert _run_sync(apply_without_prompt=True, profile="lab") == 1
    assert engines == [cfg]
    assert selected_profiles == ["lab", "lab"]


@pytest.mark.parametrize(
    "group_id, source",
    [(None, "personal Zotero library"), (123, "selected Zotero group library")],
)
def test_summary_explains_context_without_private_row_metadata(
    capsys, group_id, source
):
    from dataclasses import replace

    row = replace(
        _row("preview_upload:changed"),
        title="Private paper title",
        local_pdf_path="/private/research/Private paper.pdf",
        notion_page_id="secret-page-id",
        notion_page_url="https://notion.so/secret-page-id",
        error_message="private error detail",
    )
    cfg = SimpleNamespace(zotero=SimpleNamespace(group_id=group_id))
    _print_summary([row], preview=True, cfg=cfg)
    output = capsys.readouterr().out
    assert f"Source: {source}" in output
    assert "Destination: connected Notion database, dedicated PDF files field" in output
    assert "Item ABC: Update the PDF previously uploaded by NoteroPDF" in output
    assert "Preview only. Notion has not changed." in output
    for private in (
        row.title,
        row.local_pdf_path,
        row.notion_page_id,
        row.notion_page_url,
        row.error_message,
    ):
        assert private not in output


def test_summary_is_bounded_and_details_show_every_item(capsys):
    from dataclasses import replace

    rows = [
        replace(_row("preview_upload:missing_remote_pdf"), zotero_item_key=f"ITEM{i}")
        for i in range(30)
    ]
    rows.append(
        replace(_row("quick_fingerprint_match", "UNCHANGED"), zotero_item_key="CURRENT")
    )
    _print_summary(rows, preview=True)
    output = capsys.readouterr().out
    assert "PDFs to upload: 30" in output
    assert "...and 25 more" in output
    assert "Item ITEM4:" in output
    assert "Item ITEM5:" not in output
    assert "Item CURRENT:" not in output
    _print_summary(rows, preview=True, details=True)
    output = capsys.readouterr().out
    assert "Item ITEM29:" in output
    assert "Item CURRENT: The existing PDF is current" in output
    assert "...and" not in output


def test_summary_has_actionable_skips_and_never_echoes_raw_errors_or_controls(capsys):
    from dataclasses import replace

    rows = [
        replace(
            _row("skip", "MULTIPLE_PDFS"),
            zotero_item_key="groups/123/ITEM",
            error_message="Private diagnostic /home/person/paper.pdf",
        ),
        replace(
            _row("skip", "UNRECOGNIZED_SECRET_STATUS"), zotero_item_key="\x1b[31mBAD"
        ),
        _row("skip:remote_pdf_not_managed", "REMOTE_PDF_CONFLICT"),
    ]
    _print_summary(rows, preview=True, details=True)
    output = capsys.readouterr().out
    assert "Item ITEM: Several PDFs are available" in output
    assert "nothing was replaced" in output
    assert "Run `noteropdf doctor`" in output
    assert "Private diagnostic" not in output
    assert "UNRECOGNIZED_SECRET_STATUS" not in output
    assert "\x1b" not in output


def test_parser_accepts_details_for_sync():
    assert _build_parser().parse_args(["sync", "--details"]).details is True


def test_pdf_command_inspects_validates_saves_and_clears_without_notion(
    monkeypatch, tmp_path, capsys
):
    from noteropdf.cli import _run_pdf
    from noteropdf.models import ZoteroAttachment, ZoteroItem
    from noteropdf.state_store import StateStore
    from noteropdf.zotero_repo import ZoteroRepository

    selected_profiles = []
    state_path = tmp_path / "state.sqlite3"
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    item = ZoteroItem(1, "ITEM", "Private title", "", None, ZoteroLibrary(123))
    repo = object.__new__(ZoteroRepository)
    repo._storage_dir = tmp_path
    repo._data_dir = tmp_path
    repo._api_parent_items = [item]
    repo._api_attachments = {
        1: [ZoteroAttachment(1, "ITEM", "PDF", str(path), "application/pdf", None)]
    }
    monkeypatch.setattr("noteropdf.cli.ZoteroRepository", lambda *args, **kwargs: repo)
    monkeypatch.setattr(
        "noteropdf.cli._load_ready_config",
        lambda _profile: (
            selected_profiles.append(_profile)
            or SimpleNamespace(
                zotero=SimpleNamespace(
                    storage_dir=tmp_path, data_dir=tmp_path, group_id=123
                ),
                sync=SimpleNamespace(state_db_path=state_path),
            )
        ),
    )
    assert _run_pdf("ITEM", None, clear=False, profile="lab") == 0
    output = capsys.readouterr().out
    assert "--profile lab ITEM ATTACHMENT" in output
    assert "- PDF" in output
    assert "Private title" not in output
    assert str(path) not in output
    assert _run_pdf("ITEM", "PDF", clear=False, profile="lab") == 0
    store = StateStore(state_path)
    assert store.get_pdf_selection("groups/123/ITEM").attachment_key == "PDF"
    assert store.get_pdf_selection("ITEM") is None
    store.close()
    with pytest.raises(ValueError, match="does not belong"):
        _run_pdf("ITEM", "WRONG", clear=False, profile="lab")
    with pytest.raises(ValueError, match="unavailable"):
        _run_pdf("OTHER", "PDF", clear=False, profile="lab")
    assert _run_pdf("ITEM", None, clear=True, profile="lab") == 0
    store = StateStore(state_path)
    assert store.get_pdf_selection("groups/123/ITEM") is None
    store.close()

    assert selected_profiles == ["lab"] * 5


def test_sync_filters_are_repeatable_and_passed_to_workflow(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "noteropdf.cli._run_sync", lambda **kwargs: calls.append(kwargs) or 0
    )
    monkeypatch.setattr(
        "noteropdf.cli.setup_run_logging", lambda *args, **kwargs: tmp_path / "log"
    )
    assert (
        main(
            [
                "sync",
                "--profile",
                "lab",
                "--collection-key",
                "ABCD1234",
                "--collection-key",
                "EFGH5678",
                "--item-key",
                "IJKL9012",
                "--item-key",
                "MNOP3456",
                "--apply",
            ]
        )
        == 0
    )
    assert calls == [
        {
            "apply_without_prompt": True,
            "details": False,
            "profile": "lab",
            "scope": SyncScope(("ABCD1234", "EFGH5678"), ("IJKL9012", "MNOP3456")),
        }
    ]


@pytest.mark.parametrize("flag", ["--collection-key", "--item-key"])
@pytest.mark.parametrize(
    "key", ["", "abcD1234", "ABCD123", "ABCD12345", "ABCD/123", "ABCD12é4"]
)
def test_sync_filter_keys_reject_invalid_input_before_setup(flag, key):
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(["sync", flag, key])
    assert exc.value.code == 2


@pytest.mark.parametrize("details", [False, True])
def test_noninteractive_retry_command_preserves_profile_and_scope(
    monkeypatch, capsys, details
):
    class Engine:
        def __init__(self, _cfg):
            pass

        def sync(self, *, apply, scope):
            assert not apply
            assert scope == SyncScope(("COLL1234",), ("ITEM1234",))
            return [_row("preview_upload:missing_remote_pdf")]

        def preview_actions(self):
            return ()

        def close(self):
            pass

    monkeypatch.setattr(
        "noteropdf.cli._load_ready_config",
        lambda profile: SimpleNamespace(zotero=SimpleNamespace(group_id=None)),
    )
    monkeypatch.setattr("noteropdf.cli.SyncEngine", Engine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: False)
    assert (
        _run_sync(
            apply_without_prompt=False,
            profile="lab",
            scope=SyncScope(("COLL1234",), ("ITEM1234",)),
            details=details,
        )
        == 2
    )
    assert (
        "noteropdf sync --profile lab"
        + (" --details" if details else "")
        + " --collection-key COLL1234 --item-key ITEM1234 --apply"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "lab", "pdf", "ITEM", "PDF"],
        ["pdf", "ITEM", "PDF", "--profile", "lab"],
    ],
)
def test_pdf_command_routes_named_profile_before_or_after_command(
    monkeypatch, tmp_path, arguments
):
    calls = []
    monkeypatch.setattr(
        "noteropdf.cli.setup_run_logging", lambda *args, **kwargs: tmp_path / "log"
    )
    monkeypatch.setattr(
        "noteropdf.cli._run_pdf",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 0,
    )
    assert main(arguments) == 0
    assert calls == [(("ITEM", "PDF"), {"clear": False, "profile": "lab"})]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "lab", "sync", "--details", "--item-key", "ABCD1234"],
        ["sync", "--details", "--item-key", "ABCD1234", "--profile", "lab"],
    ],
)
def test_sync_routes_details_with_named_profile_and_scope(
    monkeypatch, tmp_path, arguments
):
    calls = []
    monkeypatch.setattr(
        "noteropdf.cli.setup_run_logging", lambda *args, **kwargs: tmp_path / "log"
    )
    monkeypatch.setattr(
        "noteropdf.cli._run_sync", lambda **kwargs: calls.append(kwargs) or 0
    )
    assert main(arguments) == 0
    assert calls == [
        {
            "apply_without_prompt": False,
            "details": True,
            "profile": "lab",
            "scope": SyncScope(item_keys=("ABCD1234",)),
        }
    ]


def test_sources_parser_requires_explicit_valid_items():
    parser = _build_parser()
    for args in [["sources"], ["sources", "--item-key", "bad"]]:
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(args)
        assert exc.value.code == 2
    args = parser.parse_args(
        ["sources", "--item-key", "PARENT01", "--item-key", "PARENT02"]
    )
    assert args.item_key == ["PARENT01", "PARENT02"]


@pytest.mark.parametrize("apply_now", [False, True])
def test_sources_reuses_exact_scope_and_approval(monkeypatch, apply_now):
    cfg = object()
    instances = []
    approved = (PreviewAction("PARENT01:ATT00001", "page", "file.csv", 4, 5, "digest"),)

    class Engine:
        def __init__(self, config, *, item_keys):
            assert config is cfg
            assert item_keys == ("PARENT01",)
            instances.append(self)

        def sync(self, **kwargs):
            self.kwargs = kwargs
            row = _row(
                "upload_attach:source_file"
                if kwargs["apply"]
                else "preview_upload:source_file"
            )
            row.zotero_item_key = approved[0].zotero_item_key
            return [row]

        def preview_actions(self):
            return approved

        def close(self):
            pass

    monkeypatch.setattr("noteropdf.cli._load_ready_config", lambda _profile: cfg)
    monkeypatch.setattr(
        "noteropdf.cli.source_files_config", lambda config, name: config
    )
    monkeypatch.setattr("noteropdf.cli.SourceFilesEngine", Engine)
    monkeypatch.setattr("noteropdf.cli._stdin_interactive", lambda: False)
    assert _run_sources(
        item_keys=("PARENT01",),
        property_name="Source files",
        apply_without_prompt=apply_now,
    ) == (0 if apply_now else 2)
    assert len(instances) == (2 if apply_now else 1)
    if apply_now:
        assert instances[1].kwargs == {"apply": True, "approved_actions": approved}
