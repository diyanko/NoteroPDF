import logging

import pytest

from noteropdf import cli
from noteropdf.models import SyncRow


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/private/folder/paper.pdf", "paper.pdf"),
        (r"C:\private\folder\paper.pdf", "paper.pdf"),
        (r"\\server\private\paper.pdf", "paper.pdf"),
        ("/private/paper\x1b[31m\n\u202e.pdf", "paper?[31m??.pdf"),
        ("/private/" + "x" * 200, "x" * 120),
        ("/private/", "File"),
    ],
)
def test_local_attachment_names_are_basename_only_and_terminal_safe(
    monkeypatch, caplog, path, expected
):
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    with caplog.at_level(logging.DEBUG):
        assert cli._local_attachment_name(path) == f" ({expected})"
    assert caplog.records == []


def test_pdf_recovery_keeps_exact_item_and_profile(capsys):
    row = SyncRow(
        "groups/123/PARENT01",
        "private title",
        "",
        "private page",
        "",
        None,
        "skip",
        "INVALID_PDF_SELECTION",
        None,
    )
    cli._print_summary([row], preview=True, profile="lab")
    output = capsys.readouterr().out
    assert "noteropdf pdf --profile lab PARENT01" in output
    assert "private" not in output


@pytest.mark.parametrize(
    "input_tty,output_tty", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize(
    "property_name", ["Source files", "private-schema", "private\nschema"]
)
def test_source_preview_shows_filename_only_in_local_terminals(
    monkeypatch, capsys, input_tty, output_tty, property_name
):
    from types import SimpleNamespace

    from noteropdf.sync_engine import PreviewAction

    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass

        def sync(self, *, apply):
            assert not apply
            return []

        def preview_actions(self):
            return (
                PreviewAction(
                    "PARENT01:ATT00001",
                    "private-page",
                    "/private/folder/data.xlsx",
                    1024,
                    1,
                    "digest",
                ),
            )

        def close(self):
            pass

    monkeypatch.setattr(cli, "_load_ready_config", lambda _profile: SimpleNamespace())
    monkeypatch.setattr(cli, "source_files_config", lambda cfg, _property: cfg)
    monkeypatch.setattr(cli, "SourceFilesEngine", Engine)
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: input_tty)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: output_tty)
    monkeypatch.setattr(cli, "_prompt_yes_no", lambda *_args, **_kwargs: False)
    result = cli._run_sources(
        item_keys=("PARENT01",),
        property_name=property_name,
        apply_without_prompt=False,
    )
    assert result == (0 if input_tty else 2)
    output = capsys.readouterr().out
    assert ("data.xlsx" in output) == (input_tty and output_tty)
    assert "/private/folder" not in output
    assert "private-page" not in output

    if property_name != "Source files":
        assert property_name not in output
        if not input_tty:
            assert "<your files field>" in output
            assert "same field name" in output
