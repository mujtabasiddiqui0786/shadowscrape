"""Shared fakes for spider tests."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class FakeResponse:
    def __init__(self, url: str, status: int = 200, body: Union[str, bytes] = "", headers: Optional[dict] = None):
        self.url = url
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.body.decode("utf-8", "replace")
        self.headers = headers or {"content-type": "text/html"}
        self.meta: Dict[str, Any] = {}
        self.reason = ""


Route = Union[str, Tuple[int, str], Tuple[int, str, dict], Callable[..., Any]]


class FakeSession:
    """In-memory async session. ``routes``: url -> body | (status, body[, headers]) | callable(url, n, kwargs)."""

    def __init__(self, routes: Dict[str, Route], latency: float = 0.0, sync: bool = False):
        self.routes = routes
        self.latency = latency
        self.calls: List[Tuple[str, str, dict]] = []
        self.counts: Dict[str, int] = {}
        self.entered = self.exited = False
        self.active = self.max_active = 0
        if sync:
            self.get = self._sync_get  # type: ignore[assignment]
            self.post = self._sync_get  # type: ignore[assignment]

    def _build(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        n = self.counts[url] = self.counts.get(url, 0) + 1
        r = self.routes.get(url)
        if r is None:
            return FakeResponse(url, 404, "nf")
        if callable(r):
            r = r(url, n, kwargs)
        if isinstance(r, FakeResponse):
            return r
        if isinstance(r, str):
            return FakeResponse(url, 200, r)
        return FakeResponse(url, r[0], r[1], r[2] if len(r) > 2 else None)

    async def _async(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            return self._build(method, url, kwargs)
        finally:
            self.active -= 1

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._async("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._async("POST", url, **kwargs)

    def _sync_get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._build("GET", url, kwargs)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *a: Any) -> None:
        self.exited = True


class FakeRotator:
    def __init__(self, proxies: List[str]):
        self.proxies = proxies
        self.i = 0
        self.failed: List[str] = []
        self.ok: List[str] = []

    def next(self) -> str:
        p = self.proxies[self.i % len(self.proxies)]
        self.i += 1
        return p

    def mark_failed(self, p: str) -> None:
        self.failed.append(p)

    def mark_ok(self, p: str) -> None:
        self.ok.append(p)


def page(links=(), title="T", extra="") -> str:
    a = "".join('<a href="%s">l</a>' % l for l in links)
    return "<html><head><title>%s</title></head><body>%s%s</body></html>" % (title, a, extra)
