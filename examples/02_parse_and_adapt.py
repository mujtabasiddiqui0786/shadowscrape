"""Parse HTML offline: search, similar elements, Markdown, and adaptive selectors after a redesign."""
from __future__ import annotations

import tempfile
from pathlib import Path

from shadowscrape import Selector

OLD = """
<html><body><main><h1>Shop</h1>
<div class="product"><span class="price-tag">$10.00</span></div>
<div class="product"><span class="price-tag">$12.50</span></div>
</main></body></html>
"""

# A redesign renames the classes and moves things around.
NEW = """
<html><body><section id="catalog"><h2>Shop</h2>
<article class="item"><b class="cost">$10.00</b></article>
<article class="item"><b class="cost">$12.50</b></article>
</section></body></html>
"""

store = str(Path(tempfile.mkdtemp()) / "adaptive.sqlite")
url = "https://shop.example/"

before = Selector(OLD, url=url, adaptive=True, storage_path=store)
# Select elements (not ::text) so there is something to fingerprint, then read .text from them.
prices = before.css(".price-tag", identifier="price", auto_save=True)
print("before:", [p.text for p in prices])

after = Selector(NEW, url=url, adaptive=True, storage_path=store)
print("plain selector after redesign:", [p.text for p in after.css(".price-tag")])
relocated = after.css(".price-tag", identifier="price", adaptive=True)
print("adaptive selector after redesign:", [p.text for p in relocated])

print(after.find_by_text("Shop").text)
print(after.to_markdown())
