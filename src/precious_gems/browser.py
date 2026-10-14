"""Browser connection and dedicated tab management via Playwright CDP."""

from typing import Optional
import urllib.error
import urllib.request
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    sync_playwright,
)

DEFAULT_CDP_URL = "http://127.0.0.1:9222"


def is_target_closed_error(err: BaseException) -> bool:
    """Returns True if the exception indicates the target page, context, or browser was closed."""
    if type(err).__name__ == "TargetClosedError":
        return True
    msg = str(err)
    return (
        "Target page, context or browser has been closed" in msg
        or "Target closed" in msg
        or "Browser has been closed" in msg
    )


class BrowserSession:
    """Manages connection to an existing Chrome instance and a dedicated worker tab."""

    def __init__(self, cdp_url: str = DEFAULT_CDP_URL) -> None:
        self.cdp_url = cdp_url
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._target_id: Optional[str] = None

    def __enter__(self) -> "BrowserSession":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        interrupted = exc_type is not None and issubclass(exc_type, KeyboardInterrupt)
        self.close(interrupted=interrupted)

    def connect(self) -> Page:
        if self._page is not None:
            return self._page

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as err:
            self.close()
            raise ConnectionError(
                f"Failed to connect to Chrome CDP at {self.cdp_url}. "
                "Ensure Chrome is running with remote debugging enabled."
            ) from err

        contexts = self._browser.contexts
        if not contexts:
            self.close()
            raise RuntimeError(f"No browser context found on Chrome instance at {self.cdp_url}")

        self._context = contexts[0]
        # Open a dedicated worker tab
        self._page = self._context.new_page()
        self._target_id = self._resolve_target_id(self._page)
        return self._page

    def _resolve_target_id(self, page: Page) -> Optional[str]:
        """Resolves the CDP targetId for the worker tab so it can be closed via HTTP if interrupted."""
        if self._context is None:
            return None
        try:
            cdp_session = self._context.new_cdp_session(page)
            info = cdp_session.send("Target.getTargetInfo")
            target_id = info.get("targetInfo", {}).get("targetId")
            cdp_session.detach()
            return target_id
        except PlaywrightError:
            return None

    def _close_tab_via_http(self, target_id: str) -> None:
        """Best-effort closes the worker tab directly via Chrome's HTTP CDP endpoint without using Playwright's fiber."""
        close_url = f"{self.cdp_url.rstrip('/')}/json/close/{target_id}"
        try:
            with urllib.request.urlopen(close_url, timeout=1.0) as _:
                pass
        except (urllib.error.URLError, OSError, ValueError):
            pass

    @staticmethod
    def _is_fiber_dead(obj: object) -> bool:
        """Checks whether Playwright's underlying greenlet dispatcher fiber has terminated."""
        impl = getattr(obj, "_impl_obj", None)
        fiber = getattr(impl, "_dispatcher_fiber", None)
        return bool(getattr(fiber, "dead", False))

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession is not connected. Call connect() first.")
        return self._page

    def close(self, interrupted: bool = False) -> None:
        page = self._page
        target_id = self._target_id
        self._page = None
        self._target_id = None
        self._context = None
        self._browser = None

        pw = self._playwright
        self._playwright = None

        fiber_dead = (page is not None and self._is_fiber_dead(page)) or (
            pw is not None and self._is_fiber_dead(pw)
        )

        if page is not None:
            if interrupted or fiber_dead:
                if target_id:
                    self._close_tab_via_http(target_id)
            else:
                try:
                    page.close()
                except BaseException as err:
                    if target_id and not is_target_closed_error(err):
                        self._close_tab_via_http(target_id)
                    if isinstance(err, KeyboardInterrupt):
                        interrupted = True
                        fiber_dead = True

        if pw is not None:
            if interrupted or fiber_dead:
                self._kill_driver_proc(pw)
            try:
                pw.stop()
            except BaseException:
                self._force_kill_playwright(pw)

    @staticmethod
    def _kill_driver_proc(pw: Playwright) -> None:
        """Terminates the underlying Playwright Node.js driver process so pipe reads finish immediately."""
        impl = getattr(pw, "_impl_obj", None)
        conn = getattr(impl, "_connection", None)
        transport = getattr(conn, "_transport", None)
        proc = getattr(transport, "_proc", None)
        if proc is not None and getattr(proc, "returncode", None) is None:
            try:
                proc.kill()
            except OSError:
                pass

    @classmethod
    def _force_kill_playwright(cls, pw: Playwright) -> None:
        """Forcefully kills the Playwright driver process and cancels pending tasks if graceful stop is interrupted."""
        import asyncio

        cls._kill_driver_proc(pw)
        impl = getattr(pw, "_impl_obj", None)
        conn = getattr(impl, "_connection", None)
        loop = getattr(conn, "_loop", None)
        if loop is not None and not loop.is_closed():
            try:
                for task in asyncio.all_tasks(loop):
                    if not (task.done() or task.cancelled()):
                        task.cancel()
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            except BaseException:
                pass
