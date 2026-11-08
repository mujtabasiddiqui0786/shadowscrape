"""Crawl every page of quotes.toscrape.com, then export the items. Ctrl+C pauses; rerun to resume."""
from __future__ import annotations

from shadowscrape.spiders import Spider


class Quotes(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com"]
    concurrent_requests = 4
    autothrottle = True
    crawldir = "./crawl-state"

    async def parse(self, response):
        for quote in response.css(".quote"):
            yield {
                "text": quote.css(".text::text").get(),
                "author": quote.css(".author::text").get(),
                "tags": quote.css(".tag::text").getall(),
            }
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield self.follow(response, next_page)


if __name__ == "__main__":
    result = Quotes().start()
    print(len(result.items), "items;", result.stats.get("finish_reason"))
    result.to_json("quotes.json")
    result.to_jsonl("quotes.jsonl")
    result.to_csv("quotes.csv")
