# NoteroPDF

[PyPI](https://pypi.org/project/noteropdf/) | [Releases](https://github.com/diyanko/NoteroPDF/releases) | [Issues](https://github.com/diyanko/NoteroPDF/issues)

NoteroPDF adds your local Zotero PDF files to the matching pages in a Notion database managed by [Notero](https://github.com/dvanoni/notero).

Use it if you already sync Zotero items to Notion with Notero and want the actual PDF files to appear in a Notion files property, usually named `PDF`.

NoteroPDF is preview-first. It reads from Zotero, shows what would change, and only updates Notion after you turn preview mode off.

## What You Need

- Zotero installed on your computer
- a local personal Zotero library
- Notero already syncing Zotero items to a Notion database
- Python 3.10, 3.11, 3.12, or 3.13 for the PyPI install
- a Notion integration token
- your Notion database shared with that integration
- a Notion files property for PDFs, usually named `PDF`

Supports Windows, macOS, and Linux. Zotero group libraries are not supported yet.

## Quick Start

1. Install NoteroPDF:

```bash
python -m pip install noteropdf
```

2. Run the guided setup:

```bash
noteropdf setup
```

3. Check that Zotero and Notion are connected:

```bash
noteropdf doctor
```

4. Preview the first sync:

```bash
noteropdf sync
```

5. If the preview looks right, open `config.yaml` and change:

```yaml
dry_run: true
```

to:

```yaml
dry_run: false
```

6. Run the real sync:

```bash
noteropdf sync
```

After that, run `noteropdf sync` whenever you want to update Notion with PDFs from Zotero.

## Useful Commands

| Command | What it does |
| --- | --- |
| `noteropdf setup` | Creates `config.yaml` with guided prompts. |
| `noteropdf doctor` | Checks Zotero, Notion, config, logs, and reports. |
| `noteropdf sync` | Uploads missing or changed PDFs. Respects `dry_run` in `config.yaml`. |
| `noteropdf sync --force` | Re-uploads PDFs even when they appear unchanged. |
| `noteropdf cleanup` | Previews stale or duplicate Notion rows. |
| `noteropdf cleanup --apply` | Moves strongly classified cleanup rows to Notion trash after confirmation. |

## What Changes

- `sync` only updates the configured Notion files property, usually `PDF`.
- `cleanup --apply` only moves strongly classified stale or duplicate rows to Notion trash.
- Unclear matches are skipped for manual review.
- Zotero is never modified.

## Common Problems

| Message | What to check |
| --- | --- |
| `NOTION_AUTH_ERROR` | Check your Notion token and make sure the database is shared with the integration. |
| `NOTION_SCHEMA_ERROR` | Check the Notion database properties, especially the `PDF` files property. |
| `NO_NOTION_MATCH` | The Zotero item could not be matched to a Notion row. |
| `MULTIPLE_NOTION_MATCHES` | More than one Notion row matched the same Zotero item. |
| `NO_PDF` | The Zotero item does not have a PDF attachment. |
| `MULTIPLE_PDFS` | The Zotero item has more than one PDF attachment. |
| `FILE_TOO_LARGE` | The PDF is larger than Notion accepts for your workspace. |

For more detail, rerun the command with `--verbose`:

```bash
noteropdf --verbose sync
```

NoteroPDF also writes logs and reports after each run. The terminal output shows where those files are saved.

## Install Options

### Upgrade from PyPI

```bash
python -m pip install --upgrade noteropdf
```

### App Bundle

If you do not want to install with Python, download a bundle from the [releases page](https://github.com/diyanko/NoteroPDF/releases), extract it, and run the included `noteropdf` program.

On macOS, if the downloaded app is blocked, this may help:

```bash
xattr -dr com.apple.quarantine /path/to/noteropdf
```

### Source Install

Use this if you want to install from a local checkout.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
python -m pip install -U pip
python -m pip install .
python -m noteropdf setup
```

## Details

### Matching

NoteroPDF matches Zotero items to Notion rows in this order:

1. Notero page link
2. Zotero URI
3. DOI

An item must have exactly one usable PDF attachment. If the match or PDF choice is unclear, the item is skipped.

### Uploads and Reports

- Files up to 20 MB use Notion's normal upload flow.
- Larger files use Notion multi-part upload when the workspace supports it.
- Files over the workspace upload limit are skipped.
- Each run writes a log, JSON report, CSV report, and summary JSON file.

## Development

Most users do not need this section.

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Additional project docs:

- [Contributing guide](./CONTRIBUTING.md)
- [Release process](./RELEASE_PROCESS.md)
