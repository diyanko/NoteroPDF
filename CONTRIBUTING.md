# Contributing to NoteroPDF

Keep every change simple, deterministic, safe, and understandable to a non-technical user.

## Expectations

- Keep changes focused, with tests and documentation for changed behavior.
- Match only through a unique Notero-created link attachment; never add URI,
  DOI, title, or fuzzy fallbacks.
- Read Zotero only through its enabled local API and never write to Zotero.
- Keep the Notion token in the operating system credential store; do not add
  file or environment-variable credential paths.
- Never replace unknown, ambiguous, or concurrently changed Notion files.
- Prefer actionable errors and a smaller dependency surface.
- Never commit credentials, personal data, diagnostic logs, or generated output.
- Keep diagnostic logs and redirected or noninteractive summaries free of research
  titles, filenames, personal paths, page IDs, and raw API or exception payloads.
  Interactive attachment listings may show sanitized basenames when both input
  and output are terminals; never show directory components or terminal controls.
  Test redirection as well as ordinary local use.

The main boundaries are intentionally direct: `cli.py` guides the user, `auth.py` and `settings.py` manage the local PAT connection, `sync_engine.py` coordinates work, `notion_client.py` handles Notion, `zotero_repo.py` reads Zotero, and `state_store.py` records local state.

Keep prompts, terminal output, and process exit handling in the CLI. Keep sync
decisions and safety checks callable without terminal interaction. A future GUI
should reuse those operations and explicit preview approval. Add a boundary when
a concrete feature needs it; avoid a plugin framework or parallel implementation.

The Notero matching rule above applies to the existing Zotero workflow. A future
direct-file workflow needs its own explicit destination selection and ownership
rules; it must not relax matching or overwrite protections for existing users.

## Start contributing

See [the roadmap](./ROADMAP.md) for direction. Use a GitHub issue for a substantial
feature or bug that needs discussion; small fixes do not need a planning ceremony.
Describe the user problem, a small useful outcome, and how to verify it. Work on
one cohesive change at a time. External contributions use focused pull requests.

For an initial development environment:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[test,qa,build]"
python scripts/check.py
```

Use focused tests such as `python -m pytest -q tests/test_cli.py` during development. The shared
check command validates source without formatting it. Add `--package` to build
and validate a fresh wheel and source archive. Checks never run a live sync.

## Integration and dependency changes

Monthly Dependabot pull requests group Python and GitHub Actions updates. Review
them in small batches and run the full checks before integration; do not merge
automatically or widen major-version bounds just to accept an update.

For changes to Notion, Zotero, or Notero behavior, verify current upstream
documentation and relevant release notes. Prefer supported public interfaces,
keep API versions explicit, and test failures as well as successful responses.
Use [Notion's API documentation](https://developers.notion.com/),
[Zotero's local API documentation](https://www.zotero.org/support/dev/web_api/v3/local_api),
and [Notero's repository](https://github.com/dvanoni/notero) as starting points.
Add a dependency only when it solves a concrete problem better than the existing
code or standard library. Preserve the supported Python and OS versions unless
an intentional compatibility change is documented.

Include installation, connection, preview, cancellation, and repeat-run behavior
when a change affects onboarding or sync. Use synthetic fixtures in tests and
public examples; never ask contributors to post tokens or unreviewed logs.

For notes, preserve source references and distinguish imported content from the
user's own writing. Establish ownership, duplicate detection, and edit-conflict
behavior before adding writes. Check what Notero already synchronizes and avoid
two tools managing the same content. Evaluate new API, Markdown, CLI, or MCP
interfaces against a concrete user need; keep ordinary sync deterministic and
usable without an AI service or an additional CLI installation.

Use [RELEASING.md](./RELEASING.md) as the single source of truth for local
setup, validation, commits, and releases. Use proportionate checks while
developing, then run its complete pre-push checks for cross-cutting changes.

Report security issues privately through the process in [SECURITY.md](./SECURITY.md); do not open a public issue with vulnerability details.
