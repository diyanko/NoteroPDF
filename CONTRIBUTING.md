# Contributing to NoteroPDF

Thanks for helping improve NoteroPDF.

This project is built for reliability first. If you contribute, please prioritize predictable behavior, clear errors, and safe defaults.

## Ways to contribute

- Report bugs
- Improve documentation
- Add tests
- Improve reliability and error handling
- Propose or implement new features

## Before you start

1. Fork the repo and create a branch from `main`.
2. Keep changes focused on one problem per pull request.
3. If behavior changes, update docs in the same PR.

## Local setup

1. Install dependencies:

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

2. Run tests:

```bash
python -m pytest -q
```

3. (Optional) Run CLI help check:

```bash
python -m noteropdf --help
```

## Pull request expectations

Please include:
- What changed
- Why it changed
- Any tradeoffs or limitations
- How you tested it

If relevant, include example logs or report snippets (remove private data).

## Quality checklist

Before opening a PR:
- Tests pass locally
- New logic has tests
- Docs/config examples are updated if needed
- No secrets are committed (`.env`, private tokens, personal paths)
- Error messages are clear and actionable
- If this is a release PR, run the checks in `RELEASE_CHECKLIST.md`
- For maintainers publishing a release, follow `RELEASE_PROCESS.md` as the canonical flow

## Coding guidelines

- Prefer simple, explicit logic over clever shortcuts.
- Keep matching behavior deterministic.
- Avoid destructive behavior by default.
- Keep user-facing language plain and direct.

### AI-assisted development policy

This codebase is primarily built and maintained using AI assistants. If you are an AI assistant:
1. You **MUST** read [AI_AGENTIC_DEVELOPMENT.md](./AI_AGENTIC_DEVELOPMENT.md) before making any changes.
2. Favor readability, simplicity, and predictable behavior over complex abstractions. 
3. Never swallow errors silently.

## Reporting bugs

Open an issue with:
- What you expected
- What happened
- Steps to reproduce
- OS + Python version
- Command used
- Relevant error output

## Feature requests

For new ideas, include:
- Problem statement
- Proposed behavior
- Why it fits the project goals
- Any migration or compatibility concerns

## Security

If you find a security issue, do not post sensitive details publicly first.
Open a minimal issue asking for a private contact path.

Maintainer contact:
- GitHub: `@diyanko`
- Preferred first contact: a GitHub issue in this repository
