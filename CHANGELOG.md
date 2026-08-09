# Changelog

All notable changes to this project are documented in this file.

## 0.4.0 - 2026-08-09

### Added

- one-command first run with Zotero detection, a paste-once Notion personal access token, database discovery, preview, and confirmation
- secure token storage through a supported operating-system credential backend
- stable Notion property-ID discovery and optional creation of a dedicated `NoteroPDF PDF` files property
- exact preview-action approval, remote-file ownership records, and protection against concurrent Notion changes
- Python 3.14 support

### Changed

- reduced the public CLI to `sync`, `connect`, and `doctor`; running without a command starts sync
- made interactive sync preview and ask once, non-interactive sync preview only with a nonzero pending-work status, and `--apply` explicit
- updated the Notion transport to the `2026-03-11` REST API, data sources, direct/multipart uploads, bounded retries, and the 10,000-page query safety limit
- stored non-secret settings and sync state in a local `noteropdf.sqlite3` database
- replaced per-item Notion queries with one filtered data-source snapshot per run
- read the running Zotero library only through its supported local API
- restricted page matching to each item's unique Notero-created link attachment
- replaced per-run output files with a small rotating diagnostic log
- made unknown files, duplicate item-to-page mappings, unsafe Zotero paths, and incomplete Notion snapshots fail closed

### Removed

- YAML and `.env` configuration, environment-variable token overrides, manual token/database-ID configuration, and persistent dry-run settings
- the `setup` and `cleanup` commands, destructive page cleanup, and CSV/JSON/summary reports
- `sync --force`, direct Zotero database access, and Zotero URI/DOI fallback matching
- the PyYAML and python-dotenv runtime dependencies
- unsigned standalone archives; PyPI/pipx is the supported installation path

## 0.3.1 - 2026-05-15

### Changed

- simplified the README around the PyPI install path and first-time user workflow
- expanded supported Python versions to include Python 3.10
- updated PyPI package metadata with a clearer project description and broader utility topic classifier

## 0.3.0 - 2026-05-15

### Added

- cleanup command for previewing and applying stale or duplicate Notion row trashing
- user-friendly colored terminal output with `--no-color`, `NO_COLOR`, and `--verbose` support
- confirmation prompt for `cleanup --apply`, plus `--yes` for scripted runs
- package metadata for license, project URLs, Python 3.11-3.13, and OS classifiers

### Changed

- simplified normal terminal output while keeping detailed technical logs in run log files
- accepted `--verbose` and `--no-color` before or after public commands
- updated README and release checks around preview-first sync and cleanup workflows
- stopped creating default app directories just by loading config
- avoided opening the sync-state database or lock during `doctor` and cleanup preview
- made report filenames collision-resistant for runs that start in the same second

### Fixed

- normalized cleanup URI comparisons so case differences in Zotero web URLs do not mark live rows stale
- treated `TERM=dumb` as plain output with no ANSI color
- made `cleanup --apply` fail clearly in non-interactive shells unless `--yes` is provided

## 0.2.1 - 2026-03-18

### Fixed

- accepted `collection://...` Notion data-source URLs even when pasted into the setup database prompt
- normalized setup target IDs before writing config output to avoid malformed values
- made default-config fallback apply only when `--config` was not explicitly passed

## 0.2.0 - 2026-03-18

### Added

- guided Notion target discovery during `setup` using the provided integration token
- stronger Zotero data-directory detection across Windows, macOS, and Linux
- multipart upload support for larger PDFs when the Notion workspace supports it
- published standalone GitHub Release bundles for Windows, macOS, and Linux
- installed-artifact smoke checks in CI and release validation

### Changed

- simplified the public CLI to `setup`, `doctor`, and `sync`
- reduced the default config surface to the fields a normal user is expected to edit
- made `sync` repair common Notion PDF-field drift instead of silently skipping it
- clarified setup and release docs around bundle installs, best-effort discovery, and dry-run validation

### Removed

- destructive recovery commands from the public CLI
- support-bundle command and its related maintenance complexity

## 0.1.0 - 2026-03-15

### Added

- initial public release
