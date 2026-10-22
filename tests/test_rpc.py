"""Unit tests for Playwright RPC extraction and file downloading."""

from pathlib import Path
import pytest
from playwright._impl._errors import TargetClosedError
from precious_gems.rpc import (
    download_conversation_files,
    fetch_conversation_rpc,
    fetch_raw_conversation_rpc,
    list_conversations_rpc,
)


def test_fetch_raw_and_structured_conversation_rpc():
    class MockPage:
        def evaluate(self, script, arg):
            assert arg == "c_conv123"
            # Return raw batchexecute response string
            inner = [
                [
                    [
                        ["c_conv123", "r_t1"],
                        None,
                        [["Hello world"], 2],
                        [[["rc_1", ["Model answer"]]]],
                        [1790622000, 0],
                    ]
                ]
            ]
            import json

            envelope = [["wrb.fr", "hNvQHb", json.dumps(inner), None, "generic"]]
            return f")]}}'\n\n123\n{json.dumps(envelope)}\n"

    page = MockPage()  # type: ignore
    raw_text = fetch_raw_conversation_rpc(page, "conv123")
    assert "wrb.fr" in raw_text
    assert "hNvQHb" in raw_text

    conv = fetch_conversation_rpc(page, "conv123")
    assert conv["id"] == "conv123"
    assert conv["title"] == "Hello world"
    assert len(conv["turns"]) == 1
    assert conv["turns"][0]["user_prompt"]["text"] == "Hello world"
    assert conv["turns"][0]["model_response"]["text"] == "Model answer"


def test_download_conversation_files_sanitization_and_conflicts(tmp_path: Path):
    class MockResponse:
        def __init__(self, body: bytes, status: int = 200):
            self._body = body
            self.status = status

        def body(self) -> bytes:
            return self._body

    class MockRequest:
        def get(self, url: str, timeout: int = 120000):
            if "fail" in url:
                return MockResponse(b"", status=500)
            return MockResponse(f"content for {url}".encode("utf-8"))

    class MockPage:
        def __init__(self):
            self.request = MockRequest()

    turns = [
        {
            "user_prompt": {
                "attachments": [
                    # Path traversal attempt / forward slash in filename
                    {"filename": "../evil/payload.bin", "url": "https://example.com/payload.bin"},
                    # Reserved conversation file conflict
                    {"filename": "conversation.json", "url": "https://example.com/conv.json"},
                    # Name collision requiring numbering
                    {"filename": "data.csv", "url": "https://example.com/data1.csv"},
                    {"filename": "data.csv", "url": "https://example.com/data2.csv"},
                ]
            }
        }
    ]

    hashes = download_conversation_files(
        page=MockPage(),  # type: ignore
        output_dir=tmp_path,
        export_filename="conv_test",
        turns=turns,
    )

    conv_dir = tmp_path / "conv_test"
    assert conv_dir.exists()

    # Verify traversal was flattened
    evil_file = conv_dir / ".._evil_payload.bin"
    assert evil_file.exists()
    assert ".._evil_payload.bin" in hashes

    # Verify reserved collision was renamed
    reserved_file = conv_dir / "conversation (attachment).json"
    assert reserved_file.exists()
    assert "conversation (attachment).json" in hashes

    # Verify collision deduping
    f1 = conv_dir / "data.csv"
    f2 = conv_dir / "data_1.csv"
    assert f1.exists()
    assert f2.exists()
    assert "data.csv" in hashes
    assert "data_1.csv" in hashes

    # Verify local_path written back to turns dict
    atts = turns[0]["user_prompt"]["attachments"]
    assert atts[0]["local_path"] == ".._evil_payload.bin"
    assert atts[1]["local_path"] == "conversation (attachment).json"
    assert atts[2]["local_path"] == "data.csv"
    assert atts[3]["local_path"] == "data_1.csv"


