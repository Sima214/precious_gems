"""Storage and state tracking for conversation exports."""

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def normalize_title(text: Optional[str]) -> str:
    """Normalizes whitespace and newlines in conversation titles into single spaces."""
    if not text:
        return "Untitled Conversation"
    cleaned = re.sub(r"[\r\n\t]+", " ", str(text)).strip()
    return re.sub(r"\s+", " ", cleaned) or "Untitled Conversation"


def slugify(text: str, max_length: int = 40) -> str:
    """Generate a clean filesystem-friendly slug from title, preserving alphanumeric chars and breaking at word boundaries."""
    if not text:
        return "untitled"
    # Replace whitespace and problematic characters with underscores
    cleaned = re.sub(r"[^\w\-.]+", "_", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    if not cleaned:
        return "untitled"
    if len(cleaned) <= max_length:
        return cleaned.rstrip("._")
    truncated = cleaned[:max_length]
    # Break at last word boundary (_, -, .) before max_length if possible
    last_delim = max(truncated.rfind("_"), truncated.rfind("-"), truncated.rfind("."))
    if last_delim > 0:
        return truncated[:last_delim].rstrip("._")
    return truncated.rstrip("._")


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
        raise ValueError(
            f"Invalid size string: '{size_str}'. Expected format like '50MB', '500KB', '1000'."
        )
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
    attachments: Dict[str, str] = field(default_factory=dict)
    turn_timestamp: Optional[str] = None


class Manifest:
    """Manages the central manifest.json tracking exported conversation state."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.manifest_path = self.output_dir / "manifest.json"
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.order: list[str] = []
        self.version = 1
        self._load()
        self.rescan_filesystem_locations()

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

    def rescan_filesystem_locations(self) -> None:
        """Recursively scans output_dir to detect moved conversation directories containing meta.json.
        Fails fast if duplicate folders for the same conversation ID are detected.
        """
        if not self.output_dir.exists() or not self.output_dir.is_dir():
            return

        seen_locations: Dict[str, Path] = {}
        found_convs: Dict[str, tuple[str, Dict[str, Any]]] = {}

        for meta_path in self.output_dir.rglob("meta.json"):
            if not meta_path.is_file():
                continue
            conv_dir = meta_path.parent
            if conv_dir == self.output_dir:
                continue
            if any(part.endswith(".tmp") for part in conv_dir.parts):
                continue

            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
            except (json.JSONDecodeError, OSError) as err:
                raise RuntimeError(f"Corrupted metadata file at {meta_path}") from err

            conv_id = meta_data.get("id")
            if not conv_id:
                continue

            if conv_id in seen_locations:
                first_path = seen_locations[conv_id].relative_to(self.output_dir).as_posix()
                second_path = conv_dir.relative_to(self.output_dir).as_posix()
                raise RuntimeError(
                    f"Duplicate conversation folder detected for ID '{conv_id}': "
                    f"'{first_path}' and '{second_path}'"
                )

            seen_locations[conv_id] = conv_dir
            rel_path = conv_dir.relative_to(self.output_dir).as_posix()
            found_convs[conv_id] = (rel_path, meta_data)

        dirty = False
        for conv_id, (rel_path, meta_data) in found_convs.items():
            if conv_id in self.conversations:
                if self.conversations[conv_id].get("export_filename") != rel_path:
                    self.conversations[conv_id]["export_filename"] = rel_path
                    dirty = True
            else:
                meta_data["export_filename"] = rel_path
                self.conversations[conv_id] = meta_data
                if conv_id not in self.order:
                    self.order.append(conv_id)
                dirty = True

        if dirty:
            self.save()

    def verify_conversation_integrity(self, conversation_id: str) -> tuple[bool, str]:
        """Verifies on-disk files and SHA-256 hashes against manifest records.
        Filesystem-bound: returns (is_valid, reason).
        """
        if conversation_id not in self.conversations:
            return False, "not_in_manifest"

        rec = self.conversations[conversation_id]
        export_filename = rec.get("export_filename")
        if not export_filename:
            return False, "missing_export_filename"

        conv_dir = self.output_dir / export_filename
        if not conv_dir.exists() or not conv_dir.is_dir():
            return False, "missing_directory"

        fmt = rec.get("format", "json")
        primary_name = "conversation.html" if fmt == "html" else "conversation.json"
        primary_file = conv_dir / primary_name
        if not primary_file.exists() or not primary_file.is_file():
            return False, f"missing_file_{primary_name}"

        try:
            with open(primary_file, "rb") as f:
                primary_sha = hashlib.sha256(f.read()).hexdigest()
        except OSError as err:
            return False, f"unreadable_{primary_name}_{err}"

        if rec.get("sha256") and primary_sha != rec["sha256"]:
            return False, f"hash_mismatch_{primary_name}"

        attachments = rec.get("attachments") or {}
        for att_name, expected_sha in attachments.items():
            att_file = conv_dir / att_name
            if not att_file.exists() or not att_file.is_file():
                return False, f"missing_attachment_{att_name}"
            try:
                with open(att_file, "rb") as f:
                    att_sha = hashlib.sha256(f.read()).hexdigest()
            except OSError as err:
                return False, f"unreadable_attachment_{att_name}_{err}"
            if expected_sha and att_sha != expected_sha:
                return False, f"hash_mismatch_{att_name}"

        return True, "ok"

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

    def mark_deleted_upstream(self, conversation_id: str, timestamp: Optional[str] = None) -> bool:
        if conversation_id not in self.conversations:
            return False
        self.conversations[conversation_id]["deleted_upstream"] = True
        self.conversations[conversation_id]["deleted_upstream_at"] = (
            timestamp or datetime.now(timezone.utc).isoformat()
        )
        return True

    def delete_conversation(self, conversation_id: str, delete_files: bool = False) -> bool:
        if conversation_id not in self.conversations:
            return False
        record = self.conversations.pop(conversation_id)
        if conversation_id in self.order:
            self.order.remove(conversation_id)
        if delete_files:
            export_filename = record.get("export_filename")
            if export_filename:
                conv_dir = self.output_dir / export_filename
                if conv_dir.exists() and conv_dir.is_dir():
                    shutil.rmtree(conv_dir)
        return True

    def is_up_to_date(self, conversation_id: str, catalog: Optional["Catalog"] = None) -> bool:
        if conversation_id not in self.conversations:
            return False
        rec = self.conversations[conversation_id]

        is_intact, _ = self.verify_conversation_integrity(conversation_id)
        if not is_intact:
            return False

        if catalog is not None and catalog.items:
            cat_title = catalog.get_title(conversation_id)
            if (
                cat_title
                and rec.get("title")
                and normalize_title(rec.get("title")) != normalize_title(cat_title)
            ):
                return False
            if (
                len(self.order) > 2
                and len(catalog.conversation_ids) > 2
                and set(self.order) == set(catalog.conversation_ids)
            ):
                jumped = detect_order_jumps(self.order, catalog.conversation_ids)
                if conversation_id in jumped:
                    return False

        return True

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


def apply_filesystem_timestamps(conv_dir: Path, timestamp_str: Optional[str]) -> None:
    """Sets filesystem atime and mtime on all files inside conv_dir and the directory itself."""
    if not timestamp_str or not conv_dir.exists():
        return
    try:
        dt = datetime.fromisoformat(timestamp_str)
        epoch = dt.timestamp()
    except (ValueError, TypeError):
        return

    for item in conv_dir.rglob("*"):
        if item.is_file():
            try:
                os.utime(item, (epoch, epoch))
            except OSError:
                pass
    try:
        os.utime(conv_dir, (epoch, epoch))
    except OSError:
        pass


def resolve_conversation_dir(
    output_dir: Path,
    conversation_id: str,
    title: str,
    manifest: Manifest,
) -> tuple[Path, str]:
    """Determines target folder and relative export_filename, renaming leaf folder if title changed."""
    slug = slugify(normalize_title(title))
    leaf_name = f"{conversation_id}_{slug}"

    if manifest.has_conversation(conversation_id):
        rec = manifest.get_record(conversation_id) or {}
        old_rel = rec.get("export_filename")
        if old_rel:
            old_path = output_dir / old_rel
            if old_path.exists() and old_path.is_dir():
                parent_dir = old_path.parent
                new_conv_dir = parent_dir / leaf_name
                if old_path != new_conv_dir:
                    old_path.rename(new_conv_dir)
                rel_path = new_conv_dir.relative_to(output_dir).as_posix()
                return new_conv_dir, rel_path

    conv_dir = output_dir / leaf_name
    return conv_dir, leaf_name


def save_conversation(
    output_dir: Path,
    conversation_id: str,
    title: str,
    url: str,
    html_content: str,
    manifest: Manifest,
    turn_timestamp: Optional[str] = None,
) -> ConversationRecord:
    """Saves conversation HTML, companion metadata, and updates the manifest atomically inside the conversation folder."""
    norm_title = normalize_title(title)
    conv_dir, base_filename = resolve_conversation_dir(
        output_dir, conversation_id, norm_title, manifest
    )
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
        title=norm_title,
        url=url,
        export_filename=base_filename,
        exported_at=now_iso,
        size_bytes=len(html_bytes),
        sha256=sha256_hash,
        format="html",
        turn_timestamp=turn_timestamp,
    )

    # Atomic write for metadata
    temp_meta = meta_path.with_suffix(".meta.json.tmp")
    with open(temp_meta, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(temp_meta, meta_path)

    manifest.add_record(record)
    manifest.save()
    apply_filesystem_timestamps(conv_dir, turn_timestamp or now_iso)

    return record


def save_conversation_json(
    output_dir: Path,
    conversation_id: str,
    title: str,
    url: str,
    conversation_data: Dict[str, Any],
    manifest: Manifest,
    attachments: Optional[Dict[str, str]] = None,
    turn_timestamp: Optional[str] = None,
) -> ConversationRecord:
    """Saves conversation JSON, companion metadata, and updates the manifest atomically inside the conversation folder."""
    norm_title = normalize_title(title)
    conv_dir, base_filename = resolve_conversation_dir(
        output_dir, conversation_id, norm_title, manifest
    )
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
        title=norm_title,
        url=url,
        export_filename=base_filename,
        exported_at=now_iso,
        size_bytes=len(json_bytes),
        sha256=sha256_hash,
        format="json",
        attachments=attachments or {},
        turn_timestamp=turn_timestamp,
    )

    # Atomic write for metadata
    temp_meta = meta_path.with_suffix(".meta.json.tmp")
    with open(temp_meta, "w", encoding="utf-8") as f:
        json.dump(asdict(record), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(temp_meta, meta_path)

    manifest.add_record(record)
    manifest.save()
    apply_filesystem_timestamps(conv_dir, turn_timestamp or now_iso)

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
                    title=normalize_title(item.get("title")),
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
            norm_item = CatalogItem(id=c.id, title=normalize_title(c.title), url=c.url)
            if norm_item.id not in seen:
                seen.add(norm_item.id)
                merged.append(norm_item)
        for existing in self.items:
            norm_existing = CatalogItem(
                id=existing.id, title=normalize_title(existing.title), url=existing.url
            )
            if norm_existing.id not in seen:
                seen.add(norm_existing.id)
                merged.append(norm_existing)
        self.items = merged

    def remove(self, conversation_ids: list[str]) -> bool:
        """Removes items matching conversation_ids from catalog."""
        id_set = set(conversation_ids)
        orig_len = len(self.items)
        self.items = [item for item in self.items if item.id not in id_set]
        return len(self.items) != orig_len

    def get_title(self, conversation_id: str) -> Optional[str]:
        """Returns the title for conversation_id if present in catalog."""
        for item in self.items:
            if item.id == conversation_id:
                return normalize_title(item.title)
        return None

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
