"""Run the same read-only source checks locally and in CI."""

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def python(*args: str) -> None:
    run(sys.executable, "-m", *args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", action="store_true", help="also build and validate distributions"
    )
    args = parser.parse_args()
    try:
        run("git", "diff", "--check")
        run("git", "diff", "--cached", "--check")
        python("ruff", "check", ".")
        python("ruff", "format", "--check", "noteropdf", "tests", "scripts")
        python("mypy")
        python("mypy", "scripts")
        python(
            "pytest",
            "--cov=noteropdf",
            "--cov-report=term-missing",
            "--cov-fail-under=75",
            "-q",
        )
        if args.package:
            with TemporaryDirectory(prefix="noteropdf-check-") as directory:
                python("build", "--outdir", directory)
                artifacts = sorted(Path(directory).iterdir())
                wheels = [p for p in artifacts if p.suffix == ".whl"]
                sources = [p for p in artifacts if p.name.endswith(".tar.gz")]
                if len(wheels) != 1 or len(sources) != 1 or len(artifacts) != 2:
                    raise RuntimeError(
                        "Expected exactly one wheel and one source archive."
                    )
                python("twine", "check", "--strict", *(str(p) for p in artifacts))
                python("check_wheel_contents", str(wheels[0]))
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        print(
            "Checks failed. Fix the error above and rerun. "
            'Install tooling with: python -m pip install -e ".[test,qa,build]"',
            file=sys.stderr,
        )
        return 1
    print("All requested checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
