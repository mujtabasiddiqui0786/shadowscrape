"""Fetchers: HTTP (curl_cffi) eagerly, browser classes (playwright) lazily."""
from __future__ import annotations

from typing import Any

from shadowscrape.fetchers.http import (
    AsyncFetcher,
    AsyncFetcherSession,
    Fetcher,
    FetcherSession,
)
from shadowscrape.proxy import ProxyRotator

_BROWSER_NAMES = (
    "DynamicFetcher",
    "DynamicSession",
    "AsyncDynamicSession",
    "StealthyFetcher",
    "StealthySession",
    "AsyncStealthySession",
)

__all__ = [
    "Fetcher",
    "AsyncFetcher",
    "FetcherSession",
    "AsyncFetcherSession",
    "ProxyRotator",
    *_BROWSER_NAMES,
]


def __getattr__(name: str) -> Any:
    """Import browser classes on first access so HTTP works without playwright."""
    if name in _BROWSER_NAMES:
        from shadowscrape.fetchers import browser  # noqa: WPS433

        value = getattr(browser, name)
        globals()[name] = value
        return value
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> list:
    return sorted(set(globals()) | set(_BROWSER_NAMES))