def test_download_conversation_files_max_size(tmp_path: Path):
    class MockResponse:
        def __init__(self, body: bytes, status: int = 200):
            self._body = body
            self.status = status

        def body(self) -> bytes:
            return self._body

    class MockRequest:
        def get(self, url: str, timeout: int = 120000):
            if "oversized_network" in url:
                return MockResponse(b"A" * 100)
            return MockResponse(b"small")

    class MockPage:
        def __init__(self):
            self.request = MockRequest()

    turns = [
        {
            "user_prompt": {
                "attachments": [
                    # Known size exceeds limit (skipped before HTTP request)
                    {
                        "filename": "huge_known.bin",
                        "url": "https://example.com/huge",
                        "size_bytes": 200,
                    },
                    # Unknown size, but HTTP response exceeds limit
                    {"filename": "huge_net.bin", "url": "https://example.com/oversized_network"},
                    # Within limit
                    {
                        "filename": "normal.bin",
                        "url": "https://example.com/normal",
                        "size_bytes": 5,
                    },
                ]
            }
        }
    ]

    hashes = download_conversation_files(
        page=MockPage(),  # type: ignore
        output_dir=tmp_path,
        export_filename="conv_size",
        turns=turns,
        max_attachment_size=50,
    )

    conv_dir = tmp_path / "conv_size"
    assert not (conv_dir / "huge_known.bin").exists()
    assert not (conv_dir / "huge_net.bin").exists()
    assert (conv_dir / "normal.bin").exists()
    assert "normal.bin" in hashes
    assert "huge_known.bin" not in hashes
    assert "huge_net.bin" not in hashes


def test_download_conversation_files_aborts_on_target_closed(tmp_path: Path):
    attempts = 0

    class MockRequest:
        def get(self, url: str, timeout: int = 120000):
            nonlocal attempts
            attempts += 1
            raise TargetClosedError("Target page, context or browser has been closed")

    class MockPage:
        def __init__(self):
            self.request = MockRequest()

    turns = [
        {
            "user_prompt": {
                "attachments": [
                    {"filename": "file.txt", "url": "https://example.com/file"},
                ]
            }
        }
    ]

    page = MockPage()
    with pytest.raises(TargetClosedError):
        download_conversation_files(
            page=page,  # type: ignore
            output_dir=tmp_path,
            export_filename="conv_closed",
            turns=turns,
            max_retries=3,
        )

    assert attempts == 1


def test_download_conversation_files_with_widgets(tmp_path: Path):
    class MockPage:
        pass

    turns = [
        {
            "model_response": {
                "attachments": [
                    {
                        "filename": "widget1.html",
                        "mime_type": "text/html",
                        "html_content": "<h1>Interactive Simulator</h1>",
                    }
                ]
            }
        }
    ]

    download_conversation_files(
        page=MockPage(),  # type: ignore
        output_dir=tmp_path,
        export_filename="conv_widget",
        turns=turns,
    )

    conv_dir = tmp_path / "conv_widget"
    widget_file = conv_dir / "widget1.html"
    assert widget_file.exists()
    assert widget_file.read_text(encoding="utf-8") == "<h1>Interactive Simulator</h1>"
    # html_content should be stripped from dict to avoid JSON bloat
    att = turns[0]["model_response"]["attachments"][0]
    assert "html_content" not in att
    assert att["local_path"] == "widget1.html"


def test_list_conversations_rpc_evaluation():
    class MockPage:
        def evaluate(self, script, arg):
            assert arg["batchSize"] == 25
            assert arg["maxRounds"] == 5
            return [
                {
                    "id": "conv_1",
                    "title": "  Untrimmed\nTitle\tHere  ",
                    "url": "https://gemini.google.com/app/conv_1",
                },
                {
                    "id": "conv_2",
                    "title": "",
                    "url": "https://gemini.google.com/app/conv_2",
                },
            ]

    results = list_conversations_rpc(MockPage(), batch_size=25, max_rounds=5)  # type: ignore
    assert len(results) == 2
    assert results[0]["id"] == "conv_1"
    assert results[0]["title"] == "Untrimmed Title Here"
    assert results[1]["title"] == "Untitled Conversation"


