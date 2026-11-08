# Spiders cheat sheet

```python
from shadowscrape.spiders import Spider, Request

class Quotes(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com"]
    concurrent_requests = 8
    download_delay = 0.5
    autothrottle = True
    max_retries = 3
    crawldir = "./crawl_state"        # Ctrl+C checkpoints here; rerun resumes

    async def parse(self, response):
        for q in response.css(".quote"):
            yield {"text": q.css_first(".text::text").get(), "author": q.css_first(".author::text").get()}
        nxt = response.css_first("li.next a::attr(href)")
        if nxt:
            yield Request(response.urljoin(nxt.get()), callback=self.parse)

result = Quotes().start()
result.to_json("quotes.json")   # .to_jsonl .to_csv .to_xml; result.stats
```

- `Request(url, callback, method, headers, data, meta, priority, dont_filter, sid, cb_kwargs)`.
- Mix fetchers per request via named sessions in `configure_sessions(manager)` and `sid=`.
- `async for item in Spider().stream()` for live streaming.
- Options: `allowed_domains`, `proxy_rotator`, `robots_txt_obey=True` (default off).
- Templates in `shadowscrape.spiders.templates`: `SitemapSpider`, `LinkExtractorSpider` (whole-site crawl), `ListingDetailSpider`.
