"""Public library client interface for Precious Gems."""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.sync_api import Error as PlaywrightError, Page

from .browser import DEFAULT_CDP_URL, BrowserSession, is_target_closed_error
from .deletion import delete_conversation_dom
from .extractor import extract_conversation
from .rpc import (
    download_conversation_files,
    extract_latest_timestamp,
    fetch_conversation_rpc,
    list_conversations_rpc,
)
from .sidebar import SidebarConversation, enumerate_conversations
from .storage import (
    Catalog,
    ConversationRecord,
    Manifest,
    resolve_conversation_dir,
    save_conversation,
    save_conversation_json,
    slugify,
)


class GeminiClient:
    """High-reliability client managing CDP browser session, RPC extraction, and storage."""

    def __init__(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        output_dir: Path = Path("exports"),
    ) -> None:
        self.cdp_url = cdp_url
        self.output_dir = Path(output_dir)
        self.manifest = Manifest(self.output_dir)
        self.catalog = Catalog(self.output_dir)
        self._session: Optional[BrowserSession] = None
        self._page: Optional[Page] = None

    def __enter__(self) -> "GeminiClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        interrupted = exc_type is not None and issubclass(exc_type, KeyboardInterrupt)
        self.close(interrupted=interrupted)

    def connect(self) -> Page:
        """Connects to Chrome via CDP and returns the dedicated page."""
        if self._page is not None:
            return self._page
        self._session = BrowserSession(self.cdp_url)
        self._page = self._session.connect()
        # Navigate to /app if not already on Gemini
        if "gemini.google.com" not in self._page.url:
            self._page.goto(
                "https://gemini.google.com/app", wait_until="domcontentloaded", timeout=45000
            )
            self._page.wait_for_timeout(2000)
        return self._page

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("GeminiClient is not connected. Call connect() first.")
        return self._page

    def close(self, interrupted: bool = False) -> None:
        """Closes the dedicated worker page and disconnects CDP."""
        if self._session is not None:
            self._session.close(interrupted=interrupted)
            self._session = None
        self._page = None

    def list_conversations(
        self,
        use_html_fallback: bool = False,
        max_scrolls: int = 30,
    ) -> List[SidebarConversation]:
        """Enumerates conversations via direct MaZiqc RPC (default) or DOM sidebar fallback."""
        self.connect()
        if not use_html_fallback:
            try:
                rpc_items = list_conversations_rpc(self.page)
                return [
                    SidebarConversation(
                        id=item["id"],
                        title=item["title"],
                        url=item["url"],
                    )
                    for item in rpc_items
                ]
            except PlaywrightError as err:
                if is_target_closed_error(err):
                    raise
                print(
                    f"[WARNING] RPC conversation enumeration failed: {err}. Falling back to sidebar DOM.",
                    file=sys.stderr,
                    flush=True,
                )

        return enumerate_conversations(self.page, max_scrolls=max_scrolls)

    def get_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        use_html_fallback: bool = False,
    ) -> Dict[str, Any]:
        """Extracts conversation data via direct RPC or print DOM fallback."""
        self.connect()
        slug = conversation_id.removeprefix("c_")
        if use_html_fallback:
            return extract_conversation(self.page, slug)
        return fetch_conversation_rpc(self.page, slug, title=title)

    def export_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        use_html_fallback: bool = False,
        max_attachment_size: Optional[int] = None,
    ) -> ConversationRecord:
        """Extracts and saves a single conversation to the output directory."""
        slug = conversation_id.removeprefix("c_")
        if title is None:
            title = self.catalog.get_title(slug)
        data = self.get_conversation(slug, title=title, use_html_fallback=use_html_fallback)
        resolved_title = data.get("title") or title or "Untitled Conversation"
        url = data.get("url") or f"https://gemini.google.com/app/{slug}"

        if use_html_fallback:
            return save_conversation(
                output_dir=self.output_dir,
                conversation_id=slug,
                title=resolved_title,
                url=url,
                html_content=data["html"],
                manifest=self.manifest,
            )

        # Download any conversation attachments in the browser session
        latest_ts = extract_latest_timestamp(data.get("turns", []))
        _, base_filename = resolve_conversation_dir(
            self.output_dir, slug, resolved_title, self.manifest
        )
        attachment_hashes = download_conversation_files(
            page=self.page,
            output_dir=self.output_dir,
            export_filename=base_filename,
            turns=data.get("turns", []),
            max_attachment_size=max_attachment_size,
            timestamp_iso=latest_ts,
        )

        return save_conversation_json(
            output_dir=self.output_dir,
            conversation_id=slug,
            title=resolved_title,
            url=url,
            conversation_data=data,
            manifest=self.manifest,
            attachments=attachment_hashes,
            turn_timestamp=latest_ts,
        )

    def delete_conversation(
        self,
        conversation_id: str,
        delete_archived: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Deletes a conversation upstream via DOM interaction, optionally purging local archive."""
        slug = conversation_id.removeprefix("c_")

        # Check archive up-to-date status unless force is requested
        if not force:
            if not self.manifest.is_up_to_date(slug, catalog=self.catalog):
                return {
                    "id": slug,
                    "status": "skipped",
                    "reason": "unarchived_or_outdated",
                }

        self.connect()
        deleted = delete_conversation_dom(self.page, slug)
        if deleted:
            if delete_archived:
                self.manifest.delete_conversation(slug, delete_files=True)
                self.manifest.save()
                if self.catalog.remove([slug]):
                    self.catalog.save()
                return {
                    "id": slug,
                    "status": "deleted",
                    "archived_deleted": True,
                }
            else:
                self.manifest.mark_deleted_upstream(slug)
                self.manifest.save()
                return {
                    "id": slug,
                    "status": "deleted",
                    "archived_deleted": False,
                }
        else:
            return {
                "id": slug,
                "status": "not_found",
                "reason": "not_found_on_gemini",
            }

    def sync_all(
        self,
        use_html_fallback: bool = False,
        limit: Optional[int] = None,
        max_scrolls: int = 30,
        max_attachment_size: Optional[int] = None,
    ) -> List[ConversationRecord]:
        """Synchronizes all conversations into the local archive."""
        convs = self.list_conversations(
            use_html_fallback=use_html_fallback, max_scrolls=max_scrolls
        )
        if limit is not None:
            convs = convs[:limit]

        records: List[ConversationRecord] = []
        for conv in convs:
            # Skip if already in manifest
            if self.manifest.has_conversation(conv.id):
                continue
            rec = self.export_conversation(
                conversation_id=conv.id,
                title=conv.title,
                use_html_fallback=use_html_fallback,
                max_attachment_size=max_attachment_size,
            )
            records.append(rec)
        return records
