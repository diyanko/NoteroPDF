# Changelog

All notable changes to this project will be documented in this file.

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
