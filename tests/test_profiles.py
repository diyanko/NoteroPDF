from dataclasses import replace
from types import SimpleNamespace

import pytest

from noteropdf import cli, config
from noteropdf.auth import KEYRING_SERVICE, KEYRING_USERNAME, CredentialStore
from noteropdf.models import SyncScope
from noteropdf.profiles import normalize_profile, validate_profile_name
from noteropdf.settings import LocalSettings
from noteropdf.state_store import StateRecord, StateStore


@pytest.fixture
def isolated_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config, "get_default_state_db_path", lambda: tmp_path / "state.db"
    )
    monkeypatch.setattr(config, "get_default_log_dir", lambda: tmp_path / "logs")
    return tmp_path


@pytest.mark.parametrize(
    "name", ["", "Lab", "a/b", "../lab", "lab ", "-lab", "lab-", "a" * 33]
)
def test_invalid_profile_names_are_rejected_without_paths(name):
    with pytest.raises(ValueError, match="Profile names"):
        config.get_profile_state_db_path(name)
    with pytest.raises(ValueError, match="Profile names"):
        CredentialStore(profile=name)


def test_profile_names_are_portable_and_default_is_backward_compatible(isolated_paths):
    assert validate_profile_name("lab-2026") == "lab-2026"
    assert normalize_profile("default") is None
    assert config.get_profile_state_db_path() == isolated_paths / "state.db"
    assert (
        config.get_profile_state_db_path("default")
        == config.get_default_state_db_path()
    )
    assert config.get_profile_state_db_path("con").parent.name == "profile-con"
    assert (
        config.get_profile_log_dir("lab")
        == isolated_paths / "logs/profiles/profile-lab"
    )


def test_profile_credentials_are_isolated_and_keep_legacy_account():
    accounts = {}
    keyring = SimpleNamespace(
        get_password=lambda service, username: accounts.get((service, username)),
        set_password=lambda service, username, value: accounts.__setitem__(
            (service, username), value
        ),
    )
    default = CredentialStore(keyring_module=keyring)
    lab = CredentialStore(keyring_module=keyring, profile="lab")
    other = CredentialStore(keyring_module=keyring, profile="other")
    default.save("default-test-token")
    assert lab.load() is None
    lab.save("lab-test-token")
    assert default.load() == "default-test-token"
    assert (
        CredentialStore(keyring_module=keyring, profile="default").load()
        == default.load()
    )
    assert lab.load() == "lab-test-token"
    assert other.load() is None
    assert accounts[(KEYRING_SERVICE, KEYRING_USERNAME)] == "default-test-token"
    assert (
        accounts[(KEYRING_SERVICE, f"{KEYRING_USERNAME}:profile:lab")]
        == "lab-test-token"
    )


def test_unknown_profile_does_not_create_state_or_reuse_default(isolated_paths):
    cli._save_connection(LocalSettings(notion_data_source_id="default-source"))
    with pytest.raises(config.SetupRequired, match="connect --profile typo"):
        config.load_app_config(profile="typo")
    assert not (isolated_paths / "profiles").exists()
    assert (
        config.load_local_settings(profile="new", allow_missing_profile=True)
        == LocalSettings()
    )
    assert not (isolated_paths / "profiles").exists()


def test_named_connections_keep_settings_history_and_locks_separate(isolated_paths):
    default_settings = LocalSettings(
        notion_data_source_id="personal", pdf_property_id="pdf-personal"
    )
    lab_settings = LocalSettings(
        notion_data_source_id="lab", pdf_property_id="pdf-lab", zotero_group_id=123
    )
    cli._save_connection(default_settings)
    default = StateStore(config.get_default_state_db_path())
    record = StateRecord(
        "ABC", "personal-page", "paper.pdf", 10, 20, "digest", "now", "OK", None
    )
    default.upsert(record)
    try:
        cli._save_connection(lab_settings, "lab")
        lab = StateStore(config.get_profile_state_db_path("lab"))
        try:
            assert lab.get("ABC") is None
            lab.upsert(replace(record, notion_page_id="lab-page"))
            assert default.get("ABC") == record
            with pytest.raises(RuntimeError, match="already active"):
                StateStore(config.get_profile_state_db_path("lab"))
        finally:
            lab.close()
    finally:
        default.close()
    assert config.load_local_settings() == default_settings
    assert config.load_local_settings(profile="lab") == lab_settings
    assert config.load_local_settings(profile="default") == default_settings


@pytest.mark.parametrize("command", [[], ["connect"], ["sync"], ["doctor"]])
def test_profile_option_is_accepted_before_or_after_commands(command):
    parser = cli._build_parser()
    assert parser.parse_args(["--profile", "lab", *command]).profile == "lab"
    assert parser.parse_args([*command, "--profile", "lab"]).profile == "lab"


