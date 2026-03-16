# AI Agentic Development Guidelines

This document outlines the core principles and instructions for AI agents (like GitHub Copilot, Gemini, Cursor, etc.) contributing to the **NoteroPDF** codebase. 

The primary goal of this document is to ensure that AI contributions maintain the project's focus on **simplicity, reliability, and deterministic behavior**.

> [!IMPORTANT]
> The absolute rule of this codebase: **Keep it simple, readable, and predictable. Overtly complex abstractions are forbidden.**

## 1. Core Principles

*   **No Magic:** Prefer explicit, step-by-step logic over clever meta-programming, deep class hierarchies, or highly abstracted functional chains.
*   **Safety First:** This app operates on data (Zotero databases, Notion pages). It must *always* fail safely and never perform unprompted destructive actions or write to Zotero.
*   **Clear Error Handling:** All errors must be clearly caught, categorized (see `Status` in `status.py`), and presented to the user with actionable next steps. Never swallow exceptions silently.
*   **Minimal Dependencies:** Do not add new external libraries unless absolutely necessary. Rely on the standard library whenever possible.

## 2. Testing Constraints

*   **Run command:** Use `python -m pytest` from the project root to execute tests. 
*   **100% Passing:** No code should be committed if *any* tests are failing. 
*   **Test Locality:** New business logic must include corresponding unit tests in the `tests/` directory.

## 3. Implementation Workflow

1.  **Analyze Context:** Before modifying code, read `config.py` and `sync_engine.py` to understand the flow.
2.  **Modular Changes:** Keep changes isolated and focused on a single issue or feature at a time.
3.  **Strict Typing:** Maintain and update Python type hints (`from __future__ import annotations`, `typing` module) for all new functions and variables.
4.  **Actionable Logging:** When adding new operations, log exactly what is happening using the standard `logging` setup (`self._logger.info(...)`). Include context (IDs, names).

## 4. Architecture Recap

*   **`cli.py`**: The entry point. Handles arguments and user prompts.
*   **`sync_engine.py`**: The orchestrator. Coordinates reading from Zotero, matching in Notion, uploading files, and saving state.
*   **`notion_client.py`**: A thin wrapper around the Notion API. It handles auth, retries, and rate-limit backoffs.
*   **`zotero_repo.py`**: Read-only interface to the local Zotero SQLite database and file system.
*   **`state_store.py`**: Local SQLite database to track what has already been synced, preventing double uploads.

*Note: Whenever asked to modify the codebase, always verify if existing tests cover the change. Run `python -m pytest` before marking a task as complete.*
