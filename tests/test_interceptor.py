"""Unit tests for interceptor script verification."""

from precious_gems.interceptor import INTERCEPTOR_SCRIPT


def test_interceptor_script_integrity():
    assert "window.__extractedConversation" in INTERCEPTOR_SCRIPT
    assert "Element.prototype.appendChild" in INTERCEPTOR_SCRIPT
    assert "child.contentWindow.print" in INTERCEPTOR_SCRIPT
    assert "documentElement.outerHTML" in INTERCEPTOR_SCRIPT