@pytest.mark.parametrize("command", ["sync", "doctor"])
def test_unknown_profile_stops_before_reading_credentials_or_setup(
    monkeypatch, isolated_paths, command
):
    monkeypatch.setattr(
        cli,
        "_credential_store",
        lambda *_args: SimpleNamespace(
            load=lambda: pytest.fail("credentials must not be read")
        ),
    )
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(
        cli, "_run_connect", lambda *_args: pytest.fail("run connect explicitly")
    )
    with pytest.raises(config.SetupRequired, match="connect --profile typo"):
        if command == "sync":
            cli._run_sync(apply_without_prompt=True, profile="typo")
        else:
            cli._run_doctor("typo")


def test_profile_config_reads_its_credentials_and_connection(
    monkeypatch, isolated_paths
):
    zotero = isolated_paths / "zotero"
    zotero.mkdir()
    (zotero / "storage").mkdir()
    (zotero / "zotero.sqlite").touch()
    cli._save_connection(
        LocalSettings(
            zotero_data_dir=zotero,
            notion_data_source_id="lab-source",
            pdf_property_id="lab-pdf",
            zotero_group_id=123,
        ),
        "lab",
    )
    selected = []

    def credentials(*, profile):
        selected.append(profile)
        return SimpleNamespace(load=lambda: "lab-test-token")

    monkeypatch.setattr(config, "CredentialStore", credentials)
    cfg = config.load_app_config(profile="lab")
    assert selected == ["lab"]
    assert cfg.zotero.group_id == 123
    assert cfg.notion.data_source_id == "lab-source"
    assert cfg.notion.pdf_property_id == "lab-pdf"
    assert cfg.sync.state_db_path == config.get_profile_state_db_path("lab")
    assert cfg.sync.log_dir == config.get_profile_log_dir("lab")
    assert cfg.notion_token == "lab-test-token"
    assert b"lab-test-token" not in config.get_profile_state_db_path("lab").read_bytes()


def test_connect_creates_only_selected_profile(monkeypatch, isolated_paths):
    cli._save_connection(LocalSettings(notion_data_source_id="default-source"))
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(cli, "_stdout_interactive", lambda: True)
    monkeypatch.setattr(
        cli, "_select_zotero_directory", lambda existing: isolated_paths / "zotero"
    )
    monkeypatch.setattr(
        cli, "_select_zotero_library", lambda existing: SimpleNamespace(group_id=123)
    )
    chosen_accounts = []

    def credentials(profile):
        chosen_accounts.append(profile)
        return SimpleNamespace(load=lambda: "lab-test-token")

    monkeypatch.setattr(cli, "_credential_store", credentials)
    monkeypatch.setattr(cli, "_authorize_notion", lambda token: (token, None))
    monkeypatch.setattr(
        cli,
        "NotionClient",
        lambda **_kwargs: SimpleNamespace(ping=lambda: None, close=lambda: None),
    )
    monkeypatch.setattr(
        cli,
        "_select_target",
        lambda notion: SimpleNamespace(data_source_id="lab-source", label="Lab"),
    )
    monkeypatch.setattr(
        cli,
        "_select_pdf_property",
        lambda notion, target, existing: SimpleNamespace(id="lab-pdf", name="PDF"),
    )
    monkeypatch.setattr(cli, "_review_connection", lambda *args: True)
    assert cli._run_connect("lab") == 0
    assert chosen_accounts == ["lab"]
    assert config.load_local_settings().notion_data_source_id == "default-source"
    settings = config.load_local_settings(profile="lab")
    assert settings.notion_data_source_id == "lab-source"
    assert settings.zotero_group_id == 123


@pytest.mark.parametrize("command", ["connect", "doctor", "sync"])
def test_main_dispatches_the_selected_profile(monkeypatch, isolated_paths, command):
    calls = []
    monkeypatch.setattr(cli, "setup_run_logging", lambda *_args, **_kwargs: None)
    if command == "sync":
        monkeypatch.setattr(
            cli, "_run_sync", lambda **kwargs: calls.append(kwargs) or 0
        )
    else:
        monkeypatch.setattr(
            cli, f"_run_{command}", lambda profile: calls.append(profile) or 0
        )
    assert cli.main([command, "--profile", "lab"]) == 0
    assert calls == (
        [
            {
                "apply_without_prompt": False,
                "details": False,
                "profile": "lab",
                "scope": SyncScope(),
            }
        ]
        if command == "sync"
        else ["lab"]
    )


def test_invalid_cli_profile_explains_the_name_format(capsys):
    with pytest.raises(SystemExit) as error:
        cli._build_parser().parse_args(["sync", "--profile", "Lab"])
    assert error.value.code == 2
    assert "Profile names must be" in capsys.readouterr().err


