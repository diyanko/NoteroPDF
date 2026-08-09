# Maintaining and Releasing NoteroPDF

This is the single maintainer workflow. Normal work goes directly to `main`;
use a branch or pull request only when a particular change needs one.

## One-time setup

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[test,qa,build]"
```

## Develop and validate

Run focused tests while editing:

```bash
python -m pytest -q tests/test_<area>.py
```

Before every push that changes code, dependencies, packaging, or workflows, run:

```bash
git diff --check
ruff check .
ruff format --check noteropdf tests
mypy
python -m pytest --cov=noteropdf --cov-report=term-missing --cov-fail-under=75 -q
```

For a release candidate or packaging change, also build into a fresh directory:

```bash
release_artifacts="$(mktemp -d)"
python -m build --outdir "$release_artifacts"
python -m twine check --strict "$release_artifacts"/*
check-wheel-contents "$release_artifacts"/*.whl
```

CI repeats the full quality gate on Python 3.14, validates the package, and runs
the tests on every supported Python version plus macOS and Windows.

## Real Zotero and Notion check

When setup, authentication, Zotero reading, matching, uploads, or sync safety
changes, test the affected journey against a controlled real workspace:

Keep Zotero open with its local API enabled. Use an item with exactly one PDF
and one Notero-created link attachment named `Notion`; do not substitute a URI,
DOI, title, or manual page mapping.

```bash
python -m noteropdf doctor
python -m noteropdf sync
```

Review the preview and cancel once; verify Notion did not change. Confirm that a
non-interactive preview exits `2` when uploads are pending and makes no changes:

```bash
set +e
python -m noteropdf sync </dev/null
preview_exit=$?
set -e
test "$preview_exit" -eq 2
```

Approve a real upload only when the preview contains exactly the intended test
item. Never approve a larger production batch merely to complete a smoke test.
Verify the uploaded PDF opens, then rerun sync and confirm the item is unchanged.
Retest first-time connection or token replacement only when that code changed,
and confirm the PAT is stored by the operating system credential backend.

## Commit and push

Review and stage only intended files:

```bash
git status --short
git add <explicit-files>
git diff --cached --check
git diff --cached
git commit -m "Concise description"
git push origin main
```

Wait for the `CI` workflow on that exact commit. Fix any failure before tagging.

## Publish a release

Publishing is a separate, explicit action. Update the version in `pyproject.toml`
and `CHANGELOG.md` together, commit and push them, complete the checks above, and
wait for CI. Then tag that already-tested commit:

```bash
release_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
test -z "$(git status --porcelain)"
test "$(git branch --show-current)" = "main"
git tag -a "v${release_version}" -m "Release v${release_version}"
git push origin "v${release_version}"
```

The tag-only `Release` workflow validates the tag and its ancestry on `main`,
builds and fresh-installs the exact wheel and source distribution, and then waits
for approval on the protected `pypi` environment. After approval, trusted
publishing sends the distributions to PyPI and creates the GitHub Release.

The permanent publishing identifiers are:

- repository: `diyanko/NoteroPDF`
- workflow: `release.yml`
- GitHub environment and PyPI publisher environment: `pypi`
- allowed deployment tags: `v*`

After the workflow completes, verify the version and metadata on PyPI, the wheel
and source distribution on the GitHub Release, and a fresh `pipx install` or
upgrade followed by `noteropdf --version`.
