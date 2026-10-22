"""Backend RPC extraction and communication for Gemini conversations."""

import datetime
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from .browser import is_target_closed_error
from .parser import extract_latest_timestamp, extract_turns_from_response_text
from .storage import normalize_title


def fetch_raw_conversation_rpc(
    page: Page,
    conversation_id: str,
) -> str:
    """Invokes batchexecute inside the page context using window.fetch and returns the raw response text."""
    slug = conversation_id.removeprefix("c_")
    target_conv_id = f"c_{slug}"

    js_code = """
    async (targetConvId) => {
        const wiz = window.WIZ_global_data || {};
        const atToken = wiz['SNlM0e'];
        const bl = wiz['cfb2h'];
        const fSid = wiz['FdrFJe'];

        if (!atToken) {
            throw new Error('Missing SNlM0e token in window.WIZ_global_data');
        }

        const innerReq = [targetConvId, 100, null, 1, [1], [4], null, 1];
        const outerEnvelope = [[["hNvQHb", JSON.stringify(innerReq), null, "generic"]]];

        const postBody = new URLSearchParams({
            'f.req': JSON.stringify(outerEnvelope),
            'at': atToken
        }).toString();

        const reqId = (Date.now() % 10000000).toString();
        const urlParams = new URLSearchParams({
            'rpcids': 'hNvQHb',
            'source-path': '/app/' + targetConvId.replace(/^c_/, ''),
            'bl': bl || '',
            'f.sid': fSid || '',
            'hl': 'el',
            '_reqid': reqId,
            'rt': 'c'
        });

        const url = '/_/BardChatUi/data/batchexecute?' + urlParams.toString();

        const response = await window.fetch(url, {
            method: 'POST',
            body: postBody,
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'X-Same-Domain': '1'
            }
        });

        if (!response.ok) {
            throw new Error('HTTP ' + response.status + ': ' + response.statusText);
        }

        return await response.text();
    }
    """

    return page.evaluate(js_code, target_conv_id)


