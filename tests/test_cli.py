"""Unit tests for CLI parsing."""

from precious_gems.cli.entry import build_parser, DEFAULT_CDP_URL


def test_build_parser_export():
    parser = build_parser()
    args = parser.parse_args(["export", "12345"])
    assert args.command == "export"
    assert args.target == "12345"
    assert args.output_dir == "./output"
    assert args.cdp_url == DEFAULT_CDP_URL
    assert DEFAULT_CDP_URL == "http://127.0.0.1:9222"
    assert not args.force
    assert not args.html

    args_html = parser.parse_args(["export", "12345", "--html", "--max-attachment-size", "50MB"])
    assert args_html.html is True
    assert args_html.max_attachment_size == "50MB"


def test_build_parser_raw():
    parser = build_parser()
    args = parser.parse_args(
        ["raw", "12345", "--output-dir", "/tmp/raw_out", "--cdp-url", "http://127.0.0.1:9222"]
    )
    assert args.command == "raw"
    assert args.target == "12345"
    assert args.output_dir == "/tmp/raw_out"
    assert args.cdp_url == "http://127.0.0.1:9222"


def test_cmd_raw_execution(tmp_path):
    from unittest.mock import MagicMock, patch
    from precious_gems.cli.entry import cmd_raw
    import argparse

    mock_client = MagicMock()
    mock_client.get_raw_conversation.return_value = ")]}'\n\n123\nraw_test_data"

    with patch("precious_gems.cli.entry.GeminiClient") as MockClient:
        MockClient.return_value.__enter__.return_value = mock_client
        args = argparse.Namespace(
            target="https://gemini.google.com/app/c_abcdef12345",
            output_dir=str(tmp_path),
            cdp_url="http://127.0.0.1:9222",
        )
        ret = cmd_raw(args)
        assert ret == 0

        out_file = tmp_path / "abcdef12345_raw.txt"
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == ")]}'\n\n123\nraw_test_data"
        mock_client.get_raw_conversation.assert_called_once_with("abcdef12345")


def test_build_parser_sync():
    parser = build_parser()
    args = parser.parse_args(
        [
            "sync",
            "--limit",
            "10",
            "--delay",
            "1.5",
            "--cdp-url",
            "http://127.0.0.1:9222",
            "--html",
            "--max-attachment-size",
            "100MB",
        ]
    )
    assert args.command == "sync"
    assert args.limit == 10
    assert args.delay == 1.5
    assert args.cdp_url == "http://127.0.0.1:9222"
    assert args.html is True
    assert args.max_attachment_size == "100MB"


def test_build_parser_list():
    parser = build_parser()
    args = parser.parse_args(["list", "--output-dir", "/tmp/test", "--all"])
    assert args.command == "list"
    assert args.output_dir == "/tmp/test"
    assert args.all is True


def test_build_parser_scan():
    parser = build_parser()
    args = parser.parse_args(["scan", "--output-dir", "/tmp/scan", "--max-scrolls", "50"])
    assert args.command == "scan"
    assert args.output_dir == "/tmp/scan"
    assert args.max_scrolls == 50
    assert args.cdp_url == DEFAULT_CDP_URL


def test_build_parser_sync_from_cache():
    parser = build_parser()
    args = parser.parse_args(["sync", "--from-cache", "--limit", "5"])
    assert args.command == "sync"
    assert args.from_cache is True
    assert args.limit == 5


def test_cmd_list_output(tmp_path, capsys):
    from precious_gems.storage import Manifest, Catalog, CatalogItem, save_conversation
    from precious_gems.cli.entry import cmd_list

    manifest = Manifest(tmp_path)
    save_conversation(
        output_dir=tmp_path,
        conversation_id="c1",
        title="Title 1",
        url="https://gemini.google.com/app/c1",
        html_content="<p>1</p>",
        manifest=manifest,
    )

    catalog = Catalog(tmp_path)
    catalog.update(
        [
            CatalogItem(id="c1", title="Title 1", url="https://gemini.google.com/app/c1"),
            CatalogItem(id="c2", title="Title 2", url="https://gemini.google.com/app/c2"),
        ]
    )
    catalog.save()

    parser = build_parser()
    args = parser.parse_args(["list", "--output-dir", str(tmp_path), "--all"])
    exit_code = cmd_list(args)
    assert exit_code == 0

    captured = capsys.readouterr().out
    assert "[Exported] c1: Title 1" in captured
    assert "[Pending] c2: Title 2" in captured


