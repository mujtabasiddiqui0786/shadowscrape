"""Render a JavaScript page in a stealth browser and capture the background API calls it makes.

Needs: pip install -e ".[stealth]" and shadowscrape install
"""
from __future__ import annotations

import sys

from shadowscrape import StealthyFetcher

URL = sys.argv[1] if len(sys.argv) > 1 else "https://quotes.toscrape.com/js/"

page = StealthyFetcher.fetch(URL, headless=True, network_idle=True, capture_xhr=r".*")
print(page.status, page.css("title::text").get())
print(page.css(".quote .text::text").getall()[:3])
print("captured requests:", [x["url"] for x in page.captured_xhr][:5])
print(page.to_markdown(main_content_only=True)[:300])
