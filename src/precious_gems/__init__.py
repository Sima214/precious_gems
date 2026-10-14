"""Precious Gems - Gemini conversation programmatic backup and export engine."""

from .client import GeminiClient
from .sidebar import SidebarConversation
from .storage import ConversationRecord, Manifest

__version__ = "0.1.0"
__all__ = ["GeminiClient", "SidebarConversation", "ConversationRecord", "Manifest"]