def fetch_conversation_rpc(
    page: Page,
    conversation_id: str,
    title: Optional[str] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Invokes batchexecute inside the page context using window.fetch and returns structured JSON."""
    raw_response = fetch_raw_conversation_rpc(page, conversation_id)
    turns = extract_turns_from_response_text(raw_response)

    slug = conversation_id.removeprefix("c_")
    raw_title = title or (
        turns[0]["user_prompt"]["text"][:60] if turns else "Untitled Conversation"
    )
    resolved_title = normalize_title(raw_title)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {
        "id": slug,
        "title": resolved_title,
        "url": f"https://gemini.google.com/app/{slug}",
        "extracted_at": now_iso,
        "turn_count": len(turns),
        "turns": turns,
    }


def list_conversations_rpc(
    page: Page,
    batch_size: int = 50,
    max_rounds: int = 25,
) -> List[Dict[str, Any]]:
    """Lists all conversations directly via Gemini's MaZiqc batchexecute RPC, eliminating sidebar scrolling."""
    js_code = r"""
    async ({ batchSize, maxRounds }) => {
        const wiz = window.WIZ_global_data || {};
        const atToken = wiz['SNlM0e'];
        const bl = wiz['cfb2h'];
        const fSid = wiz['FdrFJe'];

        if (!atToken) {
            throw new Error('Missing SNlM0e token in window.WIZ_global_data');
        }

        const allConversations = [];
        let cursor = null;
        let round = 0;

        while (round < maxRounds) {
            round++;
            const innerReq = [batchSize, cursor, [null, null, 1, null, null, null, null, null, null, 1]];
            const outer = [[["MaZiqc", JSON.stringify(innerReq), null, "generic"]]];
            const postBody = new URLSearchParams({
                'f.req': JSON.stringify(outer),
                'at': atToken
            }).toString();

            const reqId = (Date.now() % 10000000 + round).toString();
            const urlParams = new URLSearchParams({
                'rpcids': 'MaZiqc',
                'source-path': '/app',
                'bl': bl || '',
                'f.sid': fSid || '',
                'hl': 'el',
                '_reqid': reqId,
                'rt': 'c'
            });

            const url = '/_/BardChatUi/data/batchexecute?' + urlParams.toString();
            const response = await window.fetch(url, {
                method: 'POST',
                body: postBody,
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                    'X-Same-Domain': '1'
                }
            });

            if (!response.ok) {
                throw new Error('HTTP ' + response.status + ': ' + response.statusText);
            }

            const txt = await response.text();
            let batchToken = null;
            let batchList = [];

            for (const line of txt.split('\n')) {
                if (line.startsWith('[')) {
                    try {
                        const d = JSON.parse(line);
                        let envelopes = [];
                        if (Array.isArray(d)) {
                            if (d.length > 0 && Array.isArray(d[0]) && d[0].length > 0 && Array.isArray(d[0][0])) {
                                envelopes = d[0];
                            } else {
                                envelopes = d;
                            }
                        }
                        for (const env of envelopes) {
                            if (Array.isArray(env) && env.length > 2 && env[0] === 'wrb.fr') {
                                const payloadStr = env[2];
                                if (payloadStr) {
                                    const payload = typeof payloadStr === 'string' ? JSON.parse(payloadStr) : payloadStr;
                                    if (payload && Array.isArray(payload)) {
                                        batchToken = payload[1] || null;
                                        batchList = payload[2] || [];
                                    }
                                }
                            }
                        }
                    } catch(e) {}
                }
            }

            for (const item of batchList) {
                if (Array.isArray(item) && item.length >= 2) {
                    const rawId = String(item[0]);
                    const cleanId = rawId.replace(/^c_/, '');
                    const rawTitle = item[1] || 'Untitled Conversation';
                    const cleanTitle = rawTitle.replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();
                    allConversations.push({
                        id: cleanId,
                        title: cleanTitle || 'Untitled Conversation',
                        url: 'https://gemini.google.com/app/' + cleanId
                    });
                }
            }

            if (!batchToken || batchList.length === 0) {
                break;
            }
            cursor = batchToken;
        }

        return allConversations;
    }
    """

    results = page.evaluate(js_code, {"batchSize": batch_size, "maxRounds": max_rounds})
    for r in results:
        r["title"] = normalize_title(r.get("title"))
    return results


def download_conversation_files(
    page: Page,
    output_dir: Path,
    export_filename: str,
    turns: List[Dict[str, Any]],
    max_attachment_size: Optional[int] = None,
    max_retries: int = 3,
    timeout_ms: int = 120000,
    timestamp_iso: Optional[str] = None,
) -> Dict[str, str]:
    """Downloads all conversation attachments into the conversation folder with retry logic and size filtering.
    Returns mapping of attachment filenames to their SHA-256 hashes.
    """
    conv_dir = output_dir / export_filename
    conv_dir.mkdir(parents=True, exist_ok=True)
    used_filenames: set[str] = set()
    attachment_hashes: Dict[str, str] = {}

    epoch: Optional[float] = None
    if timestamp_iso:
        try:
            epoch = datetime.datetime.fromisoformat(timestamp_iso).timestamp()
        except (ValueError, TypeError):
            epoch = None

    for turn in turns:
        for role_key in ("user_prompt", "model_response"):
            role_data = turn.get(role_key, {})
            attachments = role_data.get("attachments", [])
            for att in attachments:
                orig_name = att.get("filename") or "attachment"
                html_content = att.get("html_content")

                # Sanitize filename: replace path separators/slashes to prevent Errno 2 directory missing errors
                safe_name = orig_name.replace("/", "_").replace("\\", "_").strip()
                if not safe_name:
                    safe_name = "attachment"

                # Handle conflict with special conversation files
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix
                if safe_name.lower() in (
                    "conversation.json",
                    "conversation.html",
                    "meta.json",
                ):
                    safe_name = f"{stem} (attachment){suffix}"
                    stem = f"{stem} (attachment)"

                filename = safe_name
                counter = 1
                while filename in used_filenames:
                    filename = f"{stem}_{counter}{suffix}"
                    counter += 1
                used_filenames.add(filename)

                if filename != orig_name and orig_name in role_data.get("text", ""):
                    role_data["text"] = role_data["text"].replace(orig_name, filename)

                dest_file = conv_dir / filename

                # If inline HTML content is already present (e.g. widget HTML)
                if html_content is not None:
                    dest_file.write_text(html_content, encoding="utf-8")
                    h = hashlib.sha256(html_content.encode("utf-8")).hexdigest()
                    attachment_hashes[filename] = h
                    if epoch is not None:
                        try:
                            os.utime(dest_file, (epoch, epoch))
                        except OSError:
                            pass
                    att["local_path"] = filename
                    att.pop("html_content", None)
                    continue

                url = att.get("url")
                if not url:
                    continue

                # Check attachment size if known
                size_bytes = att.get("size_bytes")
                if (
                    max_attachment_size is not None
                    and isinstance(size_bytes, (int, float))
                    and size_bytes > max_attachment_size
                ):
                    print(
                        f"[INFO] Skipping attachment '{orig_name}' ({size_bytes} bytes): exceeds max attachment size ({max_attachment_size} bytes)",
                        flush=True,
                    )
                    continue

                if "googleusercontent.com/gg/" in url or "googleusercontent.com/test_url" in url:
                    fetch_url = url if "=s0" in url else f"{url}=s0"
                else:
                    fetch_url = url

                downloaded = False
                for attempt in range(max_retries):
                    try:
                        resp = page.request.get(fetch_url, timeout=timeout_ms)
                        if resp.status == 200:
                            body_bytes = resp.body()
                            if (
                                max_attachment_size is not None
                                and len(body_bytes) > max_attachment_size
                            ):
                                print(
                                    f"[INFO] Skipping attachment '{filename}' ({len(body_bytes)} bytes): exceeds max attachment size ({max_attachment_size} bytes)",
                                    flush=True,
                                )
                                downloaded = True
                                break
                            dest_file.write_bytes(body_bytes)
                            h = hashlib.sha256(body_bytes).hexdigest()
                            attachment_hashes[filename] = h
                            if epoch is not None:
                                try:
                                    os.utime(dest_file, (epoch, epoch))
                                except OSError:
                                    pass
                            att["local_path"] = filename
                            if att.get("size_bytes") is None:
                                att["size_bytes"] = len(body_bytes)
                            downloaded = True
                            break
                        else:
                            print(
                                f"[WARNING] Attempt {attempt + 1}/{max_retries} failed downloading {filename}: HTTP {resp.status}",
                                flush=True,
                            )
                    except (
                        PlaywrightError,
                        PlaywrightTimeoutError,
                        OSError,
                        TimeoutError,
                    ) as err:
                        if is_target_closed_error(err):
                            raise
                        print(
                            f"[WARNING] Attempt {attempt + 1}/{max_retries} error downloading {filename}: {err}",
                            flush=True,
                        )

                    if attempt < max_retries - 1:
                        sleep_time = 3.0 + (attempt + 1) * 0.5
                        time.sleep(sleep_time)

                if not downloaded:
                    print(
                        f"[ERROR] Failed downloading attachment '{filename}' after {max_retries} attempts.",
                        flush=True,
                    )

    return attachment_hashes
