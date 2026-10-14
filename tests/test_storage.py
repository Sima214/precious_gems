"""Unit tests for storage and manifest operations."""

import json
from pathlib import Path
from precious_gems.storage import Manifest, slugify, save_conversation


def test_slugify():
    assert slugify("Simple Title") == "Simple_Title"
    assert slugify("Title with / and : special chars!") == "Title_with_and_special_chars"
    assert slugify("   leading and trailing   ") == "leading_and_trailing"
    assert slugify("Ελληνικός τίτλος συνομιλίας") == "Ελληνικός_τίτλος_συνομιλίας"
    assert slugify("") == "untitled"


def test_parse_size():
    from precious_gems.storage import parse_size
    assert parse_size(None) is None
    assert parse_size("") is None
    assert parse_size("1000") == 1000
    assert parse_size("500b") == 500
    assert parse_size("10KB") == 10 * 1024
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("1.5gb") == int(1.5 * 1024 * 1024 * 1024)
    assert parse_size("2GiB") == 2 * 1024 * 1024 * 1024


def test_manifest_lifecycle(tmp_path: Path):
    manifest = Manifest(tmp_path)
    assert not manifest.has_conversation("conv1")

    record = save_conversation(
        output_dir=tmp_path,
        conversation_id="conv1",
        title="Test Conversation",
        url="https://gemini.google.com/app/conv1",
        html_content="<html><body>Hello World</body></html>",
        manifest=manifest,
    )

    assert manifest.has_conversation("conv1")
    assert record.export_filename == "conv1_Test_Conversation"
    assert record.format == "html"

    # Verify HTML file was written inside the conversation folder
    html_file = tmp_path / record.export_filename / "conversation.html"
    assert html_file.exists()
    assert html_file.read_text(encoding="utf-8") == "<html><body>Hello World</body></html>"

    # Verify Meta file was written inside the conversation folder without self-referential meta_file
    meta_file = tmp_path / record.export_filename / "meta.json"
    assert meta_file.exists()
    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_data["id"] == "conv1"
    assert meta_data["title"] == "Test Conversation"
    assert meta_data["sha256"] == record.sha256
    assert "meta_file" not in meta_data
    assert "order_index" not in meta_data

    # Verify manifest was reloaded properly
    reloaded_manifest = Manifest(tmp_path)
    assert reloaded_manifest.has_conversation("conv1")
    assert reloaded_manifest.conversations["conv1"]["sha256"] == record.sha256


def test_save_conversation_json(tmp_path: Path):
    from precious_gems.storage import save_conversation_json

    manifest = Manifest(tmp_path)
    conv_data = {
        "id": "conv2",
        "title": "JSON Conversation",
        "turn_count": 1,
        "turns": [{"turn_index": 0, "turn_id": "r_123"}]
    }

    record = save_conversation_json(
        output_dir=tmp_path,
        conversation_id="conv2",
        title="JSON Conversation",
        url="https://gemini.google.com/app/conv2",
        conversation_data=conv_data,
        manifest=manifest,
    )

    assert manifest.has_conversation("conv2")
    assert record.export_filename == "conv2_JSON_Conversation"
    assert record.format == "json"

    json_file = tmp_path / record.export_filename / "conversation.json"
    assert json_file.exists()
    loaded_data = json.loads(json_file.read_text(encoding="utf-8"))
    assert loaded_data["turn_count"] == 1
    assert loaded_data["turns"][0]["turn_id"] == "r_123"

    meta_file = tmp_path / record.export_filename / "meta.json"
    assert meta_file.exists()
    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_data["format"] == "json"
    assert meta_data["export_filename"] == "conv2_JSON_Conversation"
    assert "meta_file" not in meta_data
    assert "order_index" not in meta_data


def test_catalog_lifecycle(tmp_path: Path):
    from precious_gems.storage import Catalog, CatalogItem

    catalog = Catalog(tmp_path)
    assert len(catalog.items) == 0

    items = [
        CatalogItem(id="c1", title="Title One", url="https://gemini.google.com/app/c1"),
        CatalogItem(id="c2", title="Title Two", url="https://gemini.google.com/app/c2"),
    ]
    catalog.update(items)
    catalog.save()

    catalog_file = tmp_path / "conversations.json"
    assert catalog_file.exists()
    catalog_json = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert "order" not in catalog_json
    assert "order_index" not in catalog_json["conversations"][0]

    reloaded = Catalog(tmp_path)
    assert len(reloaded.items) == 2
    assert reloaded.items[0].id == "c1"
    assert reloaded.items[1].title == "Title Two"

    # Test update with overlap
    catalog.update([
        CatalogItem(id="c0", title="Title Zero", url="https://gemini.google.com/app/c0"),
        CatalogItem(id="c1", title="Updated One", url="https://gemini.google.com/app/c1"),
    ])
    assert len(catalog.items) == 3
    assert catalog.items[0].id == "c0"
    assert catalog.items[1].title == "Updated One"


def test_detect_order_jumps():
    from precious_gems.storage import detect_order_jumps

    # Identical orders
    assert detect_order_jumps(["a", "b", "c"], ["a", "b", "c"]) == set()

    # Insertion at top: downstream items shift, but no jumps
    assert detect_order_jumps(["a", "b", "c"], ["new", "a", "b", "c"]) == set()

    # Insertion in middle
    assert detect_order_jumps(["a", "b", "c"], ["a", "new", "b", "c"]) == set()

    # Deletion of item: no jumps
    assert detect_order_jumps(["a", "b", "c", "d"], ["a", "c", "d"]) == set()

    # Single jump to top: 'c' moved before 'a' and 'b'
    assert detect_order_jumps(["a", "b", "c"], ["c", "a", "b"]) == {"c"}

    # Jump in the middle: 'd' jumped ahead of 'b' and 'c'
    assert detect_order_jumps(["a", "b", "c", "d"], ["a", "d", "b", "c"]) == {"d"}

    # Multiple jumps
    # 'e' (pos 4) jumped to top, 'c' (pos 2) jumped ahead of 'a' (pos 0) and 'b' (pos 1)
    prev = ["a", "b", "c", "d", "e"]
    curr = ["e", "c", "a", "b", "d"]
    assert detect_order_jumps(prev, curr) == {"e", "c"}

    # Empty cases
    assert detect_order_jumps([], ["a", "b"]) == set()
    assert detect_order_jumps(["a", "b"], []) == set()


def test_manifest_order_persistence(tmp_path: Path):
    manifest = Manifest(tmp_path)
    save_conversation(
        output_dir=tmp_path,
        conversation_id="conv1",
        title="First Conversation",
        url="https://gemini.google.com/app/conv1",
        html_content="<p>1</p>",
        manifest=manifest,
    )
    save_conversation(
        output_dir=tmp_path,
        conversation_id="conv2",
        title="Second Conversation",
        url="https://gemini.google.com/app/conv2",
        html_content="<p>2</p>",
        manifest=manifest,
    )

    assert manifest.order == ["conv1", "conv2"]

    reloaded = Manifest(tmp_path)
    assert reloaded.order == ["conv1", "conv2"]

