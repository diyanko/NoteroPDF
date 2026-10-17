import logging

import pytest
import requests

from noteropdf import cli
from noteropdf.auth import CredentialStoreError
from noteropdf.config import SetupRequired
from noteropdf.logging_setup import setup_run_logging
from noteropdf.models import SyncRow
from noteropdf.notion_client import NotionApiError, NotionClient
from noteropdf.settings import LocalSettings

PRIVATE = "Confidential paper /private/research/hidden-file.pdf page-123 secret-token"


@pytest.fixture(autouse=True)
def preserve_logging():
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    try:
        yield
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if handler not in handlers:
                handler.close()
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)


def diagnostic_text(log_dir):
    for handler in logging.getLogger().handlers:
        handler.flush()
    return (log_dir / "noteropdf.log").read_text(encoding="utf-8")


def assert_private_text_absent(text):
    for private in (
        "Confidential paper",
        "/private/research",
        "hidden-file.pdf",
        "page-123",
        "secret-token",
    ):
        assert private not in text
    assert "Traceback" not in text


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize(
    "error,expected_exit",
    [
        (RuntimeError(PRIVATE), 1),
        (
            FileNotFoundError(
                2, "File unavailable", "/private/research/hidden-file.pdf"
            ),
            1,
        ),
        (ValueError(PRIVATE), 2),
        (SetupRequired(PRIVATE), 2),
        (CredentialStoreError(PRIVATE), 2),
        (NotionApiError("NOTION_AUTH_ERROR", PRIVATE, hint=PRIVATE), 2),
        (NotionApiError("PRIVATE_STATUS", PRIVATE, hint=PRIVATE), 2),
    ],
)
def test_cli_errors_never_echo_exception_payloads(
    monkeypatch, tmp_path, capsys, verbose, error, expected_exit
):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(cli, "get_profile_log_dir", lambda _profile: log_dir)

    def fail(**_kwargs):
        logging.getLogger("urllib3.connectionpool").debug(PRIVATE)
        logging.getLogger("requests").warning(PRIVATE)
        raise error

    monkeypatch.setattr(cli, "_run_sync", fail)
    arguments = ["sync", "--profile", "lab"] + (["--verbose"] if verbose else [])
    assert cli.main(arguments) == expected_exit
    captured = capsys.readouterr()
    text = captured.out + captured.err + diagnostic_text(log_dir)
    assert_private_text_absent(text)
    assert str(tmp_path) not in text
    if expected_exit == 2:
        assert "noteropdf connect --profile lab" in text or "credential store" in text
    if expected_exit == 1:
        assert "noteropdf doctor --profile lab" in text


@pytest.mark.parametrize("transport_failure", [False, True])
def test_real_notion_error_mapping_does_not_expose_http_data(
    monkeypatch, tmp_path, capsys, transport_failure
):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(cli, "get_profile_log_dir", lambda _profile: log_dir)
    client = NotionClient("secret-token", "2026-03-11", max_retries=1)

    def request(*_args, **_kwargs):
        logging.getLogger("urllib3.connectionpool").debug(
            "POST /v1/pages/page-123 %s", PRIVATE
        )
        if transport_failure:
            raise requests.ConnectionError(PRIVATE)
        response = requests.Response()
        response.status_code = 400
        response._content = (
            '{"code":"validation_error","message":"' + PRIVATE + '"}'
        ).encode()
        return response

    monkeypatch.setattr(client._session, "request", request)
    monkeypatch.setattr(cli, "_run_sync", lambda **_kwargs: client.ping())
    try:
        assert cli.main(["sync", "--profile", "lab", "--verbose"]) == 2
    finally:
        client.close()
    captured = capsys.readouterr()
    text = captured.out + captured.err + diagnostic_text(log_dir)
    assert_private_text_absent(text)
    assert "noteropdf connect --profile lab" in text


def test_file_handler_omits_exception_chains_and_stack_info(tmp_path, capsys):
    setup_run_logging(tmp_path, no_color=True, verbose=True)
    try:
        raise RuntimeError(PRIVATE)
    except RuntimeError:
        logging.getLogger("noteropdf.test").exception(
            "Operation failed", stack_info=True
        )
    captured = capsys.readouterr()
    text = captured.err + diagnostic_text(tmp_path)
    assert "Operation failed" in text
    assert_private_text_absent(text)
    assert "Stack (most recent call last)" not in text
    assert "test_private_diagnostics.py" not in text


