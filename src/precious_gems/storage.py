"""Storage and state tracking for conversation exports."""

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def slugify(text: str, max_length: int = 60) -> str:
    """Generate a clean filesystem-friendly slug from title, preserving alphanumeric chars."""
    if not text:
        return "untitled"
    # Replace whitespace and problematic characters with underscores
    cleaned = re.sub(r"[^\w\-.]+", "_", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    if not cleaned:
        return "untitled"
    return cleaned[:max_length].rstrip("._")


def parse_size(size_str: Optional[str]) -> Optional[int]:
    """Parses human-readable size strings (e.g., '50MB', '500K', '1.5GiB', '1000') to bytes."""
    if not size_str:
        return None
    s = size_str.strip().upper()
    units = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "KIB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "MIB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "GIB": 1024 * 1024 * 1024,
        "T": 1024 * 1024 * 1024 * 1024,
        "TB": 1024 * 1024 * 1024 * 1024,
        "TIB": 1024 * 1024 * 1024 * 1024,
    }
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Z]*)$", s)
    if not m:
        raise ValueError(f"Invalid size string: '{size_str}'. Expected format like '50MB', '500KB', '1000'.")
    val_str, unit_str = m.groups()
    val = float(val_str)
    multiplier = units.get(unit_str, 1) if unit_str else 1
    return int(val * multiplier)


@dataclass
class ConversationRecord:
    id: str
    title: str
    url: str
    export_filename: str
    exported_at: str
    size_bytes: int
    sha256: str
    format: str = "json"


class Manifest:
    """Manages the central manifest.json tracking exported conversation state."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.manifest_path = self.output_dir / "manifest.json"
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.order: list[str] = []
        self.version = 1
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.version = data.get("version", 1)
            self.conversations = data.get("conversations", {})
            self.order = data.get("order", list(self.conversations.keys()))
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Corrupted manifest at {self.manifest_path}") from err

    def has_conversation(self, conversation_id: str) -> bool:
        return conversation_id in self.conversations

    def get_record(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.conversations.get(conversation_id)

    def add_record(self, record: ConversationRecord) -> None:
        self.conversations[record.id] = asdict(record)
        if record.id not in self.order:
            self.order.append(record.id)

    def set_order(self, order: list[str]) -> None:
        self.order = list(order)

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "order": self.order,
            "conversations": self.conversations,
        }
        temp_path = self.manifest_path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, self.manifest_path)


def save_conversation(
    output_dir: Path,
    conversation_id: str,
    title: str,
    url: str,
    html_content: str,
    manifest: Manifest,
) -> ConversationRecord:
    """Saves conversation HTML, companion metadata, and updates the manifest atomically inside the conversation folder."""
    slug = slugify(title)
    base_filename = f"{conversation_id}_{slug}"
    conv_dir = output_dir / base_filename
    conv_dir.mkdir(parents=True, exist_ok=True)

    html_filename = "conversation.html"
    meta_filename = "meta.json"

    html_path = conv_dir / html_filename
    meta_path = conv_dir / meta_filename

    html_bytes = html_content.encode("utf-8")
    sha256_hash = hashlib.sha256(html_bytes).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Atomic write for HTML
    temp_html = html_path.with_suffix(".html.tmp")
    with open(temp_html, "wb") as f:
        f.write(html_bytes)
    os.replace(temp_html, html_path)

    record = ConversationRecord(
        id=conversation_id,
        title=title,
        url=url,
        export_filename=base_filename,
        exported_at=now_iso,
        size_bytes=len(html_bytes),
        sha256=sha256_hash,
        format="html",
    )

    # Atomic write for metadata
    temp_meta = meta_path.with_suffix(".meta.json.tmp")
    with open(temp_meta, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(temp_meta, meta_path)

    manifest.add_record(record)
    manifest.save()

    return record


def save_conversation_json(
    output_dir: Path,
    conversation_id: str,
    title: str,
    url: str,
    conversation_data: Dict[str, Any],
    manifest: Manifest,
) -> ConversationRecord:
    """Saves conversation JSON, companion metadata, and updates the manifest atomically inside the conversation folder."""
    slug = slugify(title)
    base_filename = f"{conversation_id}_{slug}"
    conv_dir = output_dir / base_filename
    conv_dir.mkdir(parents=True, exist_ok=True)

    json_filename = "conversation.json"
    meta_filename = "meta.json"

    json_path = conv_dir / json_filename
    meta_path = conv_dir / meta_filename

    json_text = json.dumps(conversation_data, indent=2, ensure_ascii=False) + "\n"
    json_bytes = json_text.encode("utf-8")
    sha256_hash = hashlib.sha256(json_bytes).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Atomic write for JSON
    temp_json = json_path.with_suffix(".json.tmp")
    with open(temp_json, "wb") as f:
        f.write(json_bytes)
    os.replace(temp_json, json_path)

    record = ConversationRecord(
        id=conversation_id,
        title=title,
        url=url,
        export_filename=base_filename,
        exported_at=now_iso,
        size_bytes=len(json_bytes),
        sha256=sha256_hash,
        format="json",
    )

    # Atomic write for metadata
    temp_meta = meta_path.with_suffix(".meta.json.tmp")
    with open(temp_meta, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(temp_meta, meta_path)

    manifest.add_record(record)
    manifest.save()

    return record


def detect_order_jumps(previous_order: list[str], current_order: list[str]) -> set[str]:
    """Identifies conversation IDs in current_order that jumped ahead of items
    previously situated above them in previous_order.
    """
    prev_pos = {cid: idx for idx, cid in enumerate(previous_order)}
    jumped_ids: set[str] = set()

    min_prev_pos_after = float("inf")
    common_in_current = [cid for cid in current_order if cid in prev_pos]

    for cid in reversed(common_in_current):
        pos = prev_pos[cid]
        if pos > min_prev_pos_after:
            jumped_ids.add(cid)
        else:
            min_prev_pos_after = pos

    return jumped_ids


@dataclass
class CatalogItem:
    id: str
    title: str
    url: str


class Catalog:
    """Manages conversations.json caching all discovered sidebar conversation IDs and titles."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.catalog_path = self.output_dir / "conversations.json"
        self.items: list[CatalogItem] = []
        self.version = 1
        self._load()

    def _load(self) -> None:
        if not self.catalog_path.exists():
            return
        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.version = data.get("version", 1)
            raw_items = data.get("conversations", [])
            self.items = [
                CatalogItem(
                    id=item["id"],
                    title=item["title"],
                    url=item["url"],
                )
                for item in raw_items
                if "id" in item
            ]
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Corrupted catalog at {self.catalog_path}") from err

    @property
    def conversation_ids(self) -> list[str]:
        return [item.id for item in self.items]

    def update(self, conversations: list[CatalogItem]) -> None:
        """Updates catalog preserving new items in order and maintaining existing items."""
        seen = set()
        merged: list[CatalogItem] = []
        for c in conversations:
            if c.id not in seen:
                seen.add(c.id)
                merged.append(c)
        for existing in self.items:
            if existing.id not in seen:
                seen.add(existing.id)
                merged.append(existing)
        self.items = merged

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total": len(self.items),
            "conversations": [asdict(item) for item in self.items],
        }
        temp_path = self.catalog_path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, self.catalog_path)

