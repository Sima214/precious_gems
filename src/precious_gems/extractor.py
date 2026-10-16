"""Conversation extraction and scroll hydration orchestration."""

import re
import time
from typing import Any, Dict, Optional
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from .browser import is_target_closed_error
from .interceptor import install_interceptor, reset_interceptor, wait_for_intercepted_data


def hydrate_conversation_scroll(
    page: Page,
    max_rounds: int = 10,
    settle_delay: float = 2.0,
) -> None:
    """Scrolls chat history upwards using focus, PageUp/wheel pulses, and bounce sentinel re-arming."""
    try:
        scroller = page.wait_for_selector(
            'infinite-scroller.chat-history, [class*="chat-history"]',
            timeout=20000,
        )
    except PlaywrightTimeoutError:
        return

    if not scroller:
        return

    # Move focus inside chat container
    box = scroller.bounding_box()
    if box:
        page.mouse.click(
            box["x"] + min(200, box["width"] / 2), box["y"] + min(200, box["height"] / 2)
        )

    prev_first_text: Optional[str] = None
    stable_count = 0

    for r in range(max_rounds):
        # Send PageUp and wheel pulses to trigger Angular CDK scroll listeners
        page.keyboard.press("PageUp")
        for _ in range(6):
            page.mouse.wheel(0, -1200)
            page.wait_for_timeout(300)
        page.keyboard.press("Home")

        # Wait if a loading spinner is active
        try:
            page.wait_for_selector(
                ".loading-content-spinner-container.is-loading, chat-loading-animation",
                state="hidden",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            pass

        # Polite buffer for modest network / DOM hydration
        page.wait_for_timeout(int(settle_delay * 1000))

        # Check current top prompt
        state = page.evaluate("""() => {
            const queries = document.querySelectorAll('user-query');
            const first = queries.length > 0 ? queries[0].innerText.trim().slice(0, 100) : '';
            return { count: queries.length, first: first };
        }""")

        first_text = state.get("first", "")
        if first_text and first_text == prev_first_text:
            stable_count += 1
            if stable_count >= 2:
                # Top prompt unchanged across multiple bounce cycles
                break
        else:
            stable_count = 0
            prev_first_text = first_text

        # Bounce scroll down slightly to re-arm the top IntersectionObserver sentinel
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(800)

    # Ensure final scroll position is at the very top
    page.evaluate("""() => {
        const scroller = document.querySelector('infinite-scroller.chat-history, [class*="chat-history"]');
        if (scroller) scroller.scrollTop = 0;
    }""")
    page.wait_for_timeout(500)


def trigger_export_menu(page: Page) -> None:
    """Finds and clicks the conversation actions menu button in top-bar-actions, then clicks download PDF."""
    menu_button_selectors = [
        'top-bar-actions button[aria-label="Άνοιγμα μενού για ενέργειες επικοινωνίας."]',
        'top-bar-actions button[data-test-id="chat-menu-button"]',
        'top-bar-actions button[aria-label*="επικοινωνίας"]',
        'top-bar-actions button[aria-label*="chat" i]',
        "top-bar-actions button",
        'button[data-test-id="chat-menu-button"]',
        'button[aria-label="Άνοιγμα μενού για ενέργειες επικοινωνίας."]',
    ]

    try:
        page.wait_for_selector(", ".join(menu_button_selectors), timeout=15000)
    except PlaywrightTimeoutError:
        pass

    menu_btn = None
    for selector in menu_button_selectors:
        btn = page.query_selector(selector)
        if btn and btn.is_visible():
            menu_btn = btn
            break

    if not menu_btn:
        raise RuntimeError("Could not locate chat menu button (actions menu).")

    menu_btn.click()
    page.wait_for_timeout(300)

    pdf_item_selectors = [
        'gem-menu-item[data-test-id="download-pdf-button"]',
        '[data-test-id="download-pdf-button"]',
        '[data-value="download-pdf"]',
        'gem-menu-item:has-text("PDF")',
        'button:has-text("PDF")',
        '[role="menuitem"]:has-text("PDF")',
        'gem-menu-item:has-text("Λήψη αρχείου PDF")',
    ]

    pdf_btn = None
    for selector in pdf_item_selectors:
        item = page.query_selector(selector)
        if item and item.is_visible():
            pdf_btn = item
            break

    if not pdf_btn:
        # Press escape to dismiss menu before raising
        page.keyboard.press("Escape")
        raise RuntimeError("Could not locate 'Download PDF' menu item in chat menu.")

    pdf_btn.click()


def extract_conversation(
    page: Page, url_or_id: str, timeout_seconds: float = 60.0
) -> Dict[str, Any]:
    """Navigates to conversation, hydrates history, triggers export, and intercepts HTML."""
    target_url = url_or_id
    if not target_url.startswith("http"):
        target_url = f"https://gemini.google.com/app/{url_or_id}"

    # Extract ID from URL
    match = re.search(r"/app/([a-zA-Z0-9_\-]+)", target_url)
    conv_id = match.group(1) if match else url_or_id

    # Navigate if not already on the page
    if page.url != target_url:
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector(
                'infinite-scroller.chat-history, [class*="chat-history"], chat-window, user-query, [data-test-id="chat-menu-button"]',
                timeout=25000,
            )
        except PlaywrightTimeoutError:
            pass
        # Give gentle buffer for initial hydration on modest internet/compute
        page.wait_for_timeout(3000)
    else:
        page.wait_for_timeout(1500)

    # Install interceptor before any triggers
    install_interceptor(page)
    reset_interceptor(page)

    # Hydrate scroll history with focus, PageUp/wheel pulses, and bounce sentinel re-arming
    hydrate_conversation_scroll(page, max_rounds=10, settle_delay=2.0)

    # Re-verify interceptor is installed
    install_interceptor(page)

    # Gentle pause before export trigger
    page.wait_for_timeout(1000)

    # Trigger export
    trigger_export_menu(page)

    # Await intercepted clean HTML
    data = wait_for_intercepted_data(page, timeout_seconds=timeout_seconds)

    # Dismiss any leftover menu overlay if present
    try:
        page.keyboard.press("Escape")
    except PlaywrightError as err:
        if is_target_closed_error(err):
            raise

    return {
        "id": conv_id,
        "title": data.get("title") or page.title() or "Untitled Conversation",
        "url": target_url,
        "html": data["html"],
    }
