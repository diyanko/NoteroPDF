# Release Process

Use this process for every release.

## 1. Update release files

- bump `version` in `pyproject.toml`
- add a short entry to `CHANGELOG.md`
- if supported Python versions changed, update `pyproject.toml`, `noteropdf/cli.py`, `README.md`, and the CI matrix together

## 2. Verify locally

Use a clean virtual environment. The intended maintainer path is plain `venv` plus `pip`, not conda, poetry, or pipenv.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
python -m pip install --force-reinstall ./dist/noteropdf-X.Y.Z-py3-none-any.whl
python -m noteropdf --help
pyinstaller --noconfirm --clean --specpath build/pyinstaller --name noteropdf --onedir --collect-submodules keyring.backends noteropdf/__main__.py
```

Use Python 3.12 for the standalone bundle build so local release packaging matches CI.
Treat the local PyInstaller build as a smoke check. Official release bundles should be produced by GitHub Actions on pinned runners.
Replace `X.Y.Z` with the version you just built. Using the explicit wheel filename keeps the command valid in both PowerShell and POSIX shells.

Then verify a real setup:

```bash
noteropdf setup
noteropdf doctor
noteropdf sync
```

Also verify one common repair case on a real workspace by clearing or mismatching a Notion PDF field and confirming a later `noteropdf sync` run restores it.

## 3. Create the release commit

Every release should have one explicit release commit, and the release tag should
point to that exact commit. Do not make unrelated commits between the release
commit and the tag.

```bash
git add .
git commit -m "chore(release): vX.Y.Z"
git status -sb
git log -1 --oneline
```

Confirm the latest commit is `chore(release): vX.Y.Z` before tagging.

## 4. Tag and publish

```bash
git tag -a "vX.Y.Z" -m "Release vX.Y.Z"
git push origin main
git push origin "vX.Y.Z"
```

## 5. Confirm GitHub artifacts

The GitHub Release page should contain:

- wheel
- source tarball
- standalone Windows bundle zip
- standalone macOS bundle zip
- standalone Linux bundle zip

The tag release workflow also publishes the wheel and source tarball to PyPI
through trusted publishing. Confirm the PyPI project page shows the new version
after the workflow finishes.
