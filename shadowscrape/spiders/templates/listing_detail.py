"""ListingDetailSpider: paginate listing pages and scrape detail pages with CSS field maps."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Sequence

from .._util import base_url_of, extract_fields, get_doc, select
from ..request import Request
from ..spider import Spider


class ListingDetailSpider(Spider):
    """Listing pagination + detail pages.

    * ``start_urls``: first listing page(s).
    * ``item_selector``: CSS for each card on a listing page (optional).
    * ``detail_link_selector``: CSS (``a.title::attr(href)``) for detail links,
      evaluated inside each card if ``item_selector`` is set, else page-wide.
      When omitted, items are extracted straight from listing cards.
    * ``listing_fields``: field map evaluated on each card, merged into the detail item.
    * ``fields``: field map evaluated on the detail page (or on cards when there
      are no detail pages).  Spec: ``"css"``, ``("css", "all")`` or
      ``{"css"|"xpath": ..., "all": bool, "default": ...}``; bare selectors yield
      text, ``::attr(x)`` attributes.
    * ``next_page_selector``: CSS for the next-page link; ``max_pages`` caps pages.
    """

    name = "listing_detail"
    item_selector: Optional[str] = None
    detail_link_selector: Optional[str] = None
    next_page_selector: Optional[str] = None
    fields: Dict[str, Any] = {}
    listing_fields: Dict[str, Any] = {}
    max_pages: Optional[int] = None

    def post_process(self, item: Dict[str, Any], response: Any) -> Optional[Dict[str, Any]]:
        """Hook to clean/convert an item; return None to drop."""
        return item

    async def parse(self, response: Any) -> AsyncIterator[Any]:
        doc = get_doc(response)
        base = base_url_of(response, doc)
        page = int(getattr(response, "meta", {}).get("page", 1))
        if self.item_selector:
            from .._util import _xpath_for
            cards = doc.xpath(_xpath_for(self.item_selector))
        else:
            cards = [doc]
        if self.detail_link_selector:
            for card in cards:
                data = extract_fields(card, self.listing_fields, base) if self.listing_fields else {}
                for link in select(card, self.detail_link_selector, base):
                    yield Request(link, callback="parse_detail", cb_kwargs={"listing_data": data})
        elif self.item_selector:
            for card in cards:
                item = {"url": response.url, **extract_fields(card, self.listing_fields, base),
                        **extract_fields(card, self.fields, base)}
                item = self.post_process(item, response)
                if item is not None:
                    yield item
        if self.next_page_selector and (self.max_pages is None or page < self.max_pages):
            nxt = select(doc, self.next_page_selector, base)
            if nxt:
                yield Request(nxt[0], callback="parse", meta={"page": page + 1})

    async def parse_detail(self, response: Any, listing_data: Optional[Dict[str, Any]] = None) -> AsyncIterator[Any]:
        doc = get_doc(response)
        item = {"url": response.url, **(listing_data or {}), **extract_fields(doc, self.fields, base_url_of(response, doc))}
        item = self.post_process(item, response)
        if item is not None:
            yield item
