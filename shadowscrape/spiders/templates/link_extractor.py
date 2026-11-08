"""LinkExtractorSpider: crawl a whole site following links."""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, List, Optional, Sequence
from urllib.parse import urlsplit

from .._util import aiter_results, base_url_of, clean_link, get_doc, host_matches, select
from ..request import Request
from ..spider import Spider

DEFAULT_DENY_EXTENSIONS = (
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "css", "js", "pdf", "zip", "gz", "tar", "rar",
    "7z", "mp3", "mp4", "avi", "mov", "wmv", "webm", "woff", "woff2", "ttf", "eot", "exe", "dmg", "xls",
    "xlsx", "doc", "docx", "ppt", "pptx",
)


class LinkExtractorSpider(Spider):
    """Breadth-first crawl of every reachable page.

    * ``allow`` / ``deny``: regex lists matched against the absolute URL
      (a URL must match some ``allow`` pattern if any given, and no ``deny``).
    * ``max_depth``: link depth from the start URLs (``None`` = unlimited).
    * ``same_domain``: restrict to the start URLs' domains (when ``allowed_domains``
      is not set).
    * ``restrict_css``: only extract links inside elements matching this selector.
    Override :meth:`parse_page` to extract data from each page.
    """

    name = "links"
    allow: Sequence[str] = ()
    deny: Sequence[str] = ()
    max_depth: Optional[int] = 3
    same_domain: bool = True
    restrict_css: Optional[str] = None
    deny_extensions: Sequence[str] = DEFAULT_DENY_EXTENSIONS
    respect_nofollow: bool = False

    async def start_requests(self) -> AsyncIterator[Request]:
        if self.allowed_domains is None and self.same_domain:
            self.allowed_domains = sorted({urlsplit(u).hostname or "" for u in self.start_urls} - {""})
        async for r in super().start_requests():
            yield r

    def url_allowed(self, url: str) -> bool:
        path = urlsplit(url).path.lower()
        if "." in path.rsplit("/", 1)[-1] and path.rsplit(".", 1)[-1] in set(self.deny_extensions):
            return False
        if self.allow and not any(re.search(p, url) for p in self.allow):
            return False
        return not any(re.search(p, url) for p in self.deny)

    def extract_links(self, response: Any) -> List[str]:
        """Absolute, de-fragmented, filtered links found on ``response``."""
        doc = get_doc(response)
        base = base_url_of(response, doc)
        roots = [doc]
        if self.restrict_css:
            from .._util import _xpath_for  # noqa
            roots = doc.xpath(_xpath_for(self.restrict_css))
        out: List[str] = []
        seen = set()
        for root in roots:
            for a in root.xpath(".//a[@href] | .//area[@href]"):
                if self.respect_nofollow and "nofollow" in (a.get("rel") or "").lower():
                    continue
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                from urllib.parse import urljoin
                url = clean_link(urljoin(base, href))
                if url and url not in seen and self.url_allowed(url):
                    seen.add(url)
                    out.append(url)
        return out

    async def parse_page(self, response: Any) -> AsyncIterator[Any]:
        """Per-page extraction hook; default yields url/status/title/depth."""
        titles = select(get_doc(response), "title")
        yield {"url": response.url, "status": getattr(response, "status", None),
               "title": titles[0] if titles else None,
               "depth": getattr(response, "meta", {}).get("depth", 0)}

    async def parse(self, response: Any) -> AsyncIterator[Any]:
        async for out in aiter_results(self.parse_page(response)):
            yield out
        ctype = ""
        headers = getattr(response, "headers", None) or {}
        for k, v in dict(headers).items():
            if str(k).lower() == "content-type":
                ctype = str(v).lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            return
        depth = int(getattr(response, "meta", {}).get("depth", 0))
        if self.max_depth is not None and depth >= self.max_depth:
            return
        for url in self.extract_links(response):
            yield Request(url, callback="parse")
