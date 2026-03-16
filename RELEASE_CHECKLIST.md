# Release Checklist

Use this checklist together with `RELEASE_PROCESS.md`.

## 1. Prepare
- [ ] Decide version (`MAJOR.MINOR.PATCH`) and set `VERSION`.
- [ ] Update `pyproject.toml` version.
- [ ] Add a new dated section in `CHANGELOG.md`.

## 2. Validate
- [ ] `python -m pytest -q` passes.
- [ ] `python -m build` succeeds.
- [ ] `python -m noteropdf doctor` passes on a real setup.
- [ ] Run one dry-run sync and review reports.
- [ ] Run one real sync and confirm expected rows only.
- [ ] Confirm destructive command confirmation prompts work.

## 3. Docs and Safety
- [ ] README commands and examples still match current CLI behavior.
- [ ] No secrets or personal paths in tracked files.
- [ ] User-facing messages remain clear and non-technical.

## 4. Publish
- [ ] Commit release files with a release commit message.
- [ ] Create annotated tag `v${VERSION}`.
- [ ] Push `main` and the tag.
- [ ] Verify GitHub release workflow succeeds.
- [ ] Verify release artifacts include `.whl` and `.tar.gz`.
- [ ] Publish/verify GitHub release notes.

## 5. Post-Publish
- [ ] Install from released wheel URL and run `noteropdf --help` smoke test.