def test_cmd_list_default_exported_only(tmp_path, capsys):
    from precious_gems.storage import Manifest, save_conversation
    from precious_gems.cli.entry import cmd_list

    manifest = Manifest(tmp_path)
    save_conversation(
        output_dir=tmp_path,
        conversation_id="c1",
        title="Title 1",
        url="https://gemini.google.com/app/c1",
        html_content="<p>1</p>",
        manifest=manifest,
    )

    parser = build_parser()
    args = parser.parse_args(["list", "--output-dir", str(tmp_path)])
    exit_code = cmd_list(args)
    assert exit_code == 0

    captured = capsys.readouterr().out
    assert "Exported conversations in" in captured
    assert "c1 (pos 0): Title 1" in captured


def test_max_scrolls_warning(capsys, monkeypatch):
    from precious_gems.cli.entry import cmd_scan

    parser = build_parser()
    args = parser.parse_args(["scan", "--max-scrolls", "20"])

    # Mock client to prevent actual network attempt
    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_conversations(self, **kwargs):
            return []

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", lambda **kwargs: DummyClient())
    cmd_scan(args)

    stderr = capsys.readouterr().err
    assert "[WARNING] --max-scrolls is ignored unless --html is specified." in stderr


def test_cli_keyboard_interrupt(capsys, monkeypatch):
    from precious_gems.cli.entry import main

    class InterruptClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_conversations(self, **kwargs):
            raise KeyboardInterrupt()

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", lambda **kwargs: InterruptClient())
    exit_code = main(["scan"])
    assert exit_code == 130
    stderr = capsys.readouterr().err
    assert "[INFO] Interrupted by user (Ctrl+C). Exiting." in stderr


def test_cli_target_closed_error(capsys, monkeypatch):
    from playwright._impl._errors import TargetClosedError
    from precious_gems.cli.entry import main

    class ClosedTabClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_conversations(self, **kwargs):
            raise TargetClosedError(
                "Page.query_selector_all: Target page, context or browser has been closed"
            )

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", lambda **kwargs: ClosedTabClient())
    exit_code = main(["sync"])
    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "[ERROR] Browser tab or page was closed by user. Exiting." in stderr


def test_browser_session_close_interrupted_and_dead_fiber(monkeypatch):
    from precious_gems.browser import BrowserSession

    http_closed_targets = []
    monkeypatch.setattr(
        BrowserSession,
        "_close_tab_via_http",
        lambda self, tid: http_closed_targets.append(tid),
    )

    class DeadFiber:
        dead = True

    class MockImpl:
        _dispatcher_fiber = DeadFiber()

    class MockPage:
        def __init__(self):
            self._impl_obj = MockImpl()
            self.close_called = False

        def close(self):
            self.close_called = True

    class MockProc:
        def __init__(self):
            self.returncode = None
            self.killed = False

        def kill(self):
            self.killed = True
            self.returncode = -9

    class MockTransport:
        def __init__(self):
            self._proc = MockProc()

    class MockConn:
        def __init__(self):
            self._transport = MockTransport()
            self._loop = None

    class MockPwImpl:
        def __init__(self):
            self._connection = MockConn()
            self._dispatcher_fiber = DeadFiber()

    class MockPlaywright:
        def __init__(self):
            self._impl_obj = MockPwImpl()
            self.stopped = False

        def stop(self):
            self.stopped = True

    session = BrowserSession("http://127.0.0.1:9222")
    mock_page = MockPage()
    mock_pw = MockPlaywright()
    session._page = mock_page  # type: ignore
    session._target_id = "target-123"
    session._playwright = mock_pw  # type: ignore

    # Even if interrupted=False is passed, dead fiber must skip page.close(), use HTTP close, and kill proc
    session.close(interrupted=False)

    assert not mock_page.close_called
    assert http_closed_targets == ["target-123"]
    assert mock_pw._impl_obj._connection._transport._proc.killed is True
    assert mock_pw.stopped is True


def test_client_list_conversations_propagates_target_closed_error(monkeypatch):
    import pytest
    from playwright._impl._errors import TargetClosedError
    from precious_gems.client import GeminiClient

    client = GeminiClient()
    client._page = object()  # type: ignore

    def mock_rpc(page):
        raise TargetClosedError("Target page, context or browser has been closed")

    sidebar_called = False

    def mock_sidebar(page, max_scrolls=30):
        nonlocal sidebar_called
        sidebar_called = True
        return []

    monkeypatch.setattr("precious_gems.client.list_conversations_rpc", mock_rpc)
    monkeypatch.setattr("precious_gems.client.enumerate_conversations", mock_sidebar)

    with pytest.raises(TargetClosedError):
        client.list_conversations(use_html_fallback=False)

    assert not sidebar_called


def test_client_list_conversations_empty_rpc_does_not_call_sidebar(monkeypatch):
    from precious_gems.client import GeminiClient

    client = GeminiClient()
    client._page = object()  # type: ignore

    sidebar_called = False

    def mock_rpc(page):
        return []

    def mock_sidebar(page, max_scrolls=30):
        nonlocal sidebar_called
        sidebar_called = True
        return []

    monkeypatch.setattr("precious_gems.client.list_conversations_rpc", mock_rpc)
    monkeypatch.setattr("precious_gems.client.enumerate_conversations", mock_sidebar)

    results = client.list_conversations(use_html_fallback=False)
    assert results == []
    assert not sidebar_called


