"""Require a successful main-push CI run for the exact release commit."""

import argparse
import json
import sys


def ci_passed(payload: object, commit: str) -> bool:
    if not isinstance(payload, dict):
        return False
    runs = payload.get("workflow_runs")
    # The API request asks for only the latest matching run. Older successes must
    # not override a newer failed, cancelled, or still-running validation.
    if not isinstance(runs, list) or len(runs) != 1:
        return False
    run = runs[0]
    return isinstance(run, dict) and all(
        run.get(key) == value
        for key, value in {
            "path": ".github/workflows/ci.yml",
            "head_sha": commit,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit", help="exact commit SHA being released")
    args = parser.parse_args()
    try:
        passed = ci_passed(json.load(sys.stdin), args.commit)
    except (ValueError, OSError):
        passed = False
    if not passed:
        print(
            "Release blocked: the latest CI push run on main for this exact "
            "commit must complete successfully. Check CI, then rerun Release.",
            file=sys.stderr,
        )
        return 1
    print("CI passed on main for the exact release commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
