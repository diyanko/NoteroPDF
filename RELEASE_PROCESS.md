# Release Process

Use this process for every release.

## 1. Update release files

- bump `version` in `pyproject.toml`
- add a short entry to `CHANGELOG.md`

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

## 3. Publish

```bash
git add .
git commit -m "release: vX.Y.Z"
git tag -a "vX.Y.Z" -m "Release vX.Y.Z"
git push origin main
git push origin "vX.Y.Z"
```

## 4. Confirm GitHub artifacts

The GitHub Release page should contain:

- wheel
- source tarball
- standalone Windows bundle zip
- standalone macOS bundle zip
- standalone Linux bundle zip
