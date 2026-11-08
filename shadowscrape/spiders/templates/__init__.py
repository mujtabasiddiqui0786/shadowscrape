"""Ready-made spiders."""
from __future__ import annotations

from .link_extractor import LinkExtractorSpider
from .listing_detail import ListingDetailSpider
from .sitemap import SitemapSpider

__all__ = ["SitemapSpider", "LinkExtractorSpider", "ListingDetailSpider"]
