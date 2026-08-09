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

The main boundaries are intentionally direct: `cli.py` guides the user, `auth.py` and `settings.py` manage the local PAT connection, `sync_engine.py` coordinates work, `notion_client.py` handles Notion, `zotero_repo.py` reads Zotero, and `state_store.py` records local state.

Use [RELEASING.md](./RELEASING.md) as the single source of truth for local
setup, validation, commits, and releases. Use proportionate checks while
developing, then run its complete pre-push checks for cross-cutting changes.

Report security issues privately through the process in [SECURITY.md](./SECURITY.md); do not open a public issue with vulnerability details.