def test_client_list_conversations_rpc_error_falls_back_with_warning(capsys, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError
    from precious_gems.client import GeminiClient
    from precious_gems.sidebar import SidebarConversation

    client = GeminiClient()
    client._page = object()  # type: ignore

    def mock_rpc(page):
        raise PlaywrightError("Evaluation failed: Error: Missing SNlM0e token")

    sidebar_called = False

    def mock_sidebar(page, max_scrolls=30):
        nonlocal sidebar_called
        sidebar_called = True
        return [
            SidebarConversation(
                id="sidebar_1", title="Sidebar Conv", url="https://gemini.google.com/app/sidebar_1"
            )
        ]

    monkeypatch.setattr("precious_gems.client.list_conversations_rpc", mock_rpc)
    monkeypatch.setattr("precious_gems.client.enumerate_conversations", mock_sidebar)

    results = client.list_conversations(use_html_fallback=False)
    assert len(results) == 1
    assert results[0].id == "sidebar_1"
    assert sidebar_called is True

    stderr = capsys.readouterr().err
    assert (
        "[WARNING] RPC conversation enumeration failed: Evaluation failed: Error: Missing SNlM0e token"
        in stderr
    )


def test_browser_session_real_playwright_sigint_teardown(monkeypatch):
    import os
    import signal
    import time
    from playwright.sync_api import sync_playwright
    from precious_gems.browser import BrowserSession

    pw = sync_playwright().start()
    loop = pw._impl_obj._loop

    async def slow_coro():
        await loop.create_future()

    session = BrowserSession("http://127.0.0.1:9222")
    session._playwright = pw
    session._page = pw.chromium  # type: ignore  # Has _impl_obj._dispatcher_fiber and _sync
    session._target_id = "dummy-target"
    monkeypatch.setattr(BrowserSession, "_close_tab_via_http", lambda self, tid: None)

    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, lambda s, f: os.kill(os.getpid(), signal.SIGINT))
        signal.setitimer(signal.ITIMER_REAL, 0.05)
        try:
            pw.chromium._sync(slow_coro())
        except KeyboardInterrupt:
            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, old_handler)

    # _dispatcher_fiber is now dead after KeyboardInterrupt unwound greenlet_main
    assert pw.chromium._dispatcher_fiber.dead is True

    t0 = time.monotonic()
    session.close(interrupted=True)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert session._playwright is None


def test_build_parser_delete():
    parser = build_parser()
    args = parser.parse_args(["delete", "c_12345", "https://gemini.google.com/app/67890"])
    assert args.command == "delete"
    assert args.targets == ["c_12345", "https://gemini.google.com/app/67890"]
    assert args.output_dir == "./output"
    assert args.cdp_url == DEFAULT_CDP_URL
    assert not args.delete_archived
    assert not args.force

    args_force = parser.parse_args(["delete", "123", "--delete-archived", "-f"])
    assert args_force.delete_archived is True
    assert args_force.force is True


def test_cmd_delete_flow(capsys, monkeypatch, tmp_path):
    from precious_gems.cli.entry import cmd_delete

    calls = []

    class DummyClient:
        def __init__(self, cdp_url, output_dir):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def delete_conversation(self, conversation_id, delete_archived=False, force=False):
            calls.append((conversation_id, delete_archived, force))
            if conversation_id == "c1":
                return {"id": "c1", "status": "deleted", "archived_deleted": delete_archived}
            elif conversation_id == "c2":
                return {"id": "c2", "status": "skipped", "reason": "unarchived_or_outdated"}
            else:
                return {"id": conversation_id, "status": "not_found"}

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", DummyClient)

    parser = build_parser()
    args = parser.parse_args(
        [
            "delete",
            "c_c1",
            "https://gemini.google.com/app/c2",
            "c3",
            "--output-dir",
            str(tmp_path),
            "--delete-archived",
        ]
    )

    exit_code = cmd_delete(args)
    assert exit_code == 0
    assert len(calls) == 3
    assert calls[0] == ("c1", True, False)
    assert calls[1] == ("c2", True, False)
    assert calls[2] == ("c3", True, False)

    captured = capsys.readouterr().out
    assert "Total processed:               3" in captured
    assert "Successfully deleted upstream: 1" in captured
    assert "Skipped (unarchived/outdated): 1" in captured
    assert "Not found on Gemini:           1" in captured
    assert "Local archives purged:         1" in captured


