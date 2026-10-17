import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_release_ci import ci_passed

COMMIT = "a" * 40


def _run(**changes):
    return {
        "path": ".github/workflows/ci.yml",
        "head_sha": COMMIT,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


def test_release_requires_main_push_ci_on_exact_commit():
    assert ci_passed({"workflow_runs": [_run()]}, COMMIT)


@pytest.mark.parametrize(
    "changes",
    [
        {"head_sha": "b" * 40},
        {"head_branch": "feature/example"},
        {"event": "pull_request"},
        {"path": ".github/workflows/release.yml"},
        {"status": "in_progress", "conclusion": None},
        {"status": "queued", "conclusion": None},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
        {"conclusion": None},
    ],
)
def test_release_rejects_unsuccessful_or_unrelated_ci(changes):
    assert not ci_passed({"workflow_runs": [_run(**changes)]}, COMMIT)


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"workflow_runs": []}, {"workflow_runs": [None]}],
)
def test_release_rejects_missing_or_invalid_ci_response(payload):
    assert not ci_passed(payload, COMMIT)


def test_older_success_cannot_override_latest_failure():
    assert not ci_passed(
        {"workflow_runs": [_run(conclusion="failure"), _run()]}, COMMIT
    )


def test_release_command_fails_closed_without_valid_json():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_release_ci.py"
    result = subprocess.run(
        [sys.executable, str(script), COMMIT],
        input="{invalid response",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Release blocked" in result.stderr
    assert "Traceback" not in result.stderr
