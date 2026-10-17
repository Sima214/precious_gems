"""Backend RPC extraction and turn parser for Gemini conversations."""

import datetime
import hashlib
import json
import os
import re
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
from .storage import normalize_title


def parse_rpc_payload(payload: list) -> List[Dict[str, Any]]:
    """Parses batchexecute response payload for rpcid hNvQHb into structured turn data."""
    turns: List[Dict[str, Any]] = []
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return turns

    raw_entries = payload[0]
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, list) or len(entry) < 5:
            continue

        turn_id = None
        conv_id = None
        if isinstance(entry[0], list) and len(entry[0]) >= 2:
            conv_id = entry[0][0]
            turn_id = entry[0][1]

        # Extract parent turn pointer (c_id, parent_turn_id, parent_candidate_id)
        parent_turn_id = None
        if isinstance(entry[1], list) and len(entry[1]) >= 2:
            parent_turn_id = entry[1][1]
        elif len(entry) > 1 and entry[1] is not None:
            print(
                f"[CANARY] Unexpected turn entry[1] value in turn {turn_id}: {entry[1]}",
                flush=True,
            )

        # Extract custom bot / system instructions if present
        custom_instructions = None
        if len(entry) > 9 and isinstance(entry[9], list) and len(entry[9]) >= 3:
            bot_name = entry[9][0] if isinstance(entry[9][0], str) else None
            bot_desc = entry[9][1] if isinstance(entry[9][1], str) else None
            bot_prompt = entry[9][2] if isinstance(entry[9][2], str) else None
            bot_id = entry[7] if len(entry) > 7 and isinstance(entry[7], str) else None
            if bot_prompt or bot_name:
                custom_instructions = {
                    "id": bot_id,
                    "name": bot_name,
                    "description": bot_desc,
                    "instructions": bot_prompt,
                }

        # Canary: check unexpected extra fields in turn entry
        # Indices 7 (bot_id), 9 (bot profile), 10 (bot prompt duplicate) are recognized.
        if len(entry) > 5:
            for extra_i in range(5, len(entry)):
                if extra_i in (7, 9, 10):
                    continue
                if entry[extra_i] is not None:
                    print(
                        f"[CANARY] Unexpected field at turn entry[{extra_i}] in turn {turn_id}: {entry[extra_i]}",
                        flush=True,
                    )

        # Extract user prompt text and attachments
        user_text = ""
        attachments: List[Dict[str, Any]] = []
        seen_att_urls: set[str] = set()

        if isinstance(entry[2], list) and len(entry[2]) > 0 and isinstance(entry[2][0], list):
            p0 = entry[2][0]
            user_text = p0[0] if len(p0) > 0 and isinstance(p0[0], str) else ""

            # Canary: check unexpected fields in user prompt container
            if len(p0) > 1 and (
                p0[1] is not None
                or (len(p0) > 2 and p0[2] is not None)
                or (len(p0) > 3 and p0[3] is not None)
            ):
                print(
                    f"[CANARY] Unexpected non-null value in user prompt p0[1..3] in turn {turn_id}",
                    flush=True,
                )
            if len(p0) > 5:
                for pi in range(5, len(p0)):
                    if p0[pi] is not None:
                        print(
                            f"[CANARY] Unexpected field in user prompt p0[{pi}] in turn {turn_id}: {p0[pi]}",
                            flush=True,
                        )

            # Extract user uploaded attachments
            # Note: att_grp[3] contains attachments explicitly uploaded in this specific turn.
            # att_grp[4] carries previous conversation attachments as propagated context.
            if len(p0) > 4 and isinstance(p0[4], list):
                for att_grp in p0[4]:
                    if (
                        isinstance(att_grp, list)
                        and len(att_grp) > 3
                        and isinstance(att_grp[3], list)
                    ):
                        for item in att_grp[3]:
                            if isinstance(item, list) and len(item) > 3:
                                fname = item[2] if isinstance(item[2], str) else "attachment"
                                att_url = item[3] if isinstance(item[3], str) else None
                                # Documents/PDFs store download endpoints in item[7]
                                if not att_url and len(item) > 7 and isinstance(item[7], list):
                                    for candidate_url in item[7]:
                                        if isinstance(candidate_url, str) and (
                                            "contribution.usercontent.google.com" in candidate_url
                                            or "download" in candidate_url
                                        ):
                                            att_url = candidate_url
                                            break
                                    if not att_url and item[7] and isinstance(item[7][0], str):
                                        att_url = item[7][0]

                                if att_url and att_url not in seen_att_urls:
                                    seen_att_urls.add(att_url)
                                    m_type = (
                                        item[11]
                                        if len(item) > 11 and isinstance(item[11], str)
                                        else None
                                    )
                                    w, h, sz = None, None, None
                                    if (
                                        len(item) > 15
                                        and isinstance(item[15], list)
                                        and len(item[15]) >= 3
                                    ):
                                        w = item[15][0]
                                        h = item[15][1]
                                        sz = item[15][2]
                                    attachments.append(
                                        {
                                            "filename": fname,
                                            "url": att_url,
                                            "mime_type": m_type,
                                            "width": w,
                                            "height": h,
                                            "size_bytes": sz,
                                        }
                                    )

        # Extract user creation timestamp
        user_timestamp = None
        if (
            isinstance(entry[4], list)
            and len(entry[4]) >= 1
            and isinstance(entry[4][0], (int, float))
        ):
            sec = entry[4][0]
            nanos = entry[4][1] if len(entry[4]) > 1 else 0
            user_dt = datetime.datetime.fromtimestamp(sec + nanos / 1e9, tz=datetime.timezone.utc)
            user_timestamp = user_dt.isoformat()
            if len(entry[4]) > 2:
                print(
                    f"[CANARY] Unexpected user timestamp fields in turn {turn_id}: {entry[4][2:]}",
                    flush=True,
                )

        # Extract model response text, search queries, grounding/citations, edit history, and metadata
        model_text = ""
        model_timestamp = None
        candidate_history = []
        model_version = None
        search_queries = []
        web_search_grounding = []
        model_attachments = []

        if isinstance(entry[3], list):
            # entry[3][1] contains search queries executed during turn generation
            if len(entry[3]) > 1 and entry[3][1] is not None:
                if isinstance(entry[3][1], list):
                    for sq in entry[3][1]:
                        if isinstance(sq, list) and len(sq) > 0 and isinstance(sq[0], str):
                            search_queries.append(sq[0])
                        elif isinstance(sq, str):
                            search_queries.append(sq)
                else:
                    print(
                        f"[CANARY] Unexpected candidate entry[3][1] value in turn {turn_id}: {entry[3][1]}",
                        flush=True,
                    )

            if len(entry[3]) > 0 and isinstance(entry[3][0], list) and len(entry[3][0]) > 0:
                cand_list = entry[3][0]
                if isinstance(cand_list[0], list):
                    cand = cand_list[0]
                    if len(cand) > 1 and isinstance(cand[1], list) and len(cand[1]) > 0:
                        model_text = cand[1][0] if isinstance(cand[1][0], str) else str(cand[1][0])
                    elif len(cand) > 1 and isinstance(cand[1], str):
                        model_text = cand[1]

                    # Extract web citations / search grounding from cand[2]
                    if len(cand) > 2 and isinstance(cand[2], list) and len(cand[2]) > 1:
                        raw_cites = cand[2][1]
                        if isinstance(raw_cites, list):
                            for c_item in raw_cites:
                                if isinstance(c_item, list) and len(c_item) > 2:
                                    phrase = (
                                        c_item[0][0]
                                        if isinstance(c_item[0], list) and len(c_item[0]) > 0
                                        else None
                                    )
                                    indices = (
                                        c_item[0][3]
                                        if isinstance(c_item[0], list) and len(c_item[0]) > 3
                                        else None
                                    )
                                    sources = []
                                    if isinstance(c_item[2], list):
                                        for src in c_item[2]:
                                            if isinstance(src, list) and len(src) >= 2:
                                                sources.append(
                                                    {
                                                        "url": src[0],
                                                        "title": (src[1] if len(src) > 1 else None),
                                                        "snippet": (
                                                            src[3] if len(src) > 3 else None
                                                        ),
                                                    }
                                                )
                                    web_search_grounding.append(
                                        {
                                            "phrase": phrase,
                                            "indices": indices,
                                            "sources": sources,
                                        }
                                    )

                    # Extract interactive widgets from cand[41]
                    if len(cand) > 41 and isinstance(cand[41], list):
                        for w_entry in cand[41]:
                            if (
                                isinstance(w_entry, list)
                                and len(w_entry) > 0
                                and isinstance(w_entry[0], str)
                            ):
                                w_id = w_entry[0]
                                w_html = (
                                    w_entry[4]
                                    if len(w_entry) > 4 and isinstance(w_entry[4], str)
                                    else ""
                                )
                                if w_html:
                                    clean_html = w_html
                                    if clean_html.startswith("```html\n"):
                                        clean_html = clean_html.removeprefix("```html\n")
                                    elif clean_html.startswith("```"):
                                        clean_html = clean_html.split("\n", 1)[-1]
                                    if clean_html.endswith("```"):
                                        clean_html = clean_html.removesuffix("```").rstrip()

                                    w_title = (
                                        w_entry[9]
                                        if len(w_entry) > 9 and isinstance(w_entry[9], str)
                                        else None
                                    )
                                    model_attachments.append(
                                        {
                                            "filename": f"{w_id}.html",
                                            "url": None,
                                            "mime_type": "text/html",
                                            "size_bytes": len(clean_html.encode("utf-8")),
                                            "title": w_title,
                                            "widget_id": w_id,
                                            "html_content": clean_html,
                                        }
                                    )

                    # Extract carousel images and model-generated images from cand[12][1]
                    if len(cand) > 12 and isinstance(cand[12], list):
                        if len(cand[12]) > 1 and isinstance(cand[12][1], list):
                            for img_item in cand[12][1]:
                                if isinstance(img_item, list) and len(img_item) > 0:
                                    img_url = None
                                    if (
                                        isinstance(img_item[0], list)
                                        and len(img_item[0]) > 0
                                        and isinstance(img_item[0][0], list)
                                        and img_item[0][0]
                                        and isinstance(img_item[0][0][0], str)
                                    ):
                                        img_url = img_item[0][0][0]

                                    tag = None
                                    caption = None
                                    if (
                                        len(img_item) > 7
                                        and isinstance(img_item[7], list)
                                        and img_item[7]
                                    ):
                                        tag_str = (
                                            img_item[7][0]
                                            if isinstance(img_item[7][0], str)
                                            else ""
                                        )
                                        tag = tag_str.split("/")[-1] if "/" in tag_str else tag_str
                                        if len(img_item[7]) > 2 and isinstance(img_item[7][2], str):
                                            caption = img_item[7][2]

                                    attr_url = None
                                    if (
                                        len(img_item) > 1
                                        and isinstance(img_item[1], list)
                                        and img_item[1]
                                        and isinstance(img_item[1][0], list)
                                        and img_item[1][0]
                                        and isinstance(img_item[1][0][0], str)
                                    ):
                                        attr_url = img_item[1][0][0]

                                    w = (
                                        img_item[2]
                                        if len(img_item) > 2 and isinstance(img_item[2], int)
                                        else None
                                    )
                                    h = (
                                        img_item[3]
                                        if len(img_item) > 3 and isinstance(img_item[3], int)
                                        else None
                                    )

                                    fname = f"{tag}.jpg" if tag else "model_image.jpg"
                                    if img_url:
                                        model_attachments.append(
                                            {
                                                "filename": fname,
                                                "url": img_url,
                                                "mime_type": "image/jpeg",
                                                "width": w,
                                                "height": h,
                                                "title": caption,
                                                "attribution_url": attr_url,
                                            }
                                        )

                        # Extract generated video from cand[12][2]["60"]
                        if len(cand[12]) > 2 and isinstance(cand[12][2], dict):
                            val60 = cand[12][2].get("60")
                            if isinstance(val60, list) and val60 and isinstance(val60[0], list):
                                for v_entry in val60[0]:
                                    if isinstance(v_entry, list) and len(v_entry) > 0:
                                        v_desc = None
                                        v_meta = None
                                        if isinstance(v_entry[0], list):
                                            cand_desc = v_entry[0]
                                            while (
                                                isinstance(cand_desc, list)
                                                and len(cand_desc) > 0
                                                and isinstance(cand_desc[0], list)
                                            ):
                                                cand_desc = cand_desc[0]
                                            v_desc = cand_desc
                                        if (
                                            len(v_entry) > 2
                                            and isinstance(v_entry[2], list)
                                            and v_entry[2]
                                        ):
                                            v_meta = v_entry[2]

                                        if isinstance(v_desc, list) and len(v_desc) > 7:
                                            dl_urls = (
                                                v_desc[7] if isinstance(v_desc[7], list) else []
                                            )
                                            vid_url = None
                                            for u in dl_urls:
                                                if isinstance(u, str) and (
                                                    "contribution.usercontent.google.com" in u
                                                    or "download" in u
                                                ):
                                                    vid_url = u
                                                    break
                                            if (
                                                not vid_url
                                                and dl_urls
                                                and isinstance(dl_urls[0], str)
                                            ):
                                                vid_url = dl_urls[0]

                                            m_type = (
                                                v_desc[11]
                                                if len(v_desc) > 11 and isinstance(v_desc[11], str)
                                                else "video/mp4"
                                            )
                                            tag_str = (
                                                v_meta[0]
                                                if v_meta and isinstance(v_meta[0], str)
                                                else "generated_video"
                                            )
                                            tag = (
                                                tag_str.split("/")[-1]
                                                if "/" in tag_str
                                                else tag_str
                                            )
                                            fname = f"{tag}.mp4"

                                            vw = None
                                            vh = None
                                            if (
                                                len(v_desc) > 17
                                                and isinstance(v_desc[17], list)
                                                and len(v_desc[17]) >= 3
                                            ):
                                                vw = v_desc[17][1]
                                                vh = v_desc[17][2]

                                            if vid_url:
                                                model_attachments.append(
                                                    {
                                                        "filename": fname,
                                                        "url": vid_url,
                                                        "mime_type": m_type,
                                                        "width": vw,
                                                        "height": vh,
                                                    }
                                                )

            # Check candidate version history
            if len(entry[3]) > 2 and isinstance(entry[3][2], list):
                for cand_entry in entry[3][2]:
                    if (
                        isinstance(cand_entry, list)
                        and len(cand_entry) > 4
                        and isinstance(cand_entry[4], list)
                    ):
                        c_sec = cand_entry[4][0]
                        c_nanos = cand_entry[4][1] if len(cand_entry[4]) > 1 else 0
                        c_dt = datetime.datetime.fromtimestamp(
                            c_sec + c_nanos / 1e9, tz=datetime.timezone.utc
                        )
                        candidate_history.append(
                            {
                                "candidate_id": (cand_entry[3] if len(cand_entry) > 3 else None),
                                "timestamp": c_dt.isoformat(),
                            }
                        )
                if candidate_history:
                    # Last entry is initial creation time, first is latest edit
                    model_timestamp = candidate_history[-1]["timestamp"]

            if len(entry[3]) > 31 and isinstance(entry[3][31], str):
                model_version = entry[3][31]
            elif len(entry[3]) > 21 and isinstance(entry[3][21], str):
                model_version = entry[3][21]

        user_prompt_data = {
            "text": user_text,
            "timestamp": user_timestamp,
        }
        if attachments:
            user_prompt_data["attachments"] = attachments

        model_resp_data = {
            "text": model_text,
            "timestamp": model_timestamp,
            "model_version": model_version,
            "edit_history": candidate_history,
        }
        if model_attachments:
            model_resp_data["attachments"] = model_attachments
        if search_queries:
            model_resp_data["search_queries"] = search_queries
        if web_search_grounding:
            model_resp_data["web_search_grounding"] = web_search_grounding

        turn_dict = {
            "turn_index": idx,
            "turn_id": turn_id,
            "conversation_id": conv_id,
            "user_prompt": user_prompt_data,
            "model_response": model_resp_data,
        }
        if parent_turn_id:
            turn_dict["parent_turn_id"] = parent_turn_id
        if custom_instructions:
            turn_dict["custom_instructions"] = custom_instructions

        turns.append(turn_dict)

    # Sort turns chronologically by ISO timestamp
    turns.sort(key=lambda t: t["user_prompt"]["timestamp"] or "")
    for i, t in enumerate(turns):
        t["turn_index"] = i

    return turns


