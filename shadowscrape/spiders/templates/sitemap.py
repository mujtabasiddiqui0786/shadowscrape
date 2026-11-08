"""SitemapSpider: crawl URLs listed in sitemap.xml / sitemap indexes / .gz / robots.txt."""
from __future__ import annotations

import gzip
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from lxml import etree

from .._util import aiter_results
from ..request import Request
from ..spider import Spider


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def sitemap_bytes(response: Any) -> bytes:
    """Response body as raw bytes, gunzipped when needed."""
    body = getattr(response, "body", None)
    if body is None:
        body = (getattr(response, "text", "") or "").encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    return body


def parse_sitemap(body: bytes) -> Tuple[str, List[Dict[str, str]]]:
    """Return ``("index"|"urlset"|"text", entries)``; entries are dicts with ``loc`` etc."""
    try:
        root = etree.fromstring(body, parser=etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    except Exception:
        root = None
    if root is None or not _local(root.tag) in ("urlset", "sitemapindex"):
        urls = [l.strip() for l in body.decode("utf-8", "replace").splitlines() if l.strip().startswith(("http://", "https://"))]
        return "text", [{"loc": u} for u in urls]
    kind = "index" if _local(root.tag) == "sitemapindex" else "urlset"
    entries: List[Dict[str, str]] = []
    for node in root:
        entry: Dict[str, str] = {}
        for child in node:
            name = _local(child.tag)
            if name in ("loc", "lastmod", "changefreq", "priority") and child.text:
                entry[name] = child.text.strip()
        if entry.get("loc"):
            entries.append(entry)
    return kind, entries


class SitemapSpider(Spider):
    """Discover pages through sitemaps.

    Set ``sitemap_urls`` explicitly, or leave empty to derive them from
    ``start_urls`` (domain roots): ``/robots.txt`` is read for ``Sitemap:`` lines,
    falling back to ``/sitemap.xml``.  ``sitemap_rules`` is a list of
    ``(url_regex, callback_name)``; the first match wins (default: everything ->
    ``parse``).  ``sitemap_follow`` filters which child sitemaps of an index are
    followed.  With ``discover_only=True`` no pages are fetched; each matching
    sitemap entry is yielded as an item ``{url, lastmod, changefreq, priority}``.
    """

    name = "sitemap"
    sitemap_urls: Sequence[str] = ()
    sitemap_rules: Sequence[Tuple[str, str]] = (("", "parse"),)
    sitemap_follow: Sequence[str] = ("",)
    use_robots: bool = True
    discover_only: bool = False

    async def start_requests(self) -> AsyncIterator[Request]:
        targets = list(self.sitemap_urls)
        for u in self.start_urls:
            p = urlsplit(u)
            origin = "%s://%s" % (p.scheme, p.netloc)
            targets.append(origin + ("/robots.txt" if self.use_robots else "/sitemap.xml"))
        for t in targets:
            cb = "_parse_robots" if t.lower().endswith("robots.txt") else "_parse_sitemap"
            yield Request(t, callback=cb, priority=100, dont_filter=True)

    async def _parse_robots(self, response: Any) -> AsyncIterator[Any]:
        text = getattr(response, "text", "") or ""
        found = [m.group(1).strip() for m in re.finditer(r"(?im)^\s*sitemap\s*:\s*(\S+)", text)]
        if not found:
            p = urlsplit(response.url)
            found = ["%s://%s/sitemap.xml" % (p.scheme, p.netloc)]
        for u in found:
            yield Request(u, callback="_parse_sitemap", priority=100)

    def _rule_for(self, url: str) -> Optional[str]:
        for pattern, cb in self.sitemap_rules:
            if re.search(pattern, url):
                return cb
        return None

    async def _parse_sitemap(self, response: Any) -> AsyncIterator[Any]:
        kind, entries = parse_sitemap(sitemap_bytes(response))
        self.stats.inc("sitemap_count")
        for e in entries:
            loc = e["loc"]
            if kind == "index":
                if any(re.search(p, loc) for p in self.sitemap_follow):
                    yield Request(loc, callback="_parse_sitemap", priority=100)
                continue
            cb = self._rule_for(loc)
            if cb is None:
                continue
            self.stats.inc("sitemap_urls")
            if self.discover_only:
                yield {"url": loc, "lastmod": e.get("lastmod"), "changefreq": e.get("changefreq"),
                       "priority": e.get("priority")}
            else:
                yield Request(loc, callback=cb, meta={"lastmod": e.get("lastmod")})

    async def parse(self, response: Any) -> AsyncIterator[Any]:
        """Default page callback: override to extract data."""
        yield {"url": response.url, "status": getattr(response, "status", None),
               "lastmod": getattr(response, "meta", {}).get("lastmod")}
