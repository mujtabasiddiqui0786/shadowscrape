"""shadowscrape.parser: lxml-based HTML parsing with adaptive element tracking."""
from __future__ import annotations

from .selector import Selector, Selectors
from .storage import SQLiteStorage, get_storage
from .text import TextHandler

Adaptor = Selector  # deprecated alias

__all__ = ["Selector", "Selectors", "Adaptor", "TextHandler", "SQLiteStorage", "get_storage"]
