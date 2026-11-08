---
name: shadowscrape
description: Scrape and extract data from websites with the shadowscrape Python library, including JavaScript-heavy and bot-protected (Cloudflare) pages. Use when asked to fetch a URL, scrape or crawl a site, extract elements with CSS/XPath, build a spider, bypass anti-bot blocking, or turn a page into Markdown for analysis.
---

# shadowscrape

Python web scraping framework: fast HTTP with browser TLS impersonation, Playwright browsers, a stealth browser with anti-detection and Cloudflare solving, an adaptive HTML parser, and a spider framework. Install: `pip install shadowscrape` then `shadowscrape install` (downloads Chromium, only needed for browser fetchers).

## Pick the cheapest fetcher that works

1. `Fetcher` (HTTP, `get/post/put/delete`): static pages, JSON APIs, light bot checks. Fastest.
2. `DynamicFetcher` (Playwright Chromium): content rendered by JavaScript. Use `wait_selector=` for late content.
3. `StealthyFetcher`: sites that block 1 and 2 (fingerprinting, Cloudflare). Add `solve_cloudflare=True` for Turnstile/interstitial.

Escalate only when the cheaper one returns a block page (403/429/503, "Just a moment", empty shell with no data).

## From the CLI (no code)

```bash
shadowscrape get https://example.com page.md --css "article" --main-content-only
shadowscrape stealthy-fetch https://protected.site out.html --solve-cloudflare
```
Output extension picks the format: `.html` raw, `.md` Markdown, `.txt` text, `.json` metadata+content.

## From MCP (agents)

`shadowscrape mcp` exposes `get`, `bulk_get`, `fetch`, `bulk_fetch`, `stealthy_fetch`, `bulk_stealthy_fetch`. ALWAYS pass `css_selector` when you know what you need, so only relevant content enters your context. See `references/mcp.md`.

## From Python

```python
from shadowscrape import Fetcher, StealthyFetcher

page = Fetcher.get("https://quotes.toscrape.com", impersonate="chrome")
for q in page.css(".quote"):
    print(q.css_first(".text::text").get(), q.css(".author::text").get())

page = StealthyFetcher.fetch("https://protected.example", solve_cloudflare=True, wait_selector=".product")
titles = page.css(".product h2::text").getall()
```

`Response` is also a `Selector`: `css`, `css_first`, `xpath`, `find_all`, `find_by_text`, `re`, `to_markdown()`, `get_all_text()`, plus `status`, `headers`, `cookies`, `url`.

## Guidance

- Inspect first: fetch once, print `page.status` and `page.to_markdown()[:2000]`, then write selectors. Don't guess selectors.
- Prefer JSON/XHR endpoints over HTML when they exist (`capture_xhr=` on browser fetchers).
- Many pages: use sessions (`FetcherSession`) or a `Spider` with `concurrent_requests` and a `crawldir` to pause/resume. See `references/spiders.md`.
- Layouts change: `css(..., auto_save=True)` once, then `css(..., adaptive=True)` later re-finds elements. See `references/parsing.md`.
- Use `proxy=` or a `ProxyRotator` for blocks by IP; throttle with `download_delay`/autothrottle.
- Respect robots.txt, terms of service and rate limits; do not scrape personal data or log in to accounts you do not own.
- If a request fails, report status code and what you tried; do not loop endlessly.

## References

- `references/fetchers.md` fetcher classes and options
- `references/parsing.md` Selector/Selectors API, adaptive scraping
- `references/spiders.md` Spider framework
- `references/mcp.md` MCP server tools and setup
- `references/cli.md` CLI and shell cheat sheet
