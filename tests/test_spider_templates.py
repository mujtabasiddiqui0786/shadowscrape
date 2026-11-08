from __future__ import annotations

import gzip

import pytest

from shadowscrape.spiders import LinkExtractorSpider, ListingDetailSpider, SitemapSpider, Spider
from test_spider_helpers import FakeSession, page


def bind(cls, session, **attrs):
    class B(cls):
        retry_backoff = 0.0

        def configure_sessions(self, m):
            m.add("default", session)

    for k, v in attrs.items():
        setattr(B, k, v)
    return B


NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(*urls):
    return '<?xml version="1.0"?><urlset %s>%s</urlset>' % (
        NS, "".join("<url><loc>%s</loc><lastmod>2024-01-01</lastmod></url>" % u for u in urls))


def test_sitemap_robots_index_gz_and_rules():
    sess = FakeSession({
        "http://a.test/robots.txt": "User-agent: *\nSitemap: http://a.test/index.xml\n",
        "http://a.test/index.xml": '<sitemapindex %s><sitemap><loc>http://a.test/s1.xml</loc></sitemap>'
                                   '<sitemap><loc>http://a.test/s2.xml.gz</loc></sitemap></sitemapindex>' % NS,
        "http://a.test/s1.xml": urlset("http://a.test/p/1", "http://a.test/blog/1"),
        "http://a.test/s2.xml.gz": lambda u, n, kw: __import__("test_spider_helpers").FakeResponse(
            u, 200, gzip.compress(urlset("http://a.test/p/2").encode())),
        "http://a.test/p/1": page(title="P1"), "http://a.test/p/2": page(title="P2"),
        "http://a.test/blog/1": page(title="B1"),
    })

    class S(bind(SitemapSpider, sess)):
        start_urls = ["http://a.test/some/page"]
        sitemap_rules = [(r"/p/", "parse_product"), (r"/blog/", "parse_blog")]

        def parse_product(self, response):
            return {"kind": "p", "lastmod": response.meta["lastmod"], "u": response.url}

        def parse_blog(self, response):
            return {"kind": "b", "u": response.url}

    res = S().start()
    assert sorted((i["kind"], i["u"]) for i in res.items) == [
        ("b", "http://a.test/blog/1"), ("p", "http://a.test/p/1"), ("p", "http://a.test/p/2")]
    assert res.items[0] is not None and any(i.get("lastmod") == "2024-01-01" for i in res.items)
    assert res.stats["sitemap_count"] == 3


def test_sitemap_fallback_discover_only_and_text():
    sess = FakeSession({
        "http://a.test/robots.txt": "User-agent: *\nDisallow:",
        "http://a.test/sitemap.xml": urlset("http://a.test/x", "http://a.test/y"),
    })
    res = bind(SitemapSpider, sess, start_urls=["http://a.test/"], discover_only=True)().start()
    assert sorted(i["url"] for i in res.items) == ["http://a.test/x", "http://a.test/y"]
    assert all(c[1] != "http://a.test/x" for c in sess.calls)

    sess2 = FakeSession({"http://a.test/list.txt": "http://a.test/1\nhttp://a.test/2\n# c\n"})
    res2 = bind(SitemapSpider, sess2, sitemap_urls=["http://a.test/list.txt"], discover_only=True)().start()
    assert len(res2.items) == 2


def test_sitemap_follow_filter():
    sess = FakeSession({
        "http://a.test/sitemap.xml": '<sitemapindex %s><sitemap><loc>http://a.test/keep.xml</loc></sitemap>'
                                     '<sitemap><loc>http://a.test/skip.xml</loc></sitemap></sitemapindex>' % NS,
        "http://a.test/keep.xml": urlset("http://a.test/k"),
        "http://a.test/skip.xml": urlset("http://a.test/s"),
    })
    res = bind(SitemapSpider, sess, sitemap_urls=["http://a.test/sitemap.xml"], sitemap_follow=["keep"],
               discover_only=True)().start()
    assert [i["url"] for i in res.items] == ["http://a.test/k"]


def test_link_extractor_depth_allow_deny_domain():
    sess = FakeSession({
        "http://a.test/": page(["/a", "/b/x", "http://other.test/z", "/img.png", "mailto:a@b", "/a#frag", "/skip/1"], "Home"),
        "http://a.test/a": page(["/deep"], "A"),
        "http://a.test/b/x": page([], "BX"),
        "http://a.test/deep": page(["/deeper"], "Deep"),
        "http://a.test/deeper": page([], "Deeper"),
    })
    res = bind(LinkExtractorSpider, sess, start_urls=["http://a.test/"], max_depth=2, deny=[r"/skip/"])().start()
    urls = {i["url"]: i for i in res.items}
    assert set(urls) == {"http://a.test/", "http://a.test/a", "http://a.test/b/x", "http://a.test/deep"}
    assert urls["http://a.test/deep"]["depth"] == 2 and urls["http://a.test/"]["title"] == "Home"

    res2 = bind(LinkExtractorSpider, sess, start_urls=["http://a.test/"], max_depth=1, allow=[r"^http://a\.test/b/"])().start()
    assert {i["url"] for i in res2.items} == {"http://a.test/", "http://a.test/b/x"}