def test_cmd_sync_redownloads_on_integrity_hash_mismatch(capsys, monkeypatch, tmp_path):
    from precious_gems.cli.entry import build_parser, cmd_sync
    from precious_gems.storage import (
        Catalog,
        CatalogItem,
        ConversationRecord,
        Manifest,
        save_conversation_json,
    )

    manifest = Manifest(tmp_path)
    save_conversation_json(
        output_dir=tmp_path,
        conversation_id="c1",
        title="Title 1",
        url="https://gemini.google.com/app/c1",
        conversation_data={"turns": []},
        manifest=manifest,
    )

    catalog = Catalog(tmp_path)
    catalog.update([CatalogItem(id="c1", title="Title 1", url="https://gemini.google.com/app/c1")])
    catalog.save()

    # Corrupt conversation.json
    conv_file = tmp_path / "c1_Title_1" / "conversation.json"
    conv_file.write_text('{"tampered": true}\n', encoding="utf-8")

    exported_calls = []

    class DummyClient:
        def __init__(self, cdp_url, output_dir):
            self.manifest = manifest

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def export_conversation(self, conversation_id, title=None, **kwargs):
            exported_calls.append(conversation_id)
            return ConversationRecord(
                id=conversation_id,
                title=title or "Title 1",
                url="https://gemini.google.com/app/c1",
                export_filename=f"{conversation_id}_Title_1",
                exported_at="2026-10-16T00:00:00Z",
                size_bytes=100,
                sha256="abc",
            )

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", DummyClient)

    parser = build_parser()
    args = parser.parse_args(["sync", "--output-dir", str(tmp_path), "--from-cache"])
    exit_code = cmd_sync(args)

    assert exit_code == 0
    assert exported_calls == ["c1"]
    captured = capsys.readouterr().out
    assert "Verifying integrity for c1..." in captured
    assert "integrity check failed (hash_mismatch_conversation.json)" in captured


def test_cmd_sync_silently_skips_deleted_upstream(capsys, monkeypatch, tmp_path):
    from precious_gems.cli.entry import build_parser, cmd_sync
    from precious_gems.storage import Catalog, CatalogItem, Manifest, save_conversation_json

    manifest = Manifest(tmp_path)
    save_conversation_json(
        output_dir=tmp_path,
        conversation_id="c1",
        title="Title 1",
        url="https://gemini.google.com/app/c1",
        conversation_data={"turns": []},
        manifest=manifest,
    )
    manifest.mark_deleted_upstream("c1")
    manifest.save()

    catalog = Catalog(tmp_path)
    catalog.update([CatalogItem(id="c1", title="Title 1", url="https://gemini.google.com/app/c1")])
    catalog.save()

    exported_calls = []

    class DummyClient:
        def __init__(self, cdp_url, output_dir):
            self.manifest = manifest

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def export_conversation(self, conversation_id, **kwargs):
            exported_calls.append(conversation_id)

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", DummyClient)

    parser = build_parser()
    args = parser.parse_args(["sync", "--output-dir", str(tmp_path), "--from-cache"])
    exit_code = cmd_sync(args)

    assert exit_code == 0
    assert exported_calls == []
    captured = capsys.readouterr().out
    assert "c1" not in captured


def test_cmd_sync_ignores_multiline_whitespace_differences_in_title(capsys, monkeypatch, tmp_path):
    from precious_gems.cli.entry import build_parser, cmd_sync
    from precious_gems.storage import Catalog, CatalogItem, Manifest, save_conversation_json

    manifest = Manifest(tmp_path)
    save_conversation_json(
        output_dir=tmp_path,
        conversation_id="d2d821cc",
        title="Fixing NVIDIA Driver GPU Lockups ### Overview of the Issue",
        url="https://gemini.google.com/app/d2d821cc",
        conversation_data={"turns": []},
        manifest=manifest,
    )

    catalog = Catalog(tmp_path)
    # Catalog contains raw multi-line string with newlines and extra spaces
    catalog.update(
        [
            CatalogItem(
                id="d2d821cc",
                title="Fixing NVIDIA Driver GPU Lockups\n\n### Overview of the Issue\n\n",
                url="https://gemini.google.com/app/d2d821cc",
            )
        ]
    )
    catalog.save()

    exported_calls = []

    class DummyClient:
        def __init__(self, cdp_url, output_dir):
            self.manifest = manifest

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def export_conversation(self, conversation_id, **kwargs):
            exported_calls.append(conversation_id)

    monkeypatch.setattr("precious_gems.cli.entry.GeminiClient", DummyClient)

    parser = build_parser()
    args = parser.parse_args(["sync", "--output-dir", str(tmp_path), "--from-cache"])
    exit_code = cmd_sync(args)

    assert exit_code == 0
    assert exported_calls == []
    captured = capsys.readouterr().out
    assert "UPDATE: title changed" not in captured
