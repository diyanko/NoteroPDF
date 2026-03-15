# NoteroPDF

NoteroPDF helps you put the right PDF from your Zotero library into the right row in your Notion database.

It is designed to be safe and predictable:
- It never edits Zotero.
- It reads Zotero in read-only mode.
- It only writes to one Notion property (default: `PDF`).
- If matching is unclear, it skips and reports the reason.
- It syncs only your personal Zotero library (group libraries are ignored in v1).
- It supports macOS in this public v1 release.

## Safety promise

What this app can change:
- One Notion files field (default: `PDF`) in matched rows.

What this app cannot change:
- Zotero database content
- Zotero files on disk
- Other Notion fields
- Other Notion databases

If matching is unclear, it skips the item and tells you why.

## Who this is for

Use this if:
- You already use Notero.
- Your Notion rows already exist.
- You want a simple, reliable CLI sync now, and maybe a GUI later.

## What it does

For each Zotero parent item:
1. Finds exactly one local PDF attachment.
2. Finds the matching Notion row using strict rules.
3. Uploads and attaches the PDF to your Notion `PDF` property.
4. Writes clear logs and reports.

If anything is ambiguous (no PDF, multiple PDFs, multiple Notion matches), it skips that item and records why.

## Matching rules (strict)

Order used:
1. Notero page link on the Zotero item.
2. Exact match on Notion `Zotero URI` property.
3. Exact match on Notion `DOI` property (if present).
4. Otherwise skipped as no confident match.

No fuzzy title matching is used.

## Before you start

You need:
- Python 3.11+
- A Notion internal integration token
- Access to your target Notion database/data source
- Local Zotero data directory
- macOS

## Quick setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Create env file:

```bash
cp .env.example .env
```

3. Put your Notion token in `.env`:

```env
NOTION_TOKEN=secret_xxx
```

4. Create config:

```bash
cp config.example.yaml config.yaml
```

5. Edit `config.yaml`:
- Set your Zotero paths.
- Set your Notion database/data source IDs.
- Keep `notion_version: "2026-03-11"`.
- Keep `pdf_property_name` and `zotero_uri_property_name` as different fields.

## First run (safe path)

1. Check setup first:

```bash
python -m noteropdf doctor
```

`doctor` confirms:
- Paths are valid
- Notion access works
- Target field exists and is the correct type
- Zotero is accessed in immutable read-only mode

2. Preview changes without writing to Notion:

Set this in `config.yaml`:

```yaml
sync:
  dry_run: true
```

```bash
python -m noteropdf sync
```

3. If the report looks correct, run real sync:

Set `dry_run: false` in `config.yaml`, then run:

```bash
python -m noteropdf sync
```

By default, the app stops on the first error so problems are easy to see and fix.

Before each write command, the CLI prints a preflight summary:
- what command is running
- which Notion property may be changed
- how many items/pages are in scope
- whether dry-run is enabled

## Main commands

Check setup and access:

```bash
python -m noteropdf doctor
```

Run normal sync (uploads only needed files):

```bash
python -m noteropdf sync
```

Force re-upload everything (use only if needed):

```bash
python -m noteropdf sync --force
```

Clear known `PDF` values and rebuild from local files:

```bash
python -m noteropdf rebuild-page-files --yes
```

Clear known `PDF` values and local sync state (destructive):

```bash
python -m noteropdf full-reset --yes
```

For both destructive commands, you must also type a second confirmation in the terminal.

## Read results quickly

After each run:
- Logs go to `logs/`
- Reports go to `reports/`

Each run creates:
- Detailed JSON report
- CSV report (easy to open in spreadsheet tools)
- Summary JSON with status counts and top failure reasons

Quick read of results:
- Mostly `OK` and `UNCHANGED` means your sync is healthy.
- Any `NO_*` status means the item was skipped for safety and needs review.
- `UPLOAD_FAILED`, `ATTACH_FAILED`, or `NOTION_*` statuses mean a Notion/network issue happened.

For most users: run `doctor`, then one dry run, then real sync.

## Common statuses

- `OK`: upload and attach succeeded
- `UNCHANGED`: already up to date, no upload needed
- `NO_PDF`: no valid PDF found
- `MULTIPLE_PDFS`: more than one valid PDF found
- `NO_NOTION_MATCH`: no exact Notion match found
- `MULTIPLE_NOTION_MATCHES`: more than one Notion row matched
- `FILE_TOO_LARGE`: file exceeds configured upload limit
- `UPLOAD_FAILED` or `ATTACH_FAILED`: Notion upload/attach issue
- `STATE_SAVE_FAILED`: file attached in Notion, but local state save failed (later rerun may re-upload once)
- `NOTION_NETWORK_ERROR`: network problem while talking to Notion

## Safety notes

- Keep `.env` private (never commit it).
- Keep `config.yaml` machine-specific.
- Review reports before rerunning large operations.
- Close Zotero during sync for best consistency.
- Avoid `rebuild-page-files` and `full-reset` unless you understand what they change.

## Troubleshooting

- `NO_NOTION_MATCH`: The Zotero item could not be matched to a single Notion row. Check Notero link, Zotero URI property, or DOI mapping.
- `MULTIPLE_NOTION_MATCHES`: More than one Notion row matched. Make the Notion mapping unique.
- `NO_PDF` or `MULTIPLE_PDFS`: The Zotero item has no single valid PDF attachment.
- `FILE_TOO_LARGE`: File is above your configured upload size limits in `config.yaml`.
- `NOTION_AUTH_ERROR`: Token or integration permissions are wrong.
- `NOTION_SCHEMA_ERROR`: Database/property IDs or property types do not match your config.

## For contributors

Run tests:

```bash
python -m pytest -q
```

Package entrypoint:

```bash
noteropdf
```

CI runs:
- tests (`pytest`)
- package build smoke check (`python -m build`)

## License

This project is licensed under the MIT License.
See `LICENSE` for details.

## Contact

Maintainer: GitHub `@diyanko`

If you need help or want to report a bug, open an issue in this repository.
For sensitive security reports, open a minimal issue requesting a private contact channel.
