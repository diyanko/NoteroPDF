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

### Source Install

Use this path on macOS if the bundle is blocked by system security or permission prompts.

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

1. Run `noteropdf setup`.
2. Run `noteropdf doctor`.
3. Run `noteropdf sync` with `dry_run: true` first.
4. If the preview looks correct, set `dry_run: false` and run `noteropdf sync` again.

The setup flow tries to detect your Zotero data folder, discover accessible Notion targets, and write `config.yaml` for you. If discovery cannot find a target, it asks for a Notion database URL/ID or data source URL/ID and continues.

## What It Does

- Matching order is fixed: Notero page link, Zotero URI, DOI
- A Zotero item must have exactly one usable PDF attachment
- Files over Notion's upload limit are skipped with a clear error
- Files up to 20 MB use single-part upload
- Larger files use Notion multi-part upload when the workspace supports it
- Zotero data is never modified

## Results

Each sync writes local artifacts under standard OS app directories managed by `platformdirs`:

- run logs
- JSON report
- CSV report
- summary JSON

## Troubleshooting

- `NOTION_AUTH_ERROR`: verify token and database sharing
- `NOTION_SCHEMA_ERROR`: confirm the selected database contains the configured files property
- `NO_NOTION_MATCH`: check Notero page link, `Zotero URI`, or DOI
- `MULTIPLE_NOTION_MATCHES`: make Notion matching data unique
- `NO_PDF` or `MULTIPLE_PDFS`: correct Zotero attachments for the item
- `FILE_TOO_LARGE`: PDF exceeds the current Notion workspace upload limit

## Scope

- Supported: Windows, macOS, Linux
- Supported: personal Zotero libraries
- Not supported in this release: Zotero group libraries

## Contributor Docs

- [Contributing guide](./CONTRIBUTING.md)
- [Release process](./RELEASE_PROCESS.md)
