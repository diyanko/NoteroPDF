# Release Checklist

Use this with `RELEASE_PROCESS.md`.

## Pre-release

- [ ] Start from a clean virtual environment
- [ ] `python -m pip install -e ".[dev]"`
- [ ] Update the version in `pyproject.toml`
- [ ] Update `CHANGELOG.md`
- [ ] If Python support changed, update package metadata, CLI version guard, README, and CI matrix together
- [ ] `python -m pytest -q`
- [ ] `python -m build`
- [ ] Use Python 3.12 for the bundle build
- [ ] `pyinstaller --noconfirm --clean --specpath build/pyinstaller --name noteropdf --onedir --collect-submodules keyring.backends noteropdf/__main__.py`
- [ ] Treat the local PyInstaller build as a smoke check only
- [ ] Install the built wheel by explicit filename and run `python -m noteropdf --help`

## Real-world check

- [ ] Run `noteropdf setup` on a real machine
- [ ] Run `noteropdf doctor`
- [ ] Confirm normal terminal output is readable without `--verbose`
- [ ] Confirm `--verbose` shows technical details and the log path is useful
- [ ] Confirm `--no-color` and `NO_COLOR=1` produce plain output
- [ ] Run one dry-run sync
- [ ] Run one real sync against a real Zotero + Notion setup
- [ ] Confirm `sync` repairs a common drift case by clearing or mismatching one Notion PDF field and rerunning
- [ ] Run `noteropdf cleanup` and review the preview report
- [ ] Run `noteropdf cleanup --apply`, confirm the prompt, and verify only stale rows or canonical duplicates moved to Notion trash
- [ ] If testing duplicate cleanup, verify only a row duplicated by an active canonical Notero page is moved to trash
- [ ] Run `noteropdf cleanup --apply` again and decline the prompt to verify no Notion changes occur

## Docs and packaging

- [ ] README still matches the actual CLI
- [ ] `noteropdf --help`, `noteropdf sync --help`, and `noteropdf cleanup --help` use user-friendly wording
- [ ] PyPI trusted publishing is configured for `diyanko/NoteroPDF` and `.github/workflows/release.yml`
- [ ] Release workflow grants `id-token: write` and uses `pypa/gh-action-pypi-publish@release/v1` without a PyPI token
- [ ] No secrets or machine-specific paths are committed
- [ ] GitHub Actions release workflow produced wheel, sdist, and standalone Windows/macOS/Linux bundles
- [ ] PyPI release page contains `noteropdf` version `X.Y.Z`

## Publish

- [ ] Commit release changes as `chore(release): vX.Y.Z`
- [ ] Confirm `git log -1 --oneline` shows the release commit
- [ ] Tag `vX.Y.Z` from that exact release commit
- [ ] Push branch and tag
- [ ] Verify the GitHub release artifacts and notes
- [ ] Verify the GitHub release page contains the wheel, source tarball, and all standalone bundle zip assets
