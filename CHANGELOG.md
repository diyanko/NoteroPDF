# Changelog

## 0.1.0 - 2026-03-15

### Added
- Two-step confirmation flow for destructive commands (`rebuild-page-files --yes`, `full-reset --yes`).
- Write preflight output before commands that may update Notion.
- Stronger safety reporting in `doctor`, including explicit Zotero read-only guarantees.
- New status coverage and user guidance for `STATE_SAVE_FAILED` and `NOTION_NETWORK_ERROR`.
- CI workflow for tests and package build smoke checks.
- GitHub release workflow that builds and uploads wheel/sdist artifacts when a `v*` tag is pushed.
- Release checklist for safer public releases.

### Changed
- Clearer non-technical command output: "what happened" + "what to do next" summary blocks.
- Improved Notion API error mapping with user-action hints.
- Extra config sanity checks (token quality, log level validation, duplicate property field guard).

### Safety Notes
- Zotero remains strictly read-only (`mode=ro&immutable=1`) and query-guarded.
- Sync behavior remains deterministic and does not use fuzzy matching.
