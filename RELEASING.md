# Maintaining and Releasing NoteroPDF

This is the single maintainer workflow. Normal work goes directly to `main`;
isolate concurrent work only when requested. Implement and test by default;
commit, push, integrate branches, or release only when explicitly requested.

## Everyday workflow

Describe the user outcome and any top-level constraints. The implementation
should handle routine design, tests, documentation, and review without requiring
the maintainer to choose Git commands or internal details.

1. Inspect Git status, worktrees, and existing work before editing.
2. Make one focused change and run relevant tests while developing.
3. Run the shared checks below, review the diff, and report the result and any
   remaining limitations. Include setup and user documentation when affected.
4. When requested, commit the intended change and push `main`; wait for CI on
   that exact commit. Publishing remains a separate request.

Use an issue for a substantial idea or unresolved bug, rather than a branch for
an idea that is not being implemented. Keep only a small number of active changes.

## Concurrent work and worktrees

Use one writer per checkout. A request for concurrent development authorizes
isolated worktrees and branches; a pull request is optional and separately
requested. Use durable sibling directories rather than temporary directories.
For example, after fetching and verifying the intended base:

```bash
git fetch origin
git worktree add -b fix/connection-recovery ../NoteroPDF-worktrees/connection-recovery origin/main
```

Prefer independent changes based on current `main`. Use short branch prefixes
`feature/`, `fix/`, `docs/`, `refactor/`, or `test/`, with a lowercase,
hyphen-separated description. Match the worktree basename to that description.
Set up a separate `.venv` inside each worktree; do not reuse another checkout's
editable installation. Run checks through that environment's Python.

If a prerequisite is genuinely needed, finish it first where practical. For an
existing stack, record the prerequisite and review only the dependent change.
When a PR is requested, target its prerequisite branch and link that dependency.
Integrate prerequisites before dependents, preserving ancestry until the stack
is cleared; do not squash a prerequisite and then blindly retarget its children.
Independent finished PRs can use squash merge with a reviewed public subject.

Integration requires a clean destination checkout and a review of the combined
result. Never move unfinished `main` changes into another task, automatically
stash them, or rewrite published branches to make integration easier. Re-run
the full checks after integration, including interaction tests for overlapping
features. Preserve local-only commits until their changes are accounted for.

After integration is requested and complete, remove only a clean, unused
worktree with `git worktree remove`, then delete only a branch whose work has
been verified as integrated. Do not force removal or branch deletion. Audit
missing worktrees before pruning their administrative records; a missing path
does not prove the branch's work is disposable. Managed Codex worktrees should
be archived through Codex so its recovery snapshot is retained.

