# Release Checklist (GitHub Source CLI)

## 1. Safety and Logic
- [ ] `python -m noteropdf doctor` passes on a real setup.
- [ ] Run `sync` once with `dry_run: true` and review reports.
- [ ] Run `sync` with `dry_run: false` and verify expected rows only.
- [ ] Confirm destructive command confirmations block accidental execution.

## 2. Quality Gates
- [ ] `python -m pytest -q` passes.
- [ ] `python -m build` succeeds.
- [ ] No secrets in tracked files (`.env`, tokens, personal paths).

## 3. Docs and UX
- [ ] README setup steps work on a clean virtual environment.
- [ ] CLI examples in README match current command behavior.
- [ ] User-facing messages are clear and non-technical.

## 4. Release Hygiene
- [ ] Update `CHANGELOG.md` with date + release notes.
- [ ] Tag release version in git.
- [ ] Publish release notes in GitHub with known limitations (macOS-only v1).
