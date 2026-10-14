# Precious Gems 💎

**Precious Gems** is a high-reliability backup and extraction engine for Google Gemini (`gemini.google.com`).

It connects to your open Google Chrome browser via Chrome DevTools Protocol (CDP) and exports your conversations directly into structured JSON files with turn-level timestamps, user queries, model responses, candidate edit histories, and model versions.
It also supports legacy HTML print exports that works by intercepting the export to PDF function.

---

## Python Library Usage

Precious Gems can be imported and used as a Python library:

```python
from precious_gems import GeminiClient

with GeminiClient(cdp_url="http://127.0.0.1:9222", output_dir="./output") as client:
    # List all conversations from the sidebar
    conversations = client.list_conversations()
    for conv in conversations:
        print(conv.id, conv.title)

    # Export a conversation to JSON (with full turn timestamps)
    record = client.export_conversation("1a2b3c4d5e")
    print(f"Exported to {record.export_filename}/conversation.json")

    # Export using legacy HTML print fallback
    record_html = client.export_conversation("1a2b3c4d5e", use_html_fallback=True)
    print(f"Exported to {record_html.export_filename}/conversation.html")

    # Synchronize all conversations
    client.sync_all(limit=10)
```

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
virtenv/bin/pip install -e .
```
*(run directly via `./virtenv/bin/precious-gems`)*

### 3. Back Up Your Conversations
To discover and automatically back up any new or unexported conversations to structured JSON:
```bash
./virtenv/bin/precious-gems sync
```

To use legacy HTML print exports instead:
```bash
./virtenv/bin/precious-gems sync --html
```

---

## Common Commands

### 📋 Index All Conversations (`scan`)
Scans through your Gemini sidebar history and saves a complete list of your conversation titles and IDs into `conversations.json`:
```bash
./virtenv/bin/precious-gems scan
```

### 🔄 Back Up Unexported Conversations (`sync`)
Downloads conversations starting from the most recent:
```bash
./virtenv/bin/precious-gems sync
```

> **Tip:** You can limit how many chats are downloaded in a single run:
> ```bash
> ./virtenv/bin/precious-gems sync --limit 5
> ```

> **Fast Sync:** If you ran `scan` previously, you can sync directly from the cached list without re-checking the sidebar:
> ```bash
> ./virtenv/bin/precious-gems sync --from-cache
> ```

### 📥 Export a Single Conversation (`export`)
Back up an individual chat by its link or ID:
```bash
./virtenv/bin/precious-gems export https://gemini.google.com/app/1a2b3c4d5e
```

To export as an offline HTML file:
```bash
./virtenv/bin/precious-gems export https://gemini.google.com/app/1a2b3c4d5e --html
```

### 📊 Check Backup Status (`list`)
See what has already been backed up to your archive folder:
```bash
./virtenv/bin/precious-gems list
```

To see your entire catalog and check which chats are pending backup:
```bash
./virtenv/bin/precious-gems list --all
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
| `--limit <number>` | Maximum number of conversations to export | All |
| `--delay <seconds>` | Pause between conversations | `0.5` |
| `--force` | Re-export conversations even if already backed up | Off |
| `--from-cache` | (`sync` only) Use cached `conversations.json` instead of scanning sidebar | Off |
| `--max-attachment-size <size>` | Maximum attachment size to download (e.g. `50MB`, `500KB`, `1000`) | No limit |
| `--all` | (`list` only) Show all cataloged chats marked as `[Exported]` or `[Pending]` | Off |