See [Git's worktree documentation](https://git-scm.com/docs/git-worktree) for
removal, repair, and pruning semantics.

## One-time setup

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[test,qa,build]"
```

## Develop and validate

Run focused tests while editing:

```bash
python -m pytest -q tests/test_cli.py
```

Choose the test module for the area being changed.

Before every push that changes code, dependencies, packaging, or workflows, run:

```bash
python scripts/check.py
```

For workflow or packaging changes and release candidates, include package checks:

```bash
python scripts/check.py --package
```

The command checks staged and unstaged whitespace, Ruff lint and formatting,
mypy, and the full test suite with a 75% coverage floor. It never formats source,
stages files, commits, or calls live Zotero or Notion services. `--package` also
builds and validates exactly one wheel and source archive in a temporary directory.
Build tooling may download isolated build dependencies.

CI runs the same command with `--package` on Python 3.14 and runs tests on every
supported Python version plus macOS and Windows. Documentation-only edits need
proportionate text and link checks. If a check fails in pre-existing work, report
that separately; do not fix or reformat another task's changes without scope.

## Real Zotero and Notion check

When setup, authentication, Zotero reading, matching, uploads, or sync safety
changes, test the affected journey against a controlled real workspace:

Keep Zotero open with its local API enabled. Use an item with exactly one PDF
and one Notero-created link attachment named `Notion`; do not substitute a URI,
DOI, title, or manual page mapping.

```bash
python -m noteropdf doctor
python -m noteropdf sync
```

Review the preview and cancel once; verify Notion did not change. Confirm that a
non-interactive preview exits `2` when uploads are pending and makes no changes:

```bash
set +e
python -m noteropdf sync </dev/null
preview_exit=$?
set -e
test "$preview_exit" -eq 2
```

Approve a real upload only when the preview contains exactly the intended test
item. Never approve a larger production batch merely to complete a smoke test.
Verify the uploaded PDF opens, then rerun sync and confirm the item is unchanged.
Retest first-time connection or token replacement only when that code changed,
and confirm the PAT is stored by the operating system credential backend.

## Commit and push

Only commit or push when requested. Review and stage only intended files;
replace the example path below with the files belonging to the change:

```bash
git status --short
git add -- path/to/intended-file
git diff --cached --check
git diff --cached
git commit -m "Concise description"
git push origin main
```

Wait for the `CI` workflow on that exact commit. Fix any failure before tagging.

Keep each commit cohesive and write a concise, imperative subject describing
the resulting behavior, such as `Preserve connection selection during recovery`.
Use a body only when it explains a meaningful tradeoff. Commit messages, branch
names, PR descriptions, and release notes are public: omit chat transcripts,
agent branding, private identifiers, logs, and claims unsupported by validation.
Do not rewrite existing public history merely to normalize its writing style.

For a requested PR, explain the problem, behavior, validation, compatibility, and
any dependency. A PR is not required for ordinary solo work on `main`.

## Publish a release

Publishing is a separate, explicit action. Update the version in `pyproject.toml`
and `CHANGELOG.md` together, commit and push them, complete the checks above, and
wait for CI. Then tag that already-tested commit:

Use Python 3.11 or newer for the `tomllib` command below. The subshell stops on
any failed check before creating or pushing a tag.

```bash
(
  set -eu
  release_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
  test -z "$(git status --porcelain)"
  test "$(git branch --show-current)" = "main"
  git fetch origin main
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
  git tag -a "v${release_version}" -m "Release v${release_version}"
  git push origin "v${release_version}"
)
```

The tag-only `Release` workflow validates the tag and its ancestry on `main`,
requires the latest matching main-push CI run for that exact commit to have
succeeded, builds and fresh-installs the exact wheel and source distribution,
and then reaches the `pypi` environment. Missing, pending, cancelled, or failed
CI blocks publication. After a CI or network issue is resolved, rerun the failed
Release job; do not move or recreate a public tag.

Keep required approval and `v*` deployment-tag restrictions configured on the
GitHub `pypi` environment; referencing an environment in YAML does not configure
its protection. A solo maintainer must be able to approve their own release.
See [GitHub's environment configuration guide](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
After any configured approval, trusted publishing sends the distributions to
PyPI and creates the GitHub Release. Never bypass the environment or add a
long-lived PyPI token as a workaround.

Use a patch release for compatible fixes and a minor release for features. While
the package is `0.x`, document any intentional incompatibility in a minor release;
do not silently break existing settings or commands in a patch. There is no fixed
release schedule and no version bump for each development commit.

Before publishing, review the changelog and generated release notes for clear
user-facing language, setup instructions, and any migration steps. The application
continues to support the Python versions declared in `pyproject.toml`.

The permanent publishing identifiers are:

- repository: `diyanko/NoteroPDF`
- workflow: `release.yml`
- GitHub environment and PyPI publisher environment: `pypi`
- allowed deployment tags: `v*`

After the workflow completes, verify the version and metadata on PyPI, the wheel
and source distribution on the GitHub Release, and a fresh `pipx install` or
upgrade followed by `noteropdf --version`. Check the README's first-run journey
with controlled test data before promoting the release. Keep announcements and
demos aligned with the published package; see [the roadmap](./ROADMAP.md).
