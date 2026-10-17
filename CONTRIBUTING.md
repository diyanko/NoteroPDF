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

## Branches, worktrees, and pull requests

Work goes directly to `main` by default. Create branches and pull requests only
when the user requests them; naming conventions do not authorize commits,
pushes, merges, or releases.

For requested branch work, use `feature/`, `fix/`, `docs/`, `refactor/`, or
`test/` followed by a short, lowercase, hyphen-separated description, such as
`feature/named-connections`. Match the worktree directory name to the branch
suffix (`named-connections` in this example). Keep names clear and free of
agent, vendor, or execution-environment branding.

Keep each pull request focused on one cohesive change. Use a plain imperative
action title, such as “Add named connections,” and describe the concrete
problem, resulting behavior, validation, and remaining limitations. State any
dependencies explicitly. When stacking pull requests, target the prerequisite
branch and link its pull request so the review shows only the dependent change.
Do not merge or release without the user's explicit authorization.

Report security issues privately through the process in [SECURITY.md](./SECURITY.md); do not open a public issue with vulnerability details.
