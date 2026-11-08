# Parsing cheat sheet

```python
from shadowscrape import Selector
page = Selector("<html>...</html>")   # or any fetcher Response
```

| Task | Call |
|---|---|
| CSS (with `::text`, `::attr(href)`) | `page.css("div.item a::attr(href)")` -> `Selectors` |
| First match or None | `page.css_first("h1")` |
| XPath | `page.xpath("//a[@href]")` |
| Filter by tag/attrs/regex/function | `page.find_all("a", {"class": "x"})`, `page.find(...)` |
| By text | `page.find_by_text("Add to cart", partial=True)` |
| Regex | `page.re(r"\$(\d+)")`, `page.re_first(...)` |
| Similar elements | `el.find_similar()` |
| Navigate | `el.parent .children .siblings .next_sibling .previous_sibling` |
| Output | `el.text`, `el.get_all_text()`, `el.attrib`, `el.html_content`, `.get()`, `.getall()`, `.json()`, `el.to_markdown(main_content_only=True)` |
| Selector for an element | `el.generate_css_selector`, `el.generate_xpath_selector` |

`Selectors` is a list; `css/xpath/re/get/getall/first/last` map over members.

## Adaptive scraping (survive redesigns)
```python
page = Selector(html, adaptive=True, storage_path="site.db")
page.css("#price", identifier="price", auto_save=True)      # first run: remember element
page.css("#price", identifier="price", adaptive=True)       # later: relocates by similarity if selector broke
```

## Tips
- Select the smallest container then iterate: `for card in page.css(".card"): card.css_first(".t::text")`.
- Use `main_content_only=True` Markdown to read an article without nav/footer noise.
