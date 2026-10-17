"""Sidebar conversation discovery and enumeration."""

import re
from dataclasses import dataclass
from typing import List, Optional
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .storage import normalize_title


@dataclass
class SidebarConversation:
    id: str
    title: str
    url: str


def ensure_sidebar_open(page: Page) -> None:
    """Ensures the Gemini sidebar is open/expanded."""
    # First give Angular/Gemini components a moment to mount if loading
    try:
        page.wait_for_selector(
            'a[href*="/app/"], button[aria-label*="πλευρ"], button[data-test-id="side-nav-button"]',
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        pass

    # Check if conversation links are already visible
    existing_links = page.query_selector_all('a[href*="/app/"]')
    if len(existing_links) > 1:  # More than just /app (New Chat)
        return

    # Look for sidebar toggle button
    toggle_selectors = [
        'button[data-test-id="side-nav-sparkle-button"]',
        'button[data-test-id="side-nav-button"]',
        'button[aria-label="Άνοιγμα πλευρικής γραμμής"]',
        'button[aria-label="Main menu"]',
        'button[aria-label="Open sidebar"]',
        'button[aria-label*="πλευρ"]',
        'button[aria-label*="sidebar" i]',
        "button.side-nav-sparkle-button",
    ]
    for selector in toggle_selectors:
        btn = page.query_selector(selector)
        if btn and btn.is_visible():
            btn.click()
            try:
                page.wait_for_selector('a[href*="/app/"]', timeout=10000)
            except PlaywrightTimeoutError:
                pass
            break


def enumerate_conversations(page: Page, max_scrolls: int = 30) -> List[SidebarConversation]:
    """Enumerates all conversation links from the sidebar from top to bottom."""
    ensure_sidebar_open(page)

    # Expand any collapsed sections (e.g. "Όλες" / "All") in the sidebar
    page.evaluate("""() => {
        const toggles = document.querySelectorAll('expandable-section-header, [data-test-id="expandable-section-toggle"]');
        for (const t of toggles) {
            if (t.getAttribute('aria-expanded') === 'false') {
                t.click();
            }
        }
    }""")
    page.wait_for_timeout(500)

    conversations: List[SidebarConversation] = []
    seen_ids = set()
    no_new_rounds = 0

    for _ in range(max_scrolls):
        anchors = page.query_selector_all('a[href*="/app/"]')
        new_in_round = 0
        for anchor in anchors:
            href = anchor.get_attribute("href") or ""
            match = re.search(r"/app/([a-zA-Z0-9_\-]+)", href)
            if not match:
                continue
            conv_id = match.group(1)
            if conv_id in seen_ids:
                continue

            raw_title = anchor.inner_text().strip()
            title = normalize_title(raw_title)
            canonical_url = f"https://gemini.google.com/app/{conv_id}"

            seen_ids.add(conv_id)
            conversations.append(SidebarConversation(id=conv_id, title=title, url=canonical_url))
            new_in_round += 1

        if new_in_round == 0:
            no_new_rounds += 1
            if no_new_rounds >= 3:
                # Reached bottom of sidebar
                break
        else:
            no_new_rounds = 0

        # Scroll sidebar container down
        page.evaluate("""() => {
            const scroller = document.querySelector('side-navigation-v2 infinite-scroller, bard-sidenav infinite-scroller, [data-test-id="conversation-list"]');
            if (scroller) {
                scroller.scrollTop += 800;
            }
        }""")
        page.wait_for_timeout(400)

    return conversations
