"""Compare plain Playwright and the stealth fetcher on pages whose owners publish them for scraper testing.

Run: python -m benchmarks.permitted_targets (needs chromium). Only add targets that invite testing.
"""
from __future__ import annotations
import time
from playwright.sync_api import sync_playwright
from shadowscrape.fetchers.browser import StealthySession

# Pages whose owners publish them for testing scrapers / bot detection.
TARGETS = {
    "scrapingcourse-cloudflare": "https://www.scrapingcourse.com/cloudflare-challenge",
    "nopecha-cloudflare-demo": "https://nopecha.com/demo/cloudflare",
    "incolumitas-bot-test": "https://bot.incolumitas.com/",
}

def plain(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page(); pg.goto(url, wait_until="load", timeout=45000); pg.wait_for_timeout(6000)
            return pg.title(), pg.inner_text("body")[:300].replace("\n", " ")
        finally:
            b.close()

def stealthy(url):
    with StealthySession(headless=True) as s:
        r = s.fetch(url, wait=6000, timeout=60000, solve_cloudflare=True, humanize=True)
        return r.css("title::text").get(), r.get_all_text()[:300].replace("\n", " ")

for name, url in TARGETS.items():
    print("\n==", name, url)
    for label, fn in (("plain", plain), ("stealthy", stealthy)):
        t = time.time()
        try:
            title, txt = fn(url)
            print(f"[{label}] {time.time()-t:.0f}s title={title!r}\n   {txt}")
        except Exception as e:
            print(f"[{label}] ERROR {type(e).__name__}: {str(e)[:150]}")
