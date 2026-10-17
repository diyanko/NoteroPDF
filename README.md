# NoteroPDF

[PyPI](https://pypi.org/project/noteropdf/) | [Releases](https://github.com/diyanko/NoteroPDF/releases) | [Issues](https://github.com/diyanko/NoteroPDF/issues)

NoteroPDF puts the PDFs in your personal or selected group Zotero library onto the matching Notion pages created by [Notero](https://github.com/dvanoni/notero).

NoteroPDF is a terminal application. You install it once, then run one command whenever you want to sync.

It is designed around one workflow:

```text
install → open Zotero → run → paste a Notion token → choose database → preview → confirm
```

No configuration file or hosted service is required.

## Before You Start

You need:

- Zotero with a personal or group library and local PDF attachments
- Notero already syncing that library to a Notion database
- full membership in the Notion workspace, with permission to create a personal access token

Leave Zotero open and enable **Settings → Advanced → Allow other applications on
this computer to communicate with Zotero**. NoteroPDF makes only read requests
to Zotero's local API and never changes the Zotero library.

Group libraries must be synchronized in Zotero Desktop, and their PDFs must
already be downloaded to your computer.

## Install

### From PyPI (recommended)

NoteroPDF requires Python 3.10 or newer and is tested through Python 3.14. Install it as an isolated command-line application with [pipx](https://pipx.pypa.io/):

```bash
pipx install noteropdf
noteropdf --version
```

If `pipx` is not available, use pip:

```bash
python -m pip install noteropdf
```

To update, run `pipx upgrade noteropdf`. If the command is not found, restart the
terminal or run `python -m noteropdf` (`py -m noteropdf` on Windows).

## Use

Run one command:

```bash
noteropdf
```

On the first run, NoteroPDF:

1. Connects to Zotero and lets you choose your personal library or a locally available group library.
2. Opens Notion's personal access token page in the browser.
3. Asks you to create a token with the Notion API capability and paste it once. The token input is hidden.
4. Saves the token in your operating system's credential store.
5. Lets you choose the database managed by Notero.
6. Finds a dedicated `NoteroPDF PDF` files property, or asks before creating it.
7. Matches items through their exact Notero-created links and shows what will be uploaded.
8. Uploads PDFs only after one confirmation.

The preview identifies the source library type and the connected Notion PDF
field, counts uploads and unchanged items, and explains why items need attention.
It shows up to five upload and skipped item keys by default. Run
`noteropdf sync --details` to see every item key and outcome. Item titles, local
file paths, and Notion page addresses stay out of this summary. Technical
diagnostics remain available with `--verbose` and in the private local log.

Later runs go directly to the preview. If no interactive terminal is available, sync remains preview-only unless `--apply` is supplied and exits nonzero when uploads are pending.

The selected library is saved with your Notion connection. Each run reads only
that library. To switch libraries, run `noteropdf connect`, choose the library,
and select its Notero database. Existing connections continue using the personal
library until you change them. Sync history stays separate for each group, and
an unavailable group stops the run instead of falling back to another library.

To keep several connections, give each one a name:

```bash
noteropdf connect --profile lab
noteropdf sync --profile lab
noteropdf doctor --profile lab
```

Each named connection remembers its own Zotero library, Notion database, PDF
property, token, and sync history. Tokens stay in separate accounts in your
operating system's credential store. You can use a different Notion workspace
for each connection. Every command runs only the connection you selected.

Leaving out `--profile` (or using `--profile default`) keeps your existing
connection, token, and history. New names require `connect` first; a misspelled
name stops sync instead of using another connection. Names use 1–32 lowercase
letters, digits, or hyphens, start with a letter, and end with a letter or digit.

## Commands

| Command | Purpose |
| --- | --- |
| `noteropdf` | Preview, confirm, and sync. |
| `noteropdf sync` | The same complete sync workflow. |
| `noteropdf sync --details` | Show every item key and its outcome, including unchanged items. |
| `noteropdf sync --apply` | Preview and apply without a prompt; useful for automation. |
| `noteropdf connect` | Choose a Zotero library, reconnect Notion, or choose a different database. |
| `noteropdf pdf ITEM` | List available PDF attachment keys for an exact item key. |
| `noteropdf pdf ITEM ATTACHMENT` | Remember the exact PDF to use for this item in the selected library. |
| `noteropdf pdf ITEM --clear` | Forget a saved PDF choice. |
| `noteropdf doctor` | Check Zotero, Notion, credentials, schema, and local storage. |
| `noteropdf --version` | Show the installed version. |

Add `--verbose` for technical details or `--no-color` for plain terminal output.
Add `--profile NAME` to any command to use a named connection.

To try a smaller batch, select collections or individual parent items from the
connected Zotero library:

```bash
noteropdf sync --collection-key ABCD1234
noteropdf sync --item-key EFGH5678 --item-key IJKL9012
noteropdf sync --collection-key ABCD1234 --item-key EFGH5678 --apply
```

Keys are the eight uppercase letters or digits in Zotero item and collection
links, such as `zotero://select/library/items/EFGH5678`. Use the parent paper's
key, not a PDF attachment's key. Repeat a flag to include more collections or
items. Combining both flags selects only the requested items directly inside
the requested collections. Subcollections are not included automatically.
Filters apply to this run only; omitting them syncs the whole connected library.

A missing item or a collection with no readable parent items stops the run;
check its key and the selected library. Empty collections also stop the run.
The preview and confirmed upload use the same filters. Safety checks still
cover the whole library: an item outside your selection sharing a Notion target
blocks that target too.

When an item has several PDFs, sync skips it until you explicitly choose one.
Find the parent item and attachment keys in Zotero's item links, then run
`noteropdf pdf ITEM` to see available local PDF attachment keys. Save your choice
with `noteropdf pdf ITEM ATTACHMENT`, then run sync to review it. Choices stay
separate for personal and group libraries, and the selection command only changes
local preferences. Add `--profile lab` to inspect or save choices in a named
connection. It never uploads or changes Zotero or Notion. If the chosen
attachment disappears, moves, or stops being a local PDF, sync skips the item
until you choose again or clear the choice. Editing the contents of the chosen
PDF follows the normal preview and confirmation workflow.

## Safe by Design

- The preview never writes to Notion.
- Confirmation applies only the exact file/page actions in that preview.
- A changed PDF or missing target page is skipped and must be previewed again.
- A Notion PDF field changed after preview is skipped instead of overwritten.
- Multiple or unknown files in the destination field are never removed.
- Ambiguous PDF or Notion matches are skipped instead of guessed.
- Library changes during scanning stop the run before uploads begin.
- Uploads are serial, retry transient Notion errors, and respect `Retry-After` and workspace size limits.
- Zotero data and PDFs go directly from your computer to Notion. NoteroPDF has no hosted service.
- In normal setup, the Notion token is stored only in Keychain, Credential Manager, Secret Service, or KWallet. Setup stops with a clear error if the operating system's credential store is unavailable.
- A small rotating diagnostic log is retained (the current file plus up to three backups).

Matching is deliberately narrow: an item must have exactly one Notero-created
link attachment named `Notion`, and the linked page must be accessible in the
selected database. NoteroPDF does not fall back to Zotero URI, DOI, title, or
fuzzy matching. Unrelated Notion bookmarks are ignored. Notion property IDs are
stored locally, so renaming a connected column does not break sync.

## Troubleshooting

Start with:

```bash
noteropdf doctor
```

Then try `noteropdf connect` if the token expired or was revoked, the selected database changed, or a required property was removed. Choose not to reuse the saved token when you need to paste a replacement. Common skips such as no PDF, multiple PDFs, a missing Notero link, or an oversized file are explained in the terminal without changing either library.

If `doctor` reports that the running Zotero library cannot be read, confirm that
Zotero is open and its local API setting under **Settings → Advanced** is enabled.

To repair a pipx installation, run `pipx reinstall noteropdf`. For pip, use
`python -m pip install --upgrade --force-reinstall noteropdf`.

Notion personal access tokens act with your existing page permissions and can be created only by eligible full workspace members. Choose the Notion API capability, treat the token like a password, and replace it before its selected expiration date (at most one year). See Notion's [personal access token guide](https://developers.notion.com/guides/get-started/personal-access-tokens).

The diagnostic log is stored in the operating system's normal per-user log folder for NoteroPDF. It can include Zotero titles, item keys, and Notion page IDs needed for troubleshooting; review it before sharing.

## Development

See [Contributing](https://github.com/diyanko/NoteroPDF/blob/main/CONTRIBUTING.md)
for project invariants and
[Maintaining and Releasing](https://github.com/diyanko/NoteroPDF/blob/main/RELEASING.md)
for the complete local-to-release workflow. Report vulnerabilities through the
[Security policy](https://github.com/diyanko/NoteroPDF/blob/main/SECURITY.md).
