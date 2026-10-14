---
name: investigate-upstream-changes
description: Automated investigation protocol for diagnosing upstream Gemini web (BardChatUi) obfuscated code, RPC protocol mutations, and DOM structure shifts.
disable-model-invocation: true
---

# Investigating Upstream Gemini Changes

A repeatable, automated investigation discipline for reverse-engineering obfuscated Google Gemini web client updates (`gemini.google.com`), internal `BardChatUi` RPC protocol changes, and DOM mutations without modifying production application code.

Follow this plan strictly in phases. Every phase ends with a concrete, checkable completion criterion.

---

## Operational Guardrails

1. **Investigation Only**: Do not modify production application source code during an investigation. Record findings, write temporary probes to `/tmp/antigravity-scratch/precious_gems/`, and report verified signatures.
2. **Zero Secret Leakage**: Never log, commit, or print session cookies (`__Secure-*`, `SID`, `SAPISID`) or XSRF action tokens (`SNlM0e`). Redact tokens as `<REDACTED>` in outputs and artifacts.
3. **Port & CDP Strictness**: Default strictly to Chrome CDP at `http://127.0.0.1:9222` unless an explicit `--cdp-url` is specified.

---

## Phase 1 — Build the Inspection Harness

Before analyzing code, attach to the running Chrome CDP session and establish a repeatable probe harness.

### Step 1.1: Verify Chrome CDP & Active Page
Check that the browser is accessible over CDP and navigate or attach to a live Gemini tab:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    page = next((pg for pg in context.pages if "gemini.google.com" in pg.url), None)
    if not page:
        page = context.new_page()
        page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
```

### Step 1.2: Check Global Configuration & Build Version
Extract the current build version and active token keys from `window.WIZ_global_data`:

```javascript
(() => {
  const wiz = window.WIZ_global_data || {};
  return {
    buildLabel: wiz['cfb2h'],       // e.g. "boq_gemini-web-uiserver_20261011.07_p0"
    hasActionToken: !!wiz['SNlM0e'], // XSRF action token ("at")
    sessionId: wiz['FdrFJe'],        // Session ID ("f.sid")
    keys: Object.keys(wiz)
  };
})()
```

### Completion Criterion
You have recorded the current upstream `buildLabel` (`cfb2h`) and confirmed valid session tokens in `WIZ_global_data`.

---

## Phase 2 — Network RPC Traffic Interception

Gemini uses batched RPCs via `XMLHttpRequest` over `/_/BardChatUi/data/batchexecute`. When upstream breaks, capture and inspect the raw envelopes.

### Step 2.1: In-Page XHR Probe Hook
Inject a temporary non-destructive XHR hook into the active page before triggering user actions or navigation:

```javascript
(() => {
  window.__upstreamXHRs = window.__upstreamXHRs || [];
  if (window.__upstreamXHRHooked) return;
  window.__upstreamXHRHooked = true;

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url, ...args) {
    this._url = url;
    this._method = method;
    return origOpen.apply(this, [method, url, ...args]);
  };

  XMLHttpRequest.prototype.send = function(body) {
    this.addEventListener('load', () => {
      if (this._url && this._url.includes('batchexecute')) {
        window.__upstreamXHRs.push({
          url: this._url,
          status: this.status,
          postBody: typeof body === 'string' ? body.slice(0, 1000) : null,
          response: this.responseText
        });
      }
    });
    return origSend.apply(this, [body]);
  };
})()
```

### Step 2.2: Extract and Envelope-Decode RPC Responses
Google `batchexecute` responses start with `)]}'\n` followed by chunk sizes and array envelopes:
`[["wrb.fr", "<rpcid>", "<json_string_payload>", ...]]`

Parse all captured RPC IDs:
```python
import json

def parse_batchexecute(raw_text: str):
    results = {}
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or line.startswith(")]}'"):
            continue
        if line.startswith("["):
            try:
                envelope = json.loads(line)
                for item in envelope:
                    if isinstance(item, list) and len(item) > 2 and item[0] == "wrb.fr":
                        rpc_id = item[1]
                        payload = json.loads(item[2]) if item[2] else None
                        results[rpc_id] = payload
            except Exception:
                pass
    return results
```

### Key RPC Directory
- **`hNvQHb`**: Main conversation turn history tree (prompts, candidate responses, model version, timestamps).
- **`ESY5D`**: Session presence / heartbeat.
- **`VxUbXb`**: User notification / status updates.
- **`aPya6c`**: Client state ack.
- **`sJBwce`**: Sidebar conversation enumeration / catalog pagination.

### Completion Criterion
You have captured the raw payload of the failing/mutated RPC ID and extracted its parsed JSON array.

