import shlex
from types import SimpleNamespace

import pytest

from noteropdf import cli
from noteropdf.models import SyncRow
from noteropdf.sync_engine import PreviewAction


def _row(action, status="OK", error=None):
    return SyncRow(
        "PARENT01:ATT00001",
        "private paper title",
        "private source",
        "private-page-id",
        "private page URL",
        "/private/research/private-name.xlsx",
        action,
        status,
        error,
    )


@pytest.mark.parametrize(
    "status,error",
    [
        (
            "STATE_SAVE_FAILED",
            "Files were attached but ownership could not be saved; existing files will be protected.",
        ),
        (
            "NOTION_NETWORK_ERROR",
            "The attachment response was lost. Preview again before trying to upload.",
        ),
        (
            "UPLOAD_FAILED",
            "A supporting file could not be uploaded. Review the current state.",
        ),
        ("OK", None),
    ],
)
def test_source_result_explains_partial_failure_and_keeps_profile(
    monkeypatch, capsys, status, error
):
    cfg = SimpleNamespace()
    engines = []
    selected_profiles = []
    approved = (
        PreviewAction(
            "PARENT01:ATT00001",
            "private-page-id",
            "/private/research/private-name.xlsx",
            1200,
            5,
            "digest",
        ),
    )

    class Engine:
        def __init__(self, config, *, item_keys):
            assert config is cfg
            assert item_keys == ("PARENT01",)
            engines.append(self)

        def sync(self, *, apply, approved_actions=None):
            if apply:
                assert approved_actions == approved
                return [
                    _row(
                        "upload_attach:source_file" if status == "OK" else "skip",
                        status,
                        error,
                    )
                ]
            return [_row("preview_upload:source_file")]

        def preview_actions(self):
            return approved

        def close(self):
            pass

    monkeypatch.setattr(
        cli,
        "_load_ready_config",
        lambda profile: selected_profiles.append(profile) or cfg,
    )
    monkeypatch.setattr(cli, "source_files_config", lambda config, _property: config)
    monkeypatch.setattr(cli, "SourceFilesEngine", Engine)
    monkeypatch.setattr(cli, "_stdin_interactive", lambda: False)
    result = cli._run_sources(
        item_keys=("PARENT01",),
        property_name="Source files",
        apply_without_prompt=True,
        profile="lab",
        details=True,
    )
    assert result == (0 if status == "OK" else 1)
    assert selected_profiles == ["lab", "lab"]
    assert len(engines) == 2
    output = capsys.readouterr().out
    assert "Supporting files result" in output
    assert "File updates verified:" in output
    if error:
        expected = {
            "STATE_SAVE_FAILED": "their history could not be saved",
            "NOTION_NETWORK_ERROR": "Notion could not be reached",
            "UPLOAD_FAILED": "A supporting file could not be uploaded",
        }
        assert expected[status] in output
        assert "noteropdf sources --profile lab --item-key PARENT01" in output
    if status == "STATE_SAVE_FAILED":
        assert "Some files may already be attached." in output
    for private in (
        "private paper title",
        "private-page-id",
        "/private/research",
        "private-name",
    ):
        assert private not in output


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize(
    "property_name",
    ["Source files", "Research's & $data", 'A "quoted" field; echo unsafe'],
)
def test_source_retry_quoting_preserves_property_names(
    monkeypatch, windows, property_name
):
    monkeypatch.setattr(cli.sys, "platform", "win32" if windows else "darwin")
    command = cli._sources_command(
        ("PARENT01",), property_name, "lab", details=True, apply=True
    )
    if windows:
        assert cli._source_shell() == "PowerShell"
        assert "--files-property '" + property_name.replace("'", "''") + "'" in command
    else:
        assert shlex.split(command) == [
            "noteropdf",
            "sources",
            "--profile",
            "lab",
            "--item-key",
            "PARENT01",
            "--files-property",
            property_name,
            "--details",
            "--apply",
        ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", "lab", "sources", "--item-key", "PARENT01", "--details"],
        ["sources", "--profile", "lab", "--item-key", "PARENT01", "--details"],
    ],
)
def test_sources_dispatch_preserves_named_profile_and_details(
    monkeypatch, tmp_path, arguments
):
    calls = []
    monkeypatch.setattr(cli, "setup_run_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_run_sources", lambda **kwargs: calls.append(kwargs) or 0)
    assert cli.main(arguments) == 0
    assert calls == [
        {
            "item_keys": ("PARENT01",),
            "property_name": "Source files",
            "apply_without_prompt": False,
            "profile": "lab",
            "details": True,
        }
    ]
