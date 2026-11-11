# shadowscrape

A Python web scraping framework: fast HTTP with browser TLS impersonation, stealth browsers, adaptive parsing, and concurrent crawling in one library.

```bash
pip install -e .                 # core: parser, HTTP fetchers, spiders, CLI
pip install -e ".[stealth]"      # + Playwright/patchright stealth browsers
pip install -e ".[all]"          # + MCP + IPython shell
shadowscrape install        # downloads Chromium for playwright and patchright
```

Python 3.9+. The MCP server is a built-in JSON-RPC implementation, so it also works on Python 3.9, where the official `mcp` SDK doesn't install.

## Fetch

```python
from shadowscrape import Fetcher, FetcherSession, StealthyFetcher, DynamicFetcher

page = Fetcher.get("https://example.com", impersonate="chrome131")   # TLS/JA3/HTTP2 impersonation
print(page.status, page.css("h1::text").get())

with FetcherSession(impersonate="random") as s:                      # persistent cookies, sticky fingerprint
    s.get("https://example.com/login")

page = StealthyFetcher.fetch(
    "https://example.com",
    solve_cloudflare=True,      # humanized click on Turnstile / interstitial, no third-party solvers
    humanize=True,
    capture_xhr=r"/api/",       # background API capture -> page.captured_xhr
    wait_selector=".loaded",
)
```

- **HTTP:** `Fetcher`, `AsyncFetcher`, `FetcherSession`, `AsyncFetcherSession`. Retries with backoff and jitter, `Retry-After`, `ProxyRotator` (cyclic, random, least_used, cooldown), redirect history, consistent per-browser headers.
- **Browsers:** `DynamicFetcher` (plain Playwright) and `StealthyFetcher` (consistent fingerprint profiles, JS evasions, CDP-leak mitigation via patchright when installed, WebRTC block, humanized input, Turnstile handling). Sync and async sessions that reuse a browser context.

## Parse

```python
page.css("a::attr(href)").getall()
page.xpath("//li/text()").getall()
page.find_by_text("Add to cart", partial=True)
page.find_by_regex(r"\$\d+\.\d\d")
page.css(".price", identifier="price", auto_save=True)   # save a fingerprint (select elements, not ::text)
page.css(".price", identifier="price", adaptive=True)    # relocate it after a redesign
page.css(".item").first.find_similar()
print(page.to_markdown(main_content_only=True))
```

## Crawl

```python
from shadowscrape.spiders import Spider, LinkExtractorSpider, SitemapSpider

class Quotes(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com"]
    concurrent_requests = 8
    autothrottle = True
    crawldir = "./crawl-state"          # Ctrl+C pauses; rerun resumes

    async def parse(self, response):
        for q in response.css(".quote"):
            yield {"text": q.css(".text::text").get(), "author": q.css(".author::text").get()}
        if nxt := response.css("li.next a::attr(href)").get():
            yield self.follow(response, nxt)

result = Quotes().start()
result.to_json("quotes.json"); result.to_jsonl("quotes.jsonl"); result.to_csv("quotes.csv"); result.to_xml("quotes.xml")
```

Priority queue, fingerprint dedup, per-domain concurrency, AutoThrottle, retries with proxy rotation and blocked-response detection, optional robots.txt (off by default), streaming (`async for item in spider.stream()`), and `SitemapSpider`, `LinkExtractorSpider`, `ListingDetailSpider` templates.

## CLI, shell, MCP, Agent Skill

```bash
shadowscrape get https://example.com out.md --main-content-only
shadowscrape stealthy-fetch https://example.com out.html --solve-cloudflare
shadowscrape shell        # IPython or stdlib console; includes curl2fetcher("curl ...")
shadowscrape mcp          # MCP server over stdio for AI agents
```

MCP tools: `get`, `bulk_get`, `fetch`, `bulk_fetch`, `stealthy_fetch`, `bulk_stealthy_fetch`, each with `css_selector`, `main_content_only` and `extraction_type` so agents get only the data they need. The Agent Skill is in `skills/shadowscrape/`.

## Stealth: what is and isn't proven

Reproduce with `python -m benchmarks.detectors` (see [benchmarks/README.md](benchmarks/README.md)). Raw results are in `benchmarks/results/`.

One run, macOS arm64, headless Chromium, 3 runs per cell:

| target | plain Playwright | shadowscrape |
|---|---|---|
| local-probe (12 checks, ours) | fail | pass |
| sannysoft | fail | pass |
| creepjs | fail | pass |
| browserscan | fail | pass |
| nowsecure | pass | pass |
| areyouheadless | site returned 502 | site returned 502 |

How to read this:
- `local-probe` is a test page we wrote ourselves, and the CreepJS pass/fail rule is our own parser's threshold. Treat them as weaker evidence than the third-party pages.
- It is one machine, one OS, headless only.
- CreepJS's "like headless" score is still 31% for shadowscrape, not zero.

On live Cloudflare practice pages (`python -m benchmarks.permitted_targets`, single runs): plain Playwright was stuck on ScrapingCourse's challenge and shadowscrape passed it, while both were hard-blocked on NopeCHA's demo. One pass and one block from one IP is a data point, not a reliability figure.

Public detector pages test fingerprint consistency. They do **not** test IP reputation, behavioural or ML scoring, or vendor JS challenges. Passing them is not a claim of bypassing Cloudflare, Akamai, DataDome, PerimeterX or Kasada, and none of those were tested. The Turnstile solver is verified only against a local mock. For non-browser requests use `Fetcher`, because a browser's own TLS fingerprint is left untouched.

## Use responsibly

Scrape only what you are allowed to. Respect site terms and applicable law, rate-limit yourself, and enable robots.txt where appropriate (`robots_txt_obey = True`).

## Tests

```bash
.venv/bin/python -m pytest      # offline; browser tests need chromium installed
```