---

## Phase 3 — Obfuscated Payload & Array Index Mapping

Google's internal minified web client uses positional array indices instead of key-value JSON objects. When the upstream build changes, fields may shift position.

### Step 3.1: Automated Recursive Field Search
Run an automated search across the raw payload looking for signature primitives:

```python
import datetime

def locate_signatures(node, path=""):
    findings = []
    if isinstance(node, list):
        for idx, item in enumerate(node):
            findings.extend(locate_signatures(item, f"{path}[{idx}]"))
    elif isinstance(node, dict):
        for k, v in node.items():
            findings.extend(locate_signatures(v, f"{path}.{k}"))
    elif isinstance(node, (int, float)):
        # Epoch seconds (2025-2030: ~1.73e9 to ~1.90e9)
        if 1700000000 <= node <= 1900000000:
            iso = datetime.datetime.fromtimestamp(node, tz=datetime.timezone.utc).isoformat()
            findings.append((path, "epoch_seconds", node, iso))
    elif isinstance(node, str):
        if node.startswith("c_"):
            findings.append((path, "conversation_id", node, None))
        elif node.startswith("r_"):
            findings.append((path, "turn_id", node, None))
        elif node.startswith("rc_"):
            findings.append((path, "candidate_id", node, None))
        elif "gemini-" in node or "flash" in node or "pro" in node:
            findings.append((path, "model_version", node, None))
    return findings
```

### Step 3.2: Re-Map Positional Index Offsets
Compare the new indices against the known baseline:

| Field | Baseline Index in `entry` (`hNvQHb`) | Verified In Current Investigation |
|---|---|---|
| Conversation & Turn ID | `entry[0]` (`['c_<id>', 'r_<id>']`) | Yes |
| Parent Turn Reference | `entry[1]` | Yes |
| User Prompt Content | `entry[2][0][0]` | Yes |
| Model Candidate Array | `entry[3][0][0]` (`['rc_<id>', [text]]`) | Yes |
| Candidate Version History | `entry[3][2]` (`[[0, None, None, cand_id, [s, ns]], ...]`) | Yes |
| Model Version String | `entry[3][31]` or `entry[3][21]` | Yes |
| User Prompt Timestamp | `entry[4]` (`[epoch_seconds, nanoseconds]`) | Yes |

### Completion Criterion
Every field used by `precious_gems.rpc` has its positional index verified or re-mapped with a reproducible JSON path.

---

## Phase 4 — DOM & Obfuscated JS Script Analysis

When DOM extraction or sidebar scraping breaks, analyze the Angular / Lit custom elements.

### Step 4.1: Custom Tag Inventory
Dump active custom components inside the chat container:
```javascript
(() => {
  const elements = Array.from(document.querySelectorAll('*'))
    .filter(el => el.tagName.includes('-'))
    .map(el => el.tagName.toLowerCase());
  return Array.from(new Set(elements));
})()
```

### Step 4.2: JSLog & Metadata Extraction
Decode tracking keys on custom elements:
```javascript
(() => {
  return Array.from(document.querySelectorAll('[jslog]'))
    .filter(el => el.getAttribute('jslog').includes('BardVeMetadataKey:'))
    .map(el => {
      const jslog = el.getAttribute('jslog');
      const b64 = jslog.split('BardVeMetadataKey:')[1].split(';')[0];
      try {
        return { tag: el.tagName, data: JSON.parse(atob(b64)) };
      } catch (e) {
        return { tag: el.tagName, raw: b64 };
      }
    });
})()
```

### Step 4.3: Obfuscated Function & Print Hook Inspection
If the clean print synthesis hook (`SSn.JEd` / `iframe.contentWindow.print`) stops firing:
1. Search window scripts for `contentWindow.print` or iframe injection routines.
2. Inspect the print button click event listeners using CDP `DOMDebugger.getEventListeners`.

---

## Phase 5 — Synthesize Findings & Recommendations

### Step 5.1: Create Investigation Report
Write the findings to `/tmp/antigravity-scratch/precious_gems/upstream_investigation_<date>.md`.

The report must include:
1. Upstream Build Version (`cfb2h`).
2. Exact breaking change (e.g. index shifted from `entry[4]` to `entry[5]`, or RPC ID renamed from `hNvQHb` to `<new_id>`).
3. Minimal standalone Python reproduction script validating the new mapping.
4. Proposed patch plan for `precious_gems.rpc` or `precious_gems.extractor`.

### Step 5.2: Update Shared Operational Memory
If an upstream change introduces a persistent policy or breaking discovery, update `AGENTS.md` per the `"remember this"` protocol.
