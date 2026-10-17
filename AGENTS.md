# NoteroPDF agent instructions

- Use `README.md` for product behavior, `CONTRIBUTING.md` for code and safety
  invariants, and `RELEASING.md` only for maintainer or release work. Do not
  duplicate those documents here.
- This is a solo-maintained, direct-to-`main` repository. Do not create branches
  or pull requests unless the user explicitly requests them. An explicit request
  for concurrent development authorizes a separate worktree and branch for each
  independent change; it does not authorize a pull request.
- Before editing, inspect status and worktrees. Use one writer per checkout.
  Preserve existing changes and use `RELEASING.md` for isolation and integration.
- Do not commit or push unless explicitly requested. If a requested push is not
  safely based on `main`, report that instead of rewriting history or moving
  existing changes.
- Preserve all user work. Never reset, restore, delete, reformat, stage, or
  commit unrelated changes.
- Keep changes focused, simple, deterministic, and fail-closed. Update relevant
  tests and user documentation when behavior changes.
- Use `ROADMAP.md` for product direction. Prioritize easy setup, the published
  CLI, and small changes; do not introduce a GUI framework or new source system
  merely to prepare for future work.
- Write public-facing commit subjects, branch names, pull requests, and release
  notes in plain professional language. Describe behavior and validation; omit
  chat history, agent branding, private data, and unsupported claims.
- Run targeted tests for focused changes. Run the full checks in `RELEASING.md`
  for cross-cutting, dependency, packaging, workflow, or release changes. For
  documentation-only changes, use proportionate text and link checks.
- Never write to Zotero or weaken protection for unknown, ambiguous, or
  concurrently changed Notion data.
- Never expose credentials, personal paths, titles, page IDs, or raw diagnostic
  data in logs or shared output. Interactive attachment listings may show sanitized
  basenames only when both input and output are terminals; never show full paths.
- Never bump a release version, create or push a tag, dispatch a publishing
  workflow, or publish to PyPI or GitHub Releases unless the user explicitly
  requests that exact action. Permission to implement, commit, or push is not
  release authority.