@pytest.mark.parametrize("profile", [None, "default", "lab-2026"])
def test_recovery_commands_are_validated_shell_arguments(profile):
    import shlex

    from noteropdf.profiles import connect_command, recovery_guidance

    expected = ["noteropdf", "connect"]
    if profile == "lab-2026":
        expected.extend(("--profile", profile))
    command = connect_command(profile)
    assert shlex.split(command) == expected
    message = "Run `noteropdf connect` to replace the token."
    rendered = recovery_guidance(message, profile)
    assert rendered == f"Run `{command}` to replace the token."
    assert recovery_guidance(rendered, profile) == rendered
    assert (
        recovery_guidance("Check access and try again.", profile)
        == "Check access and try again."
    )
    with pytest.raises(ValueError, match="Profile names"):
        connect_command("lab; echo unsafe")


@pytest.fixture
def rejected_token_error(monkeypatch):
    from noteropdf.notion_client import NotionApiError, NotionClient

    client = NotionClient(token="test-token", notion_version="2026-03-11")
    response = SimpleNamespace(status_code=401, text="expired", headers={})
    monkeypatch.setattr(client._session, "request", lambda *_args, **_kw: response)
    try:
        with pytest.raises(NotionApiError) as error:
            client.ping()
        return error.value
    finally:
        client.close()


@pytest.mark.parametrize("command", ["sync", "doctor"])
@pytest.mark.parametrize("profile", [None, "default", "lab"])
def test_main_keeps_selected_connection_in_client_error_hints(
    monkeypatch, isolated_paths, caplog, rejected_token_error, command, profile
):
    seen = []

    def fail(selected_profile):
        seen.append(selected_profile)
        raise rejected_token_error

    monkeypatch.setattr(cli, "setup_run_logging", lambda *_args, **_kwargs: None)
    if command == "sync":
        monkeypatch.setattr(
            cli, "_run_sync", lambda *, profile, **_kwargs: fail(profile)
        )
    else:
        monkeypatch.setattr(cli, "_run_doctor", fail)
    arguments = [command] + (["--profile", profile] if profile else [])
    with caplog.at_level("INFO", logger="noteropdf.cli"):
        assert cli.main(arguments) == 2
    assert seen == (["lab"] if profile == "lab" else [None])
    expected = (
        "noteropdf connect --profile lab" if profile == "lab" else "noteropdf connect"
    )
    assert f"Run `{expected}` and paste a valid personal access token" in caplog.text
    if profile == "lab":
        assert "`noteropdf connect`" not in caplog.text


@pytest.mark.parametrize("profile", [None, "lab"])
def test_sync_row_recovery_keeps_selected_connection(
    monkeypatch, isolated_paths, capsys, rejected_token_error, profile
):
    from noteropdf.models import SyncRow
    from noteropdf.sync_engine import SyncEngine

    selected = []
    row = SyncRow(
        "ITEM0001",
        None,
        None,
        None,
        None,
        None,
        "skip",
        "NOTION_AUTH_ERROR",
        SyncEngine._format_api_error(rejected_token_error),
    )

    class Engine:
        def __init__(self, _cfg):
            pass

        def sync(self, **_kwargs):
            return [row]

        def preview_actions(self):
            return ()

        def close(self):
            pass

    monkeypatch.setattr(cli, "setup_run_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_load_ready_config",
        lambda selected_profile: (
            selected.append(selected_profile)
            or SimpleNamespace(zotero=SimpleNamespace(group_id=None))
        ),
    )
    monkeypatch.setattr(cli, "SyncEngine", Engine)
    arguments = ["sync"] + (["--profile", profile] if profile else [])
    assert cli.main(arguments) == 1
    assert selected == [profile]
    output = capsys.readouterr().out
    expected = "noteropdf connect --profile lab" if profile else "noteropdf connect"
    assert f"`{expected}`" in output
    if profile:
        assert "`noteropdf connect`" not in output


def test_doctor_scopes_local_settings_recovery(monkeypatch, isolated_paths, capsys):
    def invalid_settings(**_kwargs):
        raise ValueError(
            "Unsupported settings. Run `noteropdf connect` to create fresh settings."
        )

    monkeypatch.setattr(cli, "load_local_settings", invalid_settings)
    monkeypatch.setattr(cli, "detect_zotero_data_dir", lambda: None)
    monkeypatch.setattr(
        cli, "_credential_store", lambda _profile: SimpleNamespace(load=lambda: None)
    )
    assert cli._run_doctor("lab") == 2
    output = capsys.readouterr().out
    assert "Run `noteropdf connect --profile lab` to create fresh settings." in output
    assert "`noteropdf connect`" not in output


@pytest.mark.parametrize("profile,flag", [(None, ""), ("lab", " --profile lab")])
@pytest.mark.parametrize("item", ["ITEM1234", "groups/123/ITEM1234"])
def test_pdf_recovery_preserves_selected_connection_and_parent_key(profile, flag, item):
    from noteropdf.profiles import recovery_guidance

    message = "Choose it again with `noteropdf pdf`."
    assert (
        recovery_guidance(message, profile, item_key=item)
        == f"Choose it again with `noteropdf pdf{flag} ITEM1234`."
    )
