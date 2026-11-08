"""robots.txt cache (optional, used when ``Spider.robots_txt_obey`` is True)."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class RobotsCache:
    """Fetches and caches robots.txt per origin via an async ``fetch(url)`` callable."""

    def __init__(self, fetch: Callable[[str], Awaitable[Any]], user_agent: str = "*") -> None:
        self._fetch = fetch
        self.user_agent = user_agent
        self._parsers: Dict[str, Optional[RobotFileParser]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def origin(url: str) -> str:
        p = urlsplit(url)
        return "%s://%s" % (p.scheme, p.netloc)

    async def _parser(self, url: str) -> Optional[RobotFileParser]:
        origin = self.origin(url)
        if origin in self._parsers:
            return self._parsers[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._parsers:
                return self._parsers[origin]
            rp: Optional[RobotFileParser] = None
            try:
                resp = await self._fetch(origin + "/robots.txt")
                if int(getattr(resp, "status", 0)) == 200:
                    rp = RobotFileParser()
                    text = getattr(resp, "text", None)
                    if text is None:
                        text = (resp.body or b"").decode("utf-8", "replace")
                    rp.parse(text.splitlines())
            except asyncio.CancelledError:
                raise
            except Exception:
                rp = None
            self._parsers[origin] = rp
            return rp

    async def allowed(self, url: str) -> bool:
        rp = await self._parser(url)
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    async def crawl_delay(self, url: str) -> Optional[float]:
        rp = await self._parser(url)
        if rp is None:
            return None
        d = rp.crawl_delay(self.user_agent)
        return float(d) if d is not None else None

    async def site_maps(self, url: str) -> List[str]:
        rp = await self._parser(url)
        return list(rp.site_maps() or []) if rp is not None and hasattr(rp, "site_maps") else []
