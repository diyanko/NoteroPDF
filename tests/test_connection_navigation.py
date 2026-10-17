from types import SimpleNamespace

import pytest

from noteropdf import cli, config
from noteropdf.auth import CredentialStore
from noteropdf.models import ZoteroLibrary
from noteropdf.notion_client import NotionApiError, NotionProperty, NotionTarget
from noteropdf.settings import LocalSettings


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config, "get_default_state_db_path", lambda: tmp_path / "state.db"
    )
    zotero = tmp_path / "zotero"
    zotero.mkdir()
    (zotero / "storage").mkdir()
    (zotero / "zotero.sqlite").touch()
    accounts = {}
    keyring = SimpleNamespace(
        get_password=lambda service, username: accounts.get((service, username)),
        set_password=lambda service, username, value: accounts.__setitem__(
            (service, username), value
        ),
        delete_password=lambda service, username: accounts.pop(
            (service, username), None
        ),
    )

    def credentials(profile=None, *, credential_id=None):
        return CredentialStore(
            profile=profile, credential_id=credential_id, keyring_module=keyring
        )

    old = LocalSettings(
        zotero_data_dir=zotero,
        notion_data_source_id="old-source",
        pdf_property_id="old-pdf",
    )
    cli._save_connection(old, "lab")
    credentials("lab").save("old-token")
    monkeypatch.setattr(cli, "_credential_store", credentials)
    monkeypatch.setattr(config, "CredentialStore", credentials)
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(cli, "_select_zotero_directory", lambda existing: zotero)
    monkeypatch.setattr(
        cli, "_select_zotero_library", lambda existing: ZoteroLibrary(123, "Lab")
    )
    monkeypatch.setattr(
        cli, "_authorize_notion", lambda previous: ("new-token", "new-token")
    )
    monkeypatch.setattr(
        cli,
        "NotionClient",
        lambda **kwargs: SimpleNamespace(ping=lambda: None, close=lambda: None),
    )
    monkeypatch.setattr(
        cli, "_select_target", lambda notion: NotionTarget("new-source", "Research")
    )
    monkeypatch.setattr(
        cli,
        "_select_pdf_property",
        lambda *args: NotionProperty("new-pdf", "PDF", "files"),
    )
    monkeypatch.setattr(cli, "_review_connection", lambda *args: True)
    return SimpleNamespace(
        old=old, accounts=accounts, credentials=credentials, keyring=keyring
    )


@pytest.mark.parametrize(
    "point",
    [
        "_select_zotero_directory",
        "_select_zotero_library",
        "_authorize_notion",
        "_select_target",
        "_select_pdf_property",
        "_review_connection",
    ],
)
@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError, cli._SetupCancelled])
def test_cancel_and_interrupt_preserve_working_connection(
    setup, monkeypatch, point, error
):
    old_accounts = setup.accounts.copy()

    def interrupt(*args):
        raise error

    monkeypatch.setattr(cli, point, interrupt)
    if error is cli._SetupCancelled:
        assert cli._run_connect("lab") == 0
    else:
        with pytest.raises(error):
            cli._run_connect("lab")
    assert config.load_local_settings(profile="lab") == setup.old
    assert setup.accounts == old_accounts


