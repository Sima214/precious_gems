"""DOM-driven upstream conversation deletion for Google Gemini."""

import time
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from .browser import is_target_closed_error


def delete_conversation_dom(
    page: Page,
    conversation_id: str,
    timeout_ms: int = 30000,
) -> bool:
    """Navigates to conversation and triggers deletion by clicking Gemini HTML elements.

    Returns True if successfully deleted, False if conversation was not found or already deleted.
    """
    slug = conversation_id.removeprefix("c_")
    target_url = f"https://gemini.google.com/app/{slug}"

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError as err:
        if is_target_closed_error(err):
            raise
        return False

    # Generous stabilization buffer (2.5s) to let Gemini hydrate conversation state
    page.wait_for_timeout(2500)

    # If redirected away to /app, conversation does not exist or was already deleted
    current_url = page.url.rstrip("/")
    if current_url in ("https://gemini.google.com/app", "https://gemini.google.com"):
        return False

    # Locate conversation action menu button in top-bar-actions
    menu_button_selectors = [
        'top-bar-actions button:has(mat-icon[fonticon="more_vert"])',
        'top-bar-actions button[data-test-id="chat-menu-button"]',
        'top-bar-actions button[aria-label*="επικοινωνίας"]',
        'top-bar-actions button[aria-label*="chat" i]',
        "top-bar-actions button",
    ]

    try:
        page.wait_for_selector(", ".join(menu_button_selectors), timeout=15000)
    except PlaywrightTimeoutError:
        return False

    menu_btn = None
    for sel in menu_button_selectors:
        btn = page.query_selector(sel)
        if btn and btn.is_visible():
            menu_btn = btn
            break

    if not menu_btn:
        return False

    menu_btn.click()
    # Generous stabilization buffer (1.5s) for menu overlay render
    page.wait_for_timeout(1500)

    # Locate Delete item in dropdown menu
    delete_item_selectors = [
        'gem-menu-item[data-test-id="delete-button"]',
        'gem-menu-item[value="delete"]',
        'gem-menu-item:has(mat-icon[fonticon="delete"])',
        '[role="menuitem"]:has(mat-icon[fonticon="delete"])',
        'gem-menu-item:has-text("Διαγραφή")',
        'gem-menu-item:has-text("Delete")',
    ]

    try:
        page.wait_for_selector(", ".join(delete_item_selectors), timeout=10000)
    except PlaywrightTimeoutError:
        page.keyboard.press("Escape")
        return False

    del_item = None
    for sel in delete_item_selectors:
        item = page.query_selector(sel)
        if item and item.is_visible():
            del_item = item
            break

    if not del_item:
        page.keyboard.press("Escape")
        return False

    del_item.click()
    # Generous stabilization buffer (1.5s) for confirmation dialog mount
    page.wait_for_timeout(1500)

    # Locate confirmation dialog and confirm button
    try:
        page.wait_for_selector("mat-dialog-container", timeout=10000)
    except PlaywrightTimeoutError:
        return False

    confirm_button_selectors = [
        'mat-dialog-container button[jslog*="186009"]',
        'mat-dialog-container button:has-text("Διαγραφή")',
        'mat-dialog-container button:has-text("Delete")',
        "mat-dialog-container button:last-child",
    ]

    confirm_btn = None
    for sel in confirm_button_selectors:
        btn = page.query_selector(sel)
        if btn and btn.is_visible():
            confirm_btn = btn
            break

    if not confirm_btn:
        page.keyboard.press("Escape")
        return False

    confirm_btn.click()
    # Generous post-deletion buffer (3s) for network RPC to settle and redirect
    page.wait_for_timeout(3000)

    return True
