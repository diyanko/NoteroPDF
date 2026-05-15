# Changelog

All notable changes to this project will be documented in this file.

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