def test_failed_settings_save_removes_only_staged_generation(setup, monkeypatch):
    old_accounts = setup.accounts.copy()

    def fail(*args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(cli, "_save_connection", fail)
    with pytest.raises(OSError, match="synthetic"):
        cli._run_connect("lab")
    assert config.load_local_settings(profile="lab") == setup.old
    assert setup.accounts == old_accounts


def test_interrupt_after_atomic_settings_commit_keeps_referenced_generation(
    setup, monkeypatch
):
    save = cli._save_connection

    def interrupted_save(*args):
        save(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_save_connection", interrupted_save)
    with pytest.raises(KeyboardInterrupt):
        cli._run_connect("lab")
    current = config.load_local_settings(profile="lab")
    assert current.notion_credential_id is not None
    assert current.notion_data_source_id == "new-source"
    assert (
        setup.credentials("lab", credential_id=current.notion_credential_id).load()
        == "new-token"
    )
    assert setup.credentials("lab").load() == "old-token"


def test_missing_referenced_token_never_falls_back_to_previous_account(setup):
    assert cli._run_connect("lab") == 0
    current = config.load_local_settings(profile="lab")
    setup.credentials("lab").discard(current.notion_credential_id)
    with pytest.raises(config.SetupRequired, match="authorization is missing"):
        config.load_app_config(profile="lab")
    with pytest.raises(config.SetupRequired, match="authorization is missing"):
        cli._load_ready_config("lab")
    assert setup.credentials("lab").load() == "old-token"


def test_unchanged_token_reuses_existing_generation(setup, monkeypatch):
    monkeypatch.setattr(cli, "_authorize_notion", lambda previous: (previous, previous))
    old_accounts = setup.accounts.copy()
    assert cli._run_connect("lab") == 0
    assert config.load_local_settings(profile="lab").notion_credential_id is None
    assert setup.accounts == old_accounts


def test_back_revisits_previous_step_without_saving(setup, monkeypatch):
    calls = []

    def target(notion):
        calls.append("target")
        assert setup.credentials("lab").load() == "old-token"
        if len(calls) == 1:
            raise cli._SetupBack
        return NotionTarget("new-source", "Research")

    monkeypatch.setattr(cli, "_select_target", target)
    assert cli._run_connect("lab") == 0
    assert calls == ["target", "target"]


def test_review_edit_can_change_destination_before_save(setup, monkeypatch):
    choices = iter([False, True])
    targets = iter([NotionTarget("discarded", "First"), NotionTarget("final", "Final")])
    monkeypatch.setattr(cli, "_review_connection", lambda *args: next(choices))
    monkeypatch.setattr(cli, "_select_target", lambda notion: next(targets))
    assert cli._run_connect("lab") == 0
    assert config.load_local_settings(profile="lab").notion_data_source_id == "final"


def test_partial_keyring_failure_never_changes_previous_account(setup):
    old_accounts = setup.accounts.copy()
    write = setup.keyring.set_password

    def fail_after_write(service, username, value):
        write(service, username, value)
        raise KeyboardInterrupt

    setup.keyring.set_password = fail_after_write
    with pytest.raises(KeyboardInterrupt):
        cli._run_connect("lab")
    assert setup.accounts == old_accounts
    assert config.load_local_settings(profile="lab") == setup.old


@pytest.mark.parametrize(
    "answer,error",
    [
        ("back", cli._SetupBack),
        ("cancel", cli._SetupCancelled),
        ("q", cli._SetupCancelled),
    ],
)
def test_setup_navigation_words_work_at_prompts(monkeypatch, answer, error):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    with pytest.raises(error):
        cli._prompt_yes_no("Continue?")
    with pytest.raises(error):
        cli._choose_number("Choose", ["First"])
    monkeypatch.setattr("noteropdf.cli.getpass.getpass", lambda prompt: answer)
    with pytest.raises(error):
        cli._prompt_notion_token()


def test_invalid_review_answer_retries_before_edit(monkeypatch):
    answers = iter(["wrong", "edit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert not cli._review_connection(
        ZoteroLibrary(),
        NotionTarget("source", "Database"),
        NotionProperty("pdf", "PDF", "files"),
    )


def test_remote_failure_preserves_old_connection(setup, monkeypatch):
    def fail(notion):
        raise NotionApiError("NOTION_NETWORK_ERROR", "synthetic network failure")

    monkeypatch.setattr(cli, "_select_target", fail)
    old_accounts = setup.accounts.copy()
    with pytest.raises(NotionApiError):
        cli._run_connect("lab")
    assert config.load_local_settings(profile="lab") == setup.old
    assert setup.accounts == old_accounts


def test_sqlite_failure_rolls_back_all_settings_and_staged_token(setup, monkeypatch):
    from noteropdf.state_store import StateStore

    update = StateStore.update_settings
    old_accounts = setup.accounts.copy()

    def interrupted_update(store, values):
        # The empty key fails after the earlier updates have reached SQLite.
        return update(store, {**values, "": "invalid"})

    monkeypatch.setattr(StateStore, "update_settings", interrupted_update)
    with pytest.raises(ValueError, match="keys cannot be empty"):
        cli._run_connect("lab")
    assert config.load_local_settings(profile="lab") == setup.old
    assert setup.accounts == old_accounts


def test_reconnect_from_active_generation_keeps_profile_scope(setup, monkeypatch):
    assert cli._run_connect("lab") == 0
    first = config.load_local_settings(profile="lab")
    selected = []

    def authorize(previous):
        selected.append(previous)
        return previous, None

    monkeypatch.setattr(cli, "_authorize_notion", authorize)
    old_accounts = setup.accounts.copy()
    assert cli._run_connect("lab") == 0
    assert selected == ["new-token"]
    assert setup.accounts == old_accounts
    assert (
        config.load_local_settings(profile="lab").notion_credential_id
        == first.notion_credential_id
    )


def test_setup_from_version_one_keeps_legacy_token_until_commit(setup):
    from noteropdf.state_store import StateStore

    store = StateStore(config.get_profile_state_db_path("lab"))
    try:
        store.update_settings({"settings_schema_version": "1"})
    finally:
        store.close()
    assert cli._run_connect("lab") == 0
    assert setup.credentials("lab").load() == "old-token"
    assert config.load_local_settings(profile="lab").schema_version == 2


def test_config_uses_one_settings_snapshot_for_token_and_destination(
    setup, monkeypatch
):
    assert cli._run_connect("lab") == 0
    calls = []
    load = config.load_local_settings

    def single_snapshot(*args, **kwargs):
        calls.append(1)
        assert len(calls) == 1
        return load(*args, **kwargs)

    monkeypatch.setattr(config, "load_local_settings", single_snapshot)
    cfg = cli._load_ready_config("lab")
    assert cfg.notion.data_source_id == "new-source"
    assert cfg.notion_token == "new-token"
    assert calls == [1]
