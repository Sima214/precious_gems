# AGENTS.md

## Overview
Precious Gems (`precious_gems`) is a high-reliability extraction and backup engine for Google Gemini conversations (`gemini.google.com`). It programmatically connects via Chrome DevTools Protocol (CDP) to an existing authenticated browser session, intercepts Gemini's internal clean HTML export pipeline (the "Export to PDF" DOM synthesis engine), hydrates the full conversational turn history via pre-export scrolling, and maintains an incrementally updated backup archive with state tracking.

---

## Repository Layout

* **`src/precious_gems/`**: Core application package.
  * **`cli/`**: Command-line interface (`entry.py`).
  * **`browser.py`**: CDP connection lifecycle and dedicated tab management via Playwright.
  * **`interceptor.py`**: Injected script hooks intercepting `iframe.contentWindow.print` and capturing clean synthesized HTML.
  * **`extractor.py`**: Scroll hydration loop, chat action menu activation, and export trigger orchestration.
  * **`sidebar.py`**: Conversation enumeration and navigation via the Gemini sidebar.
  * **`storage.py`**: Manifest persistence (`manifest.json`), atomic file writing, slug sanitization, and companion metadata.
* **`tests/`**: Unit and integration test suites.
* **`pyproject.toml`**: Packaging configuration, dependency definitions, and CLI entry points.
* **`README.md`**: User and developer documentation.

---

## Inviolable Operational Rules

1. **No Silent Errors (No Catch-All Try/Except):**
   * Never write bare `except:` or `except Exception: pass`.
   * Catch specific expected exceptions (`playwright.sync_api.Error`, `TimeoutError`, `FileNotFoundError`, `KeyError`, `json.JSONDecodeError`).
   * Fail fast: re-raise or chain exceptions (`raise SpecificError(...) from err`) preserving original tracebacks.
2. **Lean Output & Context Protection:**
   * Avoid stdout spam that bloats the LLM context window. Log only high-density technical status updates (conversation ID, title, byte size, status).
   * High-density technical communication: zero fluff, zero conversational preamble.
3. **Port & Configuration Privacy:**
   * Never hardcode custom or private ports (e.g. 9225) as defaults in code, configuration, or documentation.
   * Default strictly to the standard Chrome remote debugging port `http://127.0.0.1:9222`. Custom ports must always be passed explicitly at runtime via `--cdp-url`.
4. **Git Restrictions:**
   * Never execute `git commit` or any other `git` command that modifies the filesystem. Agents are authorized to inspect status, but commit creation and staging is a reserved user action.
5. **Test Invocation, Scoped Execution & Timing:**
   * **Interpreter Binary:** Always invoke tests using `virtenv/bin/python -m pytest tests/ -v` (never bare `pytest` and never system `python3`). For CLI execution, use `./virtenv/bin/precious-gems` (or `virtenv/bin/python -m precious_gems.cli.entry`).
   * **Timing Gate:** Never run tests prematurely before authoring code changes. Test only after completing a cohesive batch of edits, targeting solely the modified components during active iteration.
   * **Wait Patiently:** When tests or commands are launched in the background, wait patiently for completion. Never poll in a loop. Rely strictly on reactive system notifications.
   * **Regression Gate:** Run the test suite upon finishing an implementation plan prior to authoring the walkthrough.
6. **Avoid Overengineering:**
   * Do not construct complex machinery when simple, idiomatic Python suffices. Default to standard library structures (`dataclasses`, `pathlib`, `argparse`, `json`) and lean async/sync routines.
7. **Immediate Rule Ingestion ("Remember This" Protocol):**
   * Whenever the user intervenes with `"remember this"`, the agent must immediately update `AGENTS.md` to persist the correction or policy into the repository's operational memory.
8. **WINE Contribution Guidelines for Proposed Commits:**
   * All proposed git commit messages must adhere strictly to WINE project contribution standards:
     * **Subject Line:** Format as `<component>: <One sentence in imperative mood ending with a period.>`. Concise and <= 72 characters (e.g., `extractor: Add scroll hydration loop for conversation turns.`).
     * **Message Body:** Separated from subject by a blank line. Clear, technical imperative prose (give orders to the codebase) explaining *why* and *how*. No markdown bullets or decorative lists; wrap paragraphs to 72 columns.
     * **Sign-Off:** Not required. Leave blank by default.

---

## Development Principles

* **Decoupled Architecture:** Separate browser CDP interaction, DOM manipulation / script injection, and storage serialization cleanly.
* **Deterministic Atomic Persistence:** Ensure conversation dumps and manifest updates use atomic write strategies (writing to temporary files and renaming) to prevent data corruption during aborted runs.
* **State Resumption:** Incremental backup mode must always inspect `manifest.json` before navigating, skipping already backed-up conversations by default.
