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


def test_build_parser_sync():
    parser = build_parser()
    args = parser.parse_args(["sync", "--limit", "10", "--delay", "1.5", "--cdp-url", "http://127.0.0.1:9222", "--html", "--max-attachment-size", "100MB"])
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
    catalog.update([
        CatalogItem(id="c1", title="Title 1", url="https://gemini.google.com/app/c1"),
        CatalogItem(id="c2", title="Title 2", url="https://gemini.google.com/app/c2"),
    ])
    catalog.save()

    parser = build_parser()
    args = parser.parse_args(["list", "--output-dir", str(tmp_path), "--all"])
    exit_code = cmd_list(args)
    assert exit_code == 0

    captured = capsys.readouterr().out
    assert "[Exported] c1: Title 1" in captured
    assert "[Pending] c2: Title 2" in captured


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
            raise TargetClosedError("Page.query_selector_all: Target page, context or browser has been closed")

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