def test_link_extractor_custom_parse_page_and_restrict_css():
    sess = FakeSession({
        "http://a.test/": '<div id="nav"><a href="/n">n</a></div><div id="main"><a href="/m">m</a></div>',
        "http://a.test/m": "<h1>M</h1>", "http://a.test/n": "<h1>N</h1>",
    })

    class S(bind(LinkExtractorSpider, sess, start_urls=["http://a.test/"], restrict_css="#main")):
        def parse_page(self, response):
            yield {"u": response.url}

    res = S().start()
    assert sorted(i["u"] for i in res.items) == ["http://a.test/", "http://a.test/m"]


LISTING1 = """<html><body>
<div class="card"><a class="t" href="/item/1">One</a><span class="price">$1</span></div>
<div class="card"><a class="t" href="/item/2">Two</a><span class="price">$2</span></div>
<a class="next" href="/list?p=2">Next</a></body></html>"""
LISTING2 = """<html><body>
<div class="card"><a class="t" href="/item/3">Three</a><span class="price">$3</span></div>
<a class="next" href="/list?p=3">Next</a></body></html>"""
DETAIL = """<html><body><h1>{n}</h1><p class="d">desc {n}</p><ul><li>a</li><li>b</li></ul>
<img class="hero" src="/img/{n}.jpg"></body></html>"""


def _ld_session():
    return FakeSession({
        "http://a.test/list": LISTING1, "http://a.test/list?p=2": LISTING2, "http://a.test/list?p=3": "<html></html>",
        "http://a.test/item/1": DETAIL.format(n=1), "http://a.test/item/2": DETAIL.format(n=2),
        "http://a.test/item/3": DETAIL.format(n=3),
    })


def test_listing_detail_pagination_and_fields():
    sess = _ld_session()
    res = bind(
        ListingDetailSpider, sess, start_urls=["http://a.test/list"], item_selector="div.card",
        detail_link_selector="a.t::attr(href)", next_page_selector="a.next::attr(href)",
        listing_fields={"price": ".price"},
        fields={"title": "h1", "desc": "p.d", "tags": ("li", "all"), "image": "img.hero::attr(src)",
                "missing": {"css": ".nope", "default": "n/a"}},
    )().start()
    items = sorted(res.items, key=lambda i: i["url"])
    assert [i["title"] for i in items] == ["1", "2", "3"]
    assert items[0]["price"] == "$1" and items[2]["price"] == "$3"
    assert items[0]["tags"] == ["a", "b"] and items[0]["image"] == "http://a.test/img/1.jpg"
    assert items[0]["missing"] == "n/a" and items[0]["desc"] == "desc 1"


def test_listing_detail_max_pages_and_cards_only():
    sess = _ld_session()
    res = bind(ListingDetailSpider, sess, start_urls=["http://a.test/list"], item_selector="div.card",
               next_page_selector="a.next::attr(href)", max_pages=2,
               fields={"name": "a.t", "link": "a.t::attr(href)", "price": ".price"})().start()
    assert [i["name"] for i in res.items] in (["One", "Two", "Three"],)
    assert res.items[0]["link"] == "http://a.test/item/1"
    assert not any(c[1].endswith("p=3") for c in sess.calls)


def test_listing_detail_post_process_drop():
    sess = _ld_session()

    class S(bind(ListingDetailSpider, sess, start_urls=["http://a.test/list"], item_selector="div.card",
                 detail_link_selector="a.t::attr(href)", fields={"title": "h1"})):
        def post_process(self, item, response):
            return None if item["title"] == "2" else item

    res = S().start()
    assert sorted(i["title"] for i in res.items) == ["1"]


# ---------------------------------------------------------- integration
def _integration_available():
    try:
        import shadowscrape.fetchers  # noqa
        import shadowscrape.response  # noqa
        return True
    except Exception:
        return False


integration = pytest.mark.skipif(not _integration_available(), reason="fetchers/response not importable yet")


@integration
def test_integration_default_session_class_resolves():
    from shadowscrape.fetchers import AsyncFetcherSession
    assert AsyncFetcherSession is not None


@integration
def test_integration_local_server_crawl():
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"/": page(["/a"], "root"), "/a": page([], "a")}.get(self.path, "")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]

        class S(LinkExtractorSpider):
            start_urls = ["http://127.0.0.1:%d/" % port]

        res = S().start()
        assert sorted(i["title"] for i in res.items) == ["a", "root"]
    finally:
        srv.shutdown()
