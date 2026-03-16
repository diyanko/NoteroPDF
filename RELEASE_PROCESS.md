# Release Process

This is the standard operating process for publishing NoteroPDF releases.
Use this flow for every release to keep versioning, changelog updates, and GitHub releases consistent.

## Goals

- Keep each release repeatable and low-risk.
- Use one clear source of truth for version and notes.
- Minimize manual decisions during publish.

## Versioning Rules

Use semantic versioning: `MAJOR.MINOR.PATCH`.

- `PATCH`: bug fixes, docs-only fixes, small safe improvements.
- `MINOR`: new backward-compatible features.
- `MAJOR`: breaking changes.

Examples: `0.1.1`, `0.2.0`, `1.0.0`.

## Standard Release Flow

Run from repository root.

1. Choose the new version and export it:

```bash
export VERSION=0.1.1
```

2. Update version metadata:

- Update `pyproject.toml` version to `${VERSION}`.
- Add a new section in `CHANGELOG.md` using the format:
  - `## ${VERSION} - YYYY-MM-DD`
  - include `Added`, `Changed`, `Fixed` sections when relevant.

3. Run quality gates:

```bash
python -m pytest -q
python -m build
python -m noteropdf doctor
```

4. Verify docs and safety checks:

- Confirm README commands still match current CLI behavior.
- Confirm no secrets or local paths are committed.
- Run all checks in `RELEASE_CHECKLIST.md`.

5. Commit release changes:

```bash
git add pyproject.toml CHANGELOG.md README.md RELEASE_CHECKLIST.md RELEASE_PROCESS.md
git commit -m "release: v${VERSION}"
```

6. Create annotated tag:

```bash
git tag -a "v${VERSION}" -m "Release v${VERSION}"
```

7. Push branch and tag:

```bash
git push origin main
git push origin "v${VERSION}"
```

8. Verify GitHub release pipeline:

- Confirm release workflow succeeds for tag `v${VERSION}`.
- Confirm both artifacts exist: wheel (`.whl`) and source (`.tar.gz`).
- Publish/verify GitHub release notes.

9. Smoke-test install from GitHub release:

```bash
python -m pip install "https://github.com/diyanko/NoteroPDF/releases/download/v${VERSION}/noteropdf-${VERSION}-py3-none-any.whl"
noteropdf --help
```

## Hotfix Flow

For urgent fixes:

1. Branch from `main`.
2. Apply minimal fix.
3. Add/adjust tests.
4. Bump `PATCH` version only.
5. Follow the same Standard Release Flow.

## If You Need to Re-Tag Before Publish

Only do this if release artifacts are not yet published and you intentionally need to recreate the tag.

```bash
git tag -d "v${VERSION}"
git push origin ":refs/tags/v${VERSION}"
git tag -a "v${VERSION}" -m "Release v${VERSION}"
git push origin "v${VERSION}"
```

## Release Notes Template

Use this in `CHANGELOG.md`:

```md
## X.Y.Z - YYYY-MM-DD

### Added
- ...

### Changed
- ...

### Fixed
- ...
```