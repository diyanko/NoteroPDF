# Release Checklist

Use this with `RELEASE_PROCESS.md`.

## Pre-release

- [ ] Start from a clean virtual environment
- [ ] `python -m pip install -e ".[dev]"`
- [ ] Update the version in `pyproject.toml`
- [ ] Update `CHANGELOG.md`
- [ ] `python -m pytest -q`
- [ ] `python -m build`
- [ ] Use Python 3.12 for the bundle build
- [ ] `pyinstaller --noconfirm --clean --specpath build/pyinstaller --name noteropdf --onedir --collect-submodules keyring.backends noteropdf/__main__.py`
- [ ] Treat the local PyInstaller build as a smoke check only
- [ ] Install the built wheel by explicit filename and run `python -m noteropdf --help`

## Real-world check

- [ ] Run `noteropdf setup` on a real machine
- [ ] Run `noteropdf doctor`
- [ ] Run one dry-run sync
- [ ] Run one real sync against a real Zotero + Notion setup
- [ ] Confirm `sync` repairs a common drift case by clearing or mismatching one Notion PDF field and rerunning

## Docs and packaging

- [ ] README still matches the actual CLI
- [ ] No secrets or machine-specific paths are committed
- [ ] GitHub Actions release workflow produced wheel, sdist, and standalone Windows/macOS/Linux bundles

## Publish

- [ ] Commit release changes
- [ ] Tag `vX.Y.Z`
- [ ] Push branch and tag
- [ ] Verify the GitHub release artifacts and notes
- [ ] Verify the GitHub release page contains the wheel, source tarball, and all standalone bundle zip assets
