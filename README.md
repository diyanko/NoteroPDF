# NoteroPDF

*Sync PDFs from Zotero to your Notion database.*

---

NoteroPDF helps you attach the correct PDFs from your Zotero library to the corresponding rows in your Notion database. It is designed to be safe, read-only, and predictable.

**Note:** NoteroPDF works alongside the official [Notero](https://github.com/dvanoni/notero) plugin for Zotero. While Notero syncs metadata (titles, authors, notes), NoteroPDF specifically handles syncing the **PDF files** themselves to your Notion "Files & media" property.

---

## Quick Overview

**What it does:**
- Finds local PDF attachments in your Zotero library
- Matches them to the correct rows in your Notion database
- Uploads and attaches the PDFs to your Notion "PDF" property

**Key Features:**
- **Safe:** Never edits Zotero; runs in read-only mode
- **Precise:** Uses strict matching rules (Notero links, URIs, DOIs) to avoid mistakes
- **Transparent:** Generates detailed CSV/JSON reports after every run
- **Simple:** Command-line interface that is easy to set up

**Who is this for:**
- Users who already use Notero to sync metadata to Notion
- Users whose Notion database rows already exist (created by Notero)
- Users who want to attach the actual PDF files to those Notion rows

---

## Prerequisites

Before you start, make sure you have:

- [ ] Python 3.11+ installed on your computer
- [ ] Zotero installed with your library data
- [ ] Notion account with a database set up (using Notero)
- [ ] Notion Internal Integration Token (secret key)
- [ ] A **Files & media** property named "PDF" (or custom name) in your Notion database
- [ ] macOS (v1 currently supports macOS only)

---

## Installation

### Option 1: Install from Latest Release (Recommended)

This command automatically fetches the latest release version and installs it:

```bash
LATEST_TAG=$(curl -s https://api.github.com/repos/diyanko/NoteroPDF/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/') && \
python -m pip install "https://github.com/diyanko/NoteroPDF/releases/download/${LATEST_TAG}/noteropdf-${LATEST_TAG#v}-py3-none-any.whl"
```

### Option 2: Install from Source (Latest)

This command automatically fetches the latest release version and installs from source:

```bash
LATEST_TAG=$(curl -s https://api.github.com/repos/diyanko/NoteroPDF/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/') && \
python -m pip install "https://github.com/diyanko/NoteroPDF/archive/refs/tags/${LATEST_TAG}.tar.gz"
```

### Verify Installation

Run the help command to ensure it is installed correctly:

```bash
noteropdf --help
```

---

## Notion Database Setup

Before configuring NoteroPDF, you need to ensure your Notion database has a property for storing PDF files.

### Creating a PDF Property in Notion

1. Open your Notion database that syncs with Notero
2. Click the **"+ New property"** button at the end of the property list
3. Set the property details:
   - **Property name:** `PDF` (or whatever you prefer, but update `config.yaml` accordingly)
   - **Property type:** **"Files & media"**
4. Click **"Create"** to save the property

**Note:** The property type must be **"Files & media"** for NoteroPDF to attach PDF files to your database rows.

### Property Name Configuration

In your `config.yaml`, the `pdf_property_name` setting must match the name of your Notion property exactly:

```yaml
notion:
  pdf_property_name: "PDF"  # Must match your Notion property name
```

If you named your property something different (e.g., "PDF Attachments"), update the configuration accordingly.

---

## Configuration

### 1. Set up Environment Variables

Copy the example environment file and add your Notion token:

```bash
cp .env.example .env
```

Edit `.env` and add your Notion token:

```env
NOTION_TOKEN=secret_xxx
```

### 2. Configure Settings

Copy the example config file:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your specific paths and IDs:

```yaml
zotero:
  data_dir: "/Users/your-username/Zotero"
  sqlite_path: "/Users/your-username/Zotero/zotero.sqlite"
  storage_dir: "/Users/your-username/Zotero/storage"

notion:
  token_env: "NOTION_TOKEN"
  database_id: "your-notion-database-id"
  data_source_id: "your-data-source-id"  # Optional but recommended
  pdf_property_name: "PDF"  # Name of your Notion files property
  zotero_uri_property_name: "Zotero URI"

sync:
  dry_run: true  # Start with dry run for safety
  state_db_path: ".sync-state.sqlite3"
```

**Important:** Keep `notion_version: "2026-03-11"` as is.

---

## Usage

### Step-by-Step Guide

**Step 1: Verify Setup**

Run the doctor command to check everything is configured correctly:

```bash
python -m noteropdf doctor
```

This checks:
- Paths are valid
- Notion access works
- Target field exists and is the correct type

**Step 2: Preview Changes (Dry Run)**

Set `dry_run: true` in `config.yaml` (default) and run:

```bash
python -m noteropdf sync
```

This simulates the sync without actually uploading files. Review the report in `reports/`.

**Step 3: Run Real Sync**

If everything looks correct, set `dry_run: false` in `config.yaml` and run:

```bash
python -m noteropdf sync
```

### Main Commands

| Command | Description |
|---------|-------------|
| `python -m noteropdf doctor` | Verify setup and access |
| `python -m noteropdf sync` | Normal sync (uploads only needed files) |
| `python -m noteropdf sync --force` | Force re-upload everything |
| `python -m noteropdf rebuild-page-files --yes` | Clear PDF values and rebuild from local files |
| `python -m noteropdf full-reset --yes` | Clear PDF values and local sync state (destructive) |

---

## Understanding Results

### Status Codes

| Status | Meaning |
|--------|---------|
| `OK` | Upload and attach succeeded |
| `UNCHANGED` | Already up to date, no upload needed |
| `NO_PDF` | No valid PDF found |
| `MULTIPLE_PDFS` | More than one valid PDF found |
| `NO_NOTION_MATCH` | No exact Notion match found |
| `MULTIPLE_NOTION_MATCHES` | More than one Notion row matched |
| `FILE_TOO_LARGE` | File exceeds configured upload limit |
| `UPLOAD_FAILED` / `ATTACH_FAILED` | Notion upload/attach issue |

### Reports

After each run, check:
- **Logs:** `logs/` directory
- **Reports:** `reports/` directory (JSON, CSV, summary)

Quick interpretation:
- Mostly `OK` and `UNCHANGED` means your sync is healthy
- Any `NO_*` status means the item was skipped for safety and needs review
- `UPLOAD_FAILED`, `ATTACH_FAILED`, or `NOTION_*` statuses indicate a Notion/network issue

---

## Safety Promise

**What this app can change:**
- One Notion files field (default: `PDF`) in matched rows

**What this app cannot change:**
- Zotero database content
- Zotero files on disk
- Other Notion fields
- Other Notion databases

**Security Notes:**
- Keep `.env` private (never commit it)
- Keep `config.yaml` machine-specific
- Review reports before rerunning large operations
- Close Zotero during sync for best consistency

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `NO_NOTION_MATCH` | Check Notero link, Zotero URI property, or DOI mapping |
| `MULTIPLE_NOTION_MATCHES` | Make Notion mapping unique |
| `NO_PDF` / `MULTIPLE_PDFS` | Check Zotero item attachments |
| `FILE_TOO_LARGE` | Adjust `max_simple_upload_mb` in config |
| `NOTION_AUTH_ERROR` | Check token and integration permissions |
| `NOTION_SCHEMA_ERROR` | Verify database/property IDs in config, ensure PDF property exists and is "Files & media" type |

---

## Official Notero Plugin

This tool works alongside the official **Notero** plugin for Zotero:
- **GitHub:** [dvanoni/notero](https://github.com/dvanoni/notero)
- **Website:** [notero.vanoni.dev](https://notero.vanoni.dev)

Install the Notero plugin first to sync metadata, then use NoteroPDF to sync PDF files.

---

## For Contributors

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

---

## License

This project is licensed under the MIT License. See `LICENSE` for details.

## Contact

Maintainer: GitHub `@diyanko`

If you need help or want to report a bug, open an issue in this repository.
For sensitive security reports, open a minimal issue requesting a private contact channel.
