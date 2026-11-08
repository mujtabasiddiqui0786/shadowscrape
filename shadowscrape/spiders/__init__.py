"""Crawling framework: Spider, Request, scheduler, exporters and ready-made templates."""
from __future__ import annotations

from .request import Request, canonicalize_url
from .result import CrawlResult, JSONLWriter, export_csv, export_json, export_jsonl, export_xml
from .scheduler import AutoThrottle, Scheduler
from .sessions import SessionAdapter, SessionManager
from .spider import Spider
from .stats import StatsCollector
from .templates import LinkExtractorSpider, ListingDetailSpider, SitemapSpider

__all__ = [
    "Spider", "Request", "CrawlResult", "JSONLWriter", "SessionManager", "SessionAdapter",
    "Scheduler", "AutoThrottle", "StatsCollector", "canonicalize_url",
    "export_json", "export_jsonl", "export_csv", "export_xml",
    "SitemapSpider", "LinkExtractorSpider", "ListingDetailSpider",
]