@pytest.mark.parametrize("command", ["sync", "doctor"])
def test_log_setup_failure_and_doctor_fallback_are_private(
    monkeypatch, capsys, command
):
    def unavailable(*_args, **_kwargs):
        raise OSError(PRIVATE)

    def doctor(_profile):
        logging.getLogger("urllib3.connectionpool").warning(PRIVATE)
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(cli, "setup_run_logging", unavailable)
    monkeypatch.setattr(cli, "_run_doctor", doctor)
    assert cli.main([command, "--profile", "lab", "--verbose"]) == 1
    captured = capsys.readouterr()
    assert_private_text_absent(captured.out + captured.err)


def test_noninteractive_doctor_omits_paths_and_credential_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "load_local_settings",
        lambda **_kwargs: LocalSettings(
            zotero_data_dir="/private/research/hidden-file.pdf"
        ),
    )
    monkeypatch.setattr(cli, "_credential_store", lambda _profile: object())

    def fail(_store):
        raise CredentialStoreError(PRIVATE)

    monkeypatch.setattr(cli, "resolve_access_token", fail)
    assert cli._run_doctor("lab") == 2
    output = capsys.readouterr().out
    assert_private_text_absent(output)
    assert "noteropdf connect --profile lab" in output
    assert "credential store" in output


@pytest.mark.parametrize(
    "status", ["NOTION_AUTH_ERROR", "BROKEN_ATTACHMENT_PATH", "UNKNOWN_PRIVATE_STATUS"]
)
def test_shared_source_outcomes_use_status_guidance_only(capsys, status):
    row = SyncRow(
        "PARENT01:ATT00001",
        PRIVATE,
        PRIVATE,
        PRIVATE,
        PRIVATE,
        PRIVATE,
        "skip",
        status,
        PRIVATE,
    )
    cli._print_source_outcomes([row], preview=True, details=True, profile="lab")
    output = capsys.readouterr().out
    assert_private_text_absent(output)
    assert "UNKNOWN_PRIVATE_STATUS" not in output
    assert "Item PARENT01" in output
    if status == "NOTION_AUTH_ERROR":
        assert "noteropdf connect --profile lab" in output


def test_log_write_failure_never_dumps_paths_or_tracebacks(
    monkeypatch, tmp_path, capsys
):
    setup_run_logging(tmp_path, no_color=True)
    file_handler = next(
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "baseFilename", None)
    )

    def fail(_record):
        raise OSError(PRIVATE)

    monkeypatch.setattr(file_handler, "shouldRollover", fail)
    logging.getLogger("noteropdf.test").info("Status check completed")
    captured = capsys.readouterr()
    assert_private_text_absent(captured.err)
    assert "diagnostic log could not be written" in captured.err


def test_existing_logs_are_preserved_while_new_messages_are_redacted(tmp_path):
    log = tmp_path / "noteropdf.log"
    log.write_text("Prior local diagnostic content\n", encoding="utf-8")
    setup_run_logging(tmp_path, no_color=True)
    logging.getLogger("noteropdf.test").info("Status check completed")
    text = diagnostic_text(tmp_path)
    assert text.startswith("Prior local diagnostic content\n")
    assert text.endswith("Status check completed\n")


@pytest.mark.parametrize(
    "stdin_tty,stdout_tty", [(True, False), (False, True), (False, False)]
)
def test_setup_rejects_redirected_streams_before_reading_private_data(
    monkeypatch, capsys, stdin_tty, stdout_tty
):
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: stdin_tty)
    monkeypatch.setattr(cli, "_stdout_interactive", lambda: stdout_tty)
    for name in (
        "load_local_settings",
        "_credential_store",
        "NotionClient",
        "_select_zotero_directory",
    ):
        monkeypatch.setattr(
            cli,
            name,
            lambda *_args, **_kwargs: pytest.fail(
                "Setup accessed private data before checking both terminal streams"
            ),
        )
    with pytest.raises(SetupRequired, match="interactive terminal"):
        cli._run_connect("lab")
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_first_run_with_redirected_stdout_never_starts_setup(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli, "get_profile_log_dir", lambda _profile: tmp_path)
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(cli, "_stdout_interactive", lambda: False)

    def not_connected(_profile):
        raise SetupRequired(PRIVATE)

    monkeypatch.setattr(cli, "_load_ready_config", not_connected)
    for name in ("_run_connect", "_credential_store", "_prompt_yes_no"):
        monkeypatch.setattr(
            cli,
            name,
            lambda *_args, **_kwargs: pytest.fail(
                "Redirected first run entered interactive setup"
            ),
        )
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "noteropdf connect" in captured.err
    assert_private_text_absent(captured.err + diagnostic_text(tmp_path))