def extract_turns_from_response_text(raw_response: str) -> List[Dict[str, Any]]:
    """Extracts and parses all hNvQHb envelope turns from raw batchexecute response text."""
    lines = raw_response.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith(")]}'"):
            continue
        if line.startswith("["):
            try:
                data = json.loads(line)
                if isinstance(data, list):
                    for item in data:
                        if (
                            isinstance(item, list)
                            and len(item) > 2
                            and item[0] == "wrb.fr"
                            and item[1] == "hNvQHb"
                        ):
                            payload_str = item[2]
                            if payload_str:
                                payload = json.loads(payload_str)
                                return parse_rpc_payload(payload)
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
    return []


def fetch_conversation_rpc(
    page: Page,
    conversation_id: str,
    title: Optional[str] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Invokes batchexecute inside the page context using window.fetch and returns structured JSON."""
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

    raw_response = page.evaluate(js_code, target_conv_id)
    turns = extract_turns_from_response_text(raw_response)

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


def extract_latest_timestamp(turns: List[Dict[str, Any]]) -> Optional[str]:
    """Finds the most recent ISO timestamp among user prompts and model responses across turns."""
    timestamps: List[datetime.datetime] = []
    for turn in turns:
        for role in ("user_prompt", "model_response"):
            ts_str = turn.get(role, {}).get("timestamp")
            if ts_str and isinstance(ts_str, str):
                try:
                    dt = datetime.datetime.fromisoformat(ts_str)
                    timestamps.append(dt)
                except (ValueError, TypeError):
                    pass
    if not timestamps:
        return None
    latest_dt = max(timestamps)
    return latest_dt.isoformat()


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
