"""shadowscrape: fast HTTP, stealth browsers, adaptive parsing and concurrent crawling."""
from __future__ import annotations

from typing import Any

from .parser import Adaptor, Selector, Selectors
from .proxy import ProxyRotator
from .response import Response

__version__ = "0.1.0"

_LAZY = {
    "Fetcher": "fetchers",
    "AsyncFetcher": "fetchers",
    "FetcherSession": "fetchers",
    "AsyncFetcherSession": "fetchers",
    "DynamicFetcher": "fetchers",
    "DynamicSession": "fetchers",
    "AsyncDynamicSession": "fetchers",
    "StealthyFetcher": "fetchers",
    "StealthySession": "fetchers",
    "AsyncStealthySession": "fetchers",
    "Spider": "spiders",
    "Request": "spiders",
    "CrawlResult": "spiders",
    "SitemapSpider": "spiders",
    "LinkExtractorSpider": "spiders",
    "ListingDetailSpider": "spiders",
}

__all__ = ["Adaptor", "Selector", "Selectors", "ProxyRotator", "Response", *_LAZY]


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'shadowscrape' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
