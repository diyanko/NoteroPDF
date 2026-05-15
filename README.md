# NoteroPDF

NoteroPDF uploads local Zotero PDFs to a files property in a Notion database already managed by [Notero](https://github.com/dvanoni/notero).

It reads Zotero in read-only mode, finds one deterministic PDF per item, matches the corresponding Notion row, and uploads the file.

## Before You Start

You need:

- Zotero installed with a local personal library
- A Notion integration token
- A Notero-managed Notion database shared with that integration
- A Notion files property in that database, usually named `PDF`

## Install

### Standalone Bundle

Recommended for most users. Download the release bundle for your OS, extract it, place the `noteropdf` executable on your `PATH`, and run:

```bash
noteropdf setup
```

Python is not required when using the standalone bundle.

On macOS, the bundle can still hit Gatekeeper or privacy permission prompts on some systems. If that happens, `xattr` may clear the quarantine flag, but it does not fix every security or access failure.

```bash
xattr -dr com.apple.quarantine /path/to/noteropdf
```

If the macOS bundle still fails after that, use the source install below.

### PyPI Install

Use this path if you already have Python 3.11, 3.12, or 3.13 installed.

```bash
python -m pip install noteropdf
noteropdf setup
```

### Source Install

Use this path on macOS if the bundle is blocked by system security or permission prompts, or when you want to install directly from a local checkout.

Supported Python versions: 3.11, 3.12, 3.13.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
python -m pip install -U pip
python -m pip install .
```

Run with:

```bash
python -m noteropdf setup
```

### Contributor Install

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Use It

Start with the guided setup:

```bash
noteropdf setup
```

Then check that Zotero and Notion are reachable:

```bash
noteropdf doctor
```

Run a preview sync first. Setup starts with `dry_run: true`, so this checks what would happen without changing Notion:

```bash
noteropdf sync
```

If the preview looks right, edit your saved `config.yaml`, set `dry_run: false`, and run:

```bash
noteropdf sync
```

To check for Notion rows that are safe cleanup candidates, run:

```bash
noteropdf cleanup
```

The preview can include rows whose Zotero item no longer exists and duplicate rows when Zotero still points to a different canonical Notero page for the same item.

If the cleanup preview looks right, apply it. NoteroPDF will ask once before moving rows to Notion trash:

```bash
noteropdf cleanup --apply
```

For scripted runs, use `noteropdf cleanup --apply --yes` to skip that confirmation.

The setup flow tries to detect your Zotero data folder, discover accessible Notion targets, and write `config.yaml` for you. If discovery cannot find a target, it asks for a Notion database URL/ID or data source URL/ID and continues.

Terminal output is meant to be readable for normal users. Detailed technical logs are still written to a log file for troubleshooting. Use `--verbose` when you want those details in the terminal, or `--no-color` if your terminal should stay plain.

## What It Does

- Matching order is fixed: Notero page link, Zotero URI, DOI
- A Zotero item must have exactly one usable PDF attachment
- Files over Notion's upload limit are skipped with a clear error
- Files up to 20 MB use single-part upload
- Larger files use Notion multi-part upload when the workspace supports it
- Cleanup is separate from sync and only trashes rows with a strong stale or duplicate classification
- Zotero data is never modified

## What Changes In Notion

`sync` only updates the configured Notion files property, usually `PDF`, on rows that can be matched confidently to Zotero items.

`cleanup` does not run during sync. By default it only reports cleanup candidates. With `cleanup --apply`, it moves only strongly classified stale rows and canonical duplicates from the local personal Zotero library to Notion trash. It skips rows that are ambiguous, outside the local personal library scope, or missing the configured Zotero URI field, so you can review them manually.

## Results

Each run writes local artifacts under standard OS app directories managed by `platformdirs`:

- run logs
- JSON report
- CSV report
- summary JSON

The terminal shows the short version. Reports contain the full item-by-item details.

## Troubleshooting

- `NOTION_AUTH_ERROR`: check that your Notion token is correct and that the database is shared with the integration.
- `NOTION_SCHEMA_ERROR`: check that the selected database has the configured properties, especially the `PDF` files property.
- `NO_NOTION_MATCH`: check the Notero page link, `Zotero URI`, or DOI for that item.
- `MULTIPLE_NOTION_MATCHES`: more than one Notion row matches the same Zotero item; make the Notion data unique.
- `NO_PDF`: add one PDF attachment to the Zotero item.
- `MULTIPLE_PDFS`: keep only the intended PDF attachment for that Zotero item.
- `FILE_TOO_LARGE`: the PDF is larger than Notion accepts for your workspace.
- `AMBIGUOUS_CLEANUP_MATCH`: cleanup found duplicate Notion rows for a live Zotero item and skipped them for review.
- `UNMANAGED_NOTION_ROW`: cleanup found a row without a usable `Zotero URI` and left it untouched.

For harder cases, rerun the same command with `--verbose` and check the detailed log path shown at the top of the terminal output.

## Scope

- Supported: Windows, macOS, Linux
- Supported: personal Zotero libraries
- Not supported in this release: Zotero group libraries

## Contributor Docs

- [Contributing guide](./CONTRIBUTING.md)
- [Release process](./RELEASE_PROCESS.md)
