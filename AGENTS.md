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

1. **No Silent Errors & Transparent Fail-Fast:**
   * Never write bare `except:` or `except Exception: pass`.
   * Catch specific expected exceptions (`playwright.sync_api.Error`, `TimeoutError`, `FileNotFoundError`, `KeyError`, `json.JSONDecodeError`).
   * Fail fast: unexpected errors, bad configurations, or invalid responses must crash immediately. Log diagnostic context clearly before re-raising (`raise SpecificError(...) from err`), preserving original tracebacks.
2. **Lean Output & Context Protection:**
   * Avoid stdout spam that bloats the LLM context window. Long-running progress streams must be redirected to log files in `/tmp/antigravity-scratch/precious_gems/`.
   * High-density technical communication: zero fluff, zero conversational preamble.
3. **Port & Configuration Privacy:**
   * Never hardcode custom or private ports (e.g. 9225) as defaults in code, configuration, or documentation.
   * Default strictly to the standard Chrome remote debugging port `http://127.0.0.1:9222`. Custom ports must always be passed explicitly at runtime via `--cdp-url`.
4. **Git Restrictions:**
   * Never execute `git commit` or any other `git` command that modifies repository history or branches. Agents inspect status and author journal proposals; committing and staging is a reserved user action.
5. **Test Invocation & Timing:**
   * **Interpreter Binary:** Always invoke tests using `virtenv/bin/python -m pytest tests/ -v` (never bare `pytest` and never system `python3`). For CLI execution, use `./virtenv/bin/precious-gems`.
   * **Timing Gate:** Never run tests prematurely before authoring code changes. Test only after completing a cohesive batch of edits, targeting solely the modified components during active iteration.
   * **Regression Gate & Exemptions:** Run the entire test suite once upon finishing an implementation plan prior to authoring the walkthrough. Omit testing entirely for non-code workflows: documentation updates, exploratory research, or shell-only executions.
6. **Avoid Overengineering (The "Variant" Lesson):**
   * Default to the simplest idiomatic Python pattern that fulfills the requirement. Avoid complex metaclass dispatches or fragmented subclass hierarchies for simple data structures.
7. **WINE Contribution Guidelines for Git Commits:**
   * All proposed git commit messages must adhere strictly to WINE project contribution standards.
   * **Subject Line:** Format as `<component>: <One sentence in imperative mood ending with a period.>`. Must be concise and <= 72 characters (e.g., `extractor: Add scroll hydration loop for conversation turns.`).
   * **Message Body:** Separated from the subject by a blank line. Formatted in clear, technical imperative prose (give orders to the codebase, e.g. "Do X", not "Did X" or "This patch adds X"). Explain *why* the change was made and *how* it functions without echoing the diff.
   * **Formatting & Wrapping:** No markdown bullets, asterisks, or decorative lists in the commit message body; use clean paragraphs wrapped to 72 columns.
   * **Sign-Off:** Not required. Leave blank by default.
8. **Git Commit Accumulation & Per-Turn Journaling:**
   * At each turn where the codebase was edited, record modifications in `.git/agent/{index:03d}_{descriptive_name}.md`.
   * Entries track changed files, rationale, and draft WINE commit messages.
   * At pre-commit, consolidate accumulated entries and present the finalized commit proposal.
9. **Greenfield Architecture (Zero Legacy Shims):**
   * Greenfield project: no backward compatibility requirements.
   * Refactor directly: do not add deprecation warnings, compatibility shims, or wrappers for obsolete APIs. Update callers in place.
10. **Plain & Accessible Language:**
    * Use clear, direct, and accessible English in plans, walkthroughs, user questions, code comments, and documentation.
    * Avoid baroque vocabulary, decorative buzzwords, or convoluted phrasing. (Agent instructions and rules are exempt where dense technical shorthand is required).
11. **Immediate Rule Ingestion ("Remember This" Protocol):**
    * Whenever the user intervenes with `"remember this"`, the agent must immediately update `AGENTS.md` (or global rules when authorized) to persist the correction or policy into operational memory.


---

## Development Principles

* **Decoupled Architecture:** Separate browser CDP interaction, DOM manipulation / script injection, and storage serialization cleanly.
* **Deterministic Atomic Persistence:** Ensure conversation dumps and manifest updates use atomic write strategies (writing to temporary files and renaming) to prevent data corruption during aborted runs.
* **State Resumption:** Incremental backup mode must always inspect `manifest.json` before navigating, skipping already backed-up conversations by default.
