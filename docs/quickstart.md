# Quickstart

```bash
pip install -e ".[stealth]"
shadowscrape install        # downloads Chromium for playwright and patchright
```

```python
from shadowscrape import Fetcher, StealthyFetcher

page = Fetcher.get("https://quotes.toscrape.com", impersonate="chrome131")
for q in page.css(".quote"):
    print(q.css(".text::text").get(), "-", q.css(".author::text").get())

page = StealthyFetcher.fetch("https://example.com")
print(page.to_markdown(main_content_only=True))
```

Runnable examples are in the repository's `examples/` folder.
