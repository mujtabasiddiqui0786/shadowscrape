"""Fetch a page over HTTP with browser TLS impersonation and extract data with CSS selectors.

Uses quotes.toscrape.com, a site built for scraping practice.
"""
from __future__ import annotations

import sys

from shadowscrape import Fetcher, FetcherSession

URL = sys.argv[1] if len(sys.argv) > 1 else "https://quotes.toscrape.com"

page = Fetcher.get(URL, impersonate="chrome131")
print(page.status, page.css("title::text").get())
for quote in page.css(".quote"):
    print("-", quote.css(".text::text").get(), "|", quote.css(".author::text").get())

# A session keeps cookies and a fixed fingerprint across requests.
with FetcherSession(impersonate="chrome131") as session:
    first = session.get(URL)
    print("session status:", first.status, "cookies:", session.cookies)
