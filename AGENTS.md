# NoteroPDF agent instructions

- Use `README.md` for product behavior, `CONTRIBUTING.md` for code and safety
  invariants, and `RELEASING.md` only for maintainer or release work. Do not
  duplicate those documents here.
- This is a solo-maintained, direct-to-`main` repository. Do not create branches
  or pull requests unless the user explicitly requests them.
- Do not commit or push unless explicitly requested. If a requested push is not
  safely based on `main`, report that instead of rewriting history or moving
  existing changes.
- Preserve all user work. Never reset, restore, delete, reformat, stage, or
  commit unrelated changes.
- Keep changes focused, simple, deterministic, and fail-closed. Update relevant
  tests and user documentation when behavior changes.
- Run targeted tests for focused changes. Run the full checks in `RELEASING.md`
  for cross-cutting, dependency, packaging, workflow, or release changes. For
  documentation-only changes, use proportionate text and link checks.
- Never write to Zotero or weaken protection for unknown, ambiguous, or
  concurrently changed Notion data.
- Never expose credentials, personal paths, titles, page IDs, or diagnostic
  logs.
- Never bump a release version, create or push a tag, dispatch a publishing
  workflow, or publish to PyPI or GitHub Releases unless the user explicitly
  requests that exact action. Permission to implement, commit, or push is not
  release authority.