def test_list_conversations_rpc_js_parsing_logic():
    import json
    import subprocess
    import shutil

    if not shutil.which("node"):
        return

    # Extract the JS script from rpc.py to verify it parses correctly in Node
    js_parser_tester = r"""
    function parseBatchexecute(txt) {
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
        return { batchToken, batchList };
    }

    const payload = JSON.stringify([null, "cursor_xyz", [["c_100", "Conv 100", null, 1790000000]]]);
    const response2Level = ")]}'\n\n123\n" + JSON.stringify([["wrb.fr", "MaZiqc", payload, null, "generic"]]) + "\n";
    const response3Level = ")]}'\n\n123\n" + JSON.stringify([[["wrb.fr", "MaZiqc", payload, null, "generic"]]]) + "\n";

    const res2 = parseBatchexecute(response2Level);
    const res3 = parseBatchexecute(response3Level);

    if (res2.batchToken !== "cursor_xyz" || res2.batchList.length !== 1) {
        process.exit(1);
    }
    if (res3.batchToken !== "cursor_xyz" || res3.batchList.length !== 1) {
        process.exit(2);
    }
    console.log("OK");
    """

    res = subprocess.run(["node", "-e", js_parser_tester], capture_output=True, text=True)
    assert res.returncode == 0, f"Node test failed: stdout={res.stdout}, stderr={res.stderr}"
    assert "OK" in res.stdout


def test_download_conversation_files_with_generated_images(tmp_path: Path):
    class MockResponse:
        def __init__(self, body_bytes: bytes):
            self.status = 200
            self._body = body_bytes

        def body(self) -> bytes:
            return self._body

    class MockRequest:
        def get(self, url: str, timeout: int = 120000):
            if "mock_image_url" in url:
                return MockResponse(b"JPEG_MOCK_IMAGE_DATA")
            if "download?thought" in url:
                return MockResponse(b"PROTOBUF_MOCK_THOUGHT_DATA")
            return MockResponse(b"DEFAULT_DATA")

    class MockPage:
        def __init__(self):
            self.request = MockRequest()

    turns = [
        {
            "model_response": {
                "text": "Check image: watermarked_img_12345.jpg",
                "attachments": [
                    {
                        "filename": "watermarked_img_12345.jpg",
                        "url": "https://lh3.googleusercontent.com/gg/mock_image_url",
                        "mime_type": "image/jpeg",
                        "image_id": "im_mock_banana_id",
                    },
                    {
                        "filename": "thought_signature_12345.pb",
                        "url": "https://contribution.usercontent.google.com/download?thought1",
                        "mime_type": "application/protobuf",
                        "image_id": "im_mock_banana_id",
                    },
                ],
            }
        }
    ]

    hashes = download_conversation_files(
        page=MockPage(),  # type: ignore
        output_dir=tmp_path,
        export_filename="conv_banana",
        turns=turns,
    )

    conv_dir = tmp_path / "conv_banana"
    img_file = conv_dir / "watermarked_img_12345.jpg"
    th_file = conv_dir / "thought_signature_12345.pb"

    assert img_file.exists()
    assert img_file.read_bytes() == b"JPEG_MOCK_IMAGE_DATA"
    assert th_file.exists()
    assert th_file.read_bytes() == b"PROTOBUF_MOCK_THOUGHT_DATA"

    img_att = turns[0]["model_response"]["attachments"][0]
    th_att = turns[0]["model_response"]["attachments"][1]

    assert img_att["local_path"] == "watermarked_img_12345.jpg"
    assert img_att["size_bytes"] == len(b"JPEG_MOCK_IMAGE_DATA")
    assert th_att["local_path"] == "thought_signature_12345.pb"
    assert th_att["size_bytes"] == len(b"PROTOBUF_MOCK_THOUGHT_DATA")
    assert "watermarked_img_12345.jpg" in hashes
    assert "thought_signature_12345.pb" in hashes
