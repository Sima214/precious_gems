"""DOM interception logic for Gemini's print/export iframe."""

import time
from typing import Any, Dict, Optional
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

INTERCEPTOR_SCRIPT = """
(() => {
  if (window.__geminiInterceptorInstalled) {
    window.__extractedConversation = null;
    return;
  }
  window.__geminiInterceptorInstalled = true;
  window.__extractedConversation = null;

  function hookIframe(child) {
    if (child && child.tagName === 'IFRAME') {
      setTimeout(() => {
        try {
          if (child.contentWindow) {
            child.contentWindow.print = function() {
              try {
                const doc = child.contentDocument;
                const html = doc ? doc.documentElement.outerHTML : '';
                window.__extractedConversation = {
                  title: document.title,
                  url: window.location.href,
                  html: html,
                  intercepted_at: new Date().toISOString()
                };
              } catch (err) {
                window.__extractedConversationError = err.message || String(err);
              }
            };
          }
        } catch (e) {
          // ignore access errors on foreign frames
        }
      }, 0);
    }
  }

  const origElementAppend = Element.prototype.appendChild;
  Element.prototype.appendChild = function(child, ...rest) {
    hookIframe(child);
    return origElementAppend.apply(this, [child, ...rest]);
  };

  const origNodeAppend = Node.prototype.appendChild;
  Node.prototype.appendChild = function(child, ...rest) {
    hookIframe(child);
    return origNodeAppend.apply(this, [child, ...rest]);
  };
})();
"""


def install_interceptor(page: Page) -> None:
    """Installs the print interception hook into the active page."""
    page.evaluate(INTERCEPTOR_SCRIPT)


def reset_interceptor(page: Page) -> None:
    """Resets the extracted conversation payload to null."""
    page.evaluate("() => { window.__extractedConversation = null; window.__extractedConversationError = null; }")


def wait_for_intercepted_data(page: Page, timeout_seconds: float = 30.0) -> Dict[str, Any]:
    """Polls page for window.__extractedConversation until populated or timeout."""
    start_time = time.time()
    poll_interval = 0.25

    while time.time() - start_time < timeout_seconds:
        error = page.evaluate("() => window.__extractedConversationError || null")
        if error:
            raise RuntimeError(f"Error occurred inside export iframe print hook: {error}")

        data = page.evaluate("() => window.__extractedConversation || null")
        if data and isinstance(data, dict) and data.get("html"):
            return data

        page.wait_for_timeout(int(poll_interval * 1000))

    raise TimeoutError(f"Timed out after {timeout_seconds}s waiting for Gemini export interception.")
