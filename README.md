# Precious Gems 💎

**Precious Gems** is a high-reliability backup and extraction engine for Google Gemini (`gemini.google.com`).

It connects to your open Google Chrome browser via Chrome DevTools Protocol (CDP) and exports conversations directly into structured JSON files with turn-level timestamps, user queries, model responses, candidate edit histories, model versions, and rich attachments (generated images, thought signatures, uploaded documents, generated videos, and interactive widgets).
It also supports legacy HTML print exports by intercepting Gemini's print-to-PDF synthesis pipeline.

---

## Quick Start in 3 Steps

### 1. Launch Chrome with Remote Debugging
Open your terminal and start Google Chrome with debugging enabled:
```bash
google-chrome --remote-debugging-port=9222
```
*Make sure you are logged into your Google account at [gemini.google.com](https://gemini.google.com) in this Chrome window.*

### 2. Install Precious Gems
From this repository directory:
```bash
pip install -e .
```
*(and then run directly via `precious-gems`)*

### 3. Back Up Your Conversations
To discover and automatically back up any new or unexported conversations to structured JSON:
```bash
precious-gems sync
```

To use legacy HTML print exports instead:
```bash
precious-gems sync --html
```

---

## Common Commands

### 📋 Index All Conversations (`scan`)
Scans through your Gemini sidebar history and saves a complete list of your conversation titles and IDs into `conversations.json`:
```bash
precious-gems scan
```

### 🔄 Back Up Unexported Conversations (`sync`)
Downloads conversations starting from the most recent:
```bash
precious-gems sync
```

> **Tip:** You can limit how many chats are downloaded in a single run:
> ```bash
> precious-gems sync --limit 5
> ```

> **Fast Sync:** If you ran `scan` previously, you can sync directly from the cached list without re-checking the sidebar:
> ```bash
> precious-gems sync --from-cache
> ```

### 📥 Export a Single Conversation (`export`)
Back up an individual chat by its link or ID:
```bash
precious-gems export https://gemini.google.com/app/1a2b3c4d5e
```

To export as an offline HTML file:
```bash
precious-gems export https://gemini.google.com/app/1a2b3c4d5e --html
```

### 💾 Dump Raw Response (`raw`)
Fetch the raw unparsed Gemini `batchexecute` response for debugging or filing bug reports:
```bash
precious-gems raw https://gemini.google.com/app/1a2b3c4d5e
```
Saves the unparsed payload to `<output-dir>/<conversation_id>_raw.txt`.

### 📊 Check Backup Status (`list`)
See what has already been backed up to your archive folder:
```bash
precious-gems list
```

To see your entire catalog and check which chats are pending backup:
```bash
precious-gems list --all
```

### 🗑️ Delete Conversations Upstream (`delete`)
Delete one or more conversations from Gemini by ID or URL:
```bash
precious-gems delete https://gemini.google.com/app/1a2b3c4d5e
```

By default, deletion safely checks that the conversation is backed up locally before deleting. To force deletion anyway:
```bash
precious-gems delete https://gemini.google.com/app/1a2b3c4d5e --force
```

To also remove the local archive folder and catalog entry:
```bash
precious-gems delete https://gemini.google.com/app/1a2b3c4d5e --delete-archived
```

---

## Where Are My Backups Saved?

By default, files are saved in the `./output` folder inside dedicated conversation directories:

```text
output/
├── manifest.json              # Tracks which conversations have been saved
├── conversations.json         # Index of all discovered conversations
├── 215240ab5cb6f4bc_Printer_Guide/
│   ├── conversation.json         # Structured JSON with turn timestamps (or conversation.html with --html)
│   ├── meta.json                 # Companion record metadata
│   ├── watermarked_img_0_0.jpg   # Generated images (Imagen)
│   ├── thought_signature_0.pb    # Model thought signatures
│   ├── diagram.png               # Downloaded turn attachments (if any)
│   └── spec_manual.pdf
└── ...
```

- **Interruption-safe:** Files are saved atomically so an interrupted download will never corrupt existing files.

---

## Options Cheat Sheet

| Option | What it does | Default |
| :--- | :--- | :--- |
| `--output-dir <folder>` | Where to save backup files | `./output` |
| `--cdp-url <url>` | Address of your Chrome debugging session | `http://127.0.0.1:9222` |
| `--html` | Use legacy in-browser print DOM export producing HTML instead of JSON | Off (JSON default) |
| `--limit <number>` | Maximum number of conversations to export (`sync`) | All |
| `--delay <seconds>` | Pause in seconds between conversation exports (`sync`) | `0.5` |
| `-f, --force` | Re-export even if already backed up (`export`, `sync`) or delete without local backup check (`delete`) | Off |
| `--from-cache` | (`sync` only) Use cached `conversations.json` instead of scanning sidebar | Off |
| `--max-attachment-size <size>` | Maximum attachment size to download (e.g. `50MB`, `500KB`, `1000`) | No limit |
| `--max-scrolls <number>` | Maximum sidebar scroll passes (only applies when `--html` is specified) | `35` |
| `--all` | (`list` only) Show all cataloged chats marked as `[Exported]` or `[Pending]` | Off |
| `--delete-archived` | (`delete` only) Also purge local exported folder and manifest entry | Off |
