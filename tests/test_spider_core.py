from __future__ import annotations

import asyncio
import csv
import json
import time
import xml.etree.ElementTree as ET

import pytest

from shadowscrape.spiders import AutoThrottle, CrawlResult, JSONLWriter, Request, Scheduler, Spider, canonicalize_url
from test_spider_helpers import FakeResponse, FakeRotator, FakeSession, page


def make(session, **attrs):
    """Build a Spider subclass instance bound to a fake session."""
    class S(Spider):
        name = "t"
        start_urls = ["http://a.test/"]
        retry_backoff = 0.0

        def configure_sessions(self, manager):
            manager.add("default", session)

        async def parse(self, response):
            yield {"url": response.url}

    for k, v in attrs.items():
        setattr(S, k, v)
    return S


# ------------------------------------------------------------------ units
def test_canonicalize_and_fingerprint():
    a = Request("HTTP://A.test:80/p?b=2&a=1#frag")
    b = Request("http://a.test/p?a=1&b=2")
    assert canonicalize_url(a.url) == "http://a.test/p?a=1&b=2"
    assert a.fingerprint() == b.fingerprint()
    assert Request("http://a.test/p").fingerprint() != Request("http://a.test/p", method="POST", data="x").fingerprint()
    assert Request("http://a.test/", data=b"1").fingerprint() != Request("http://a.test/", data=b"2").fingerprint()


def test_request_roundtrip_dict():
    r = Request("http://a.test/", callback="parse_x", data=b"\x00\x01", meta={"k": 1, "_not_before": 5},
                priority=3, sid="s", cb_kwargs={"a": 1})
    d = json.loads(json.dumps(r.to_dict()))
    r2 = Request.from_dict(d)
    assert r2.data == b"\x00\x01" and r2.callback == "parse_x" and r2.meta == {"k": 1} and r2.sid == "s"


def test_scheduler_priority_fifo_and_dedup():
    s = Scheduler()
    assert s.push(Request("http://a.test/1", priority=0))
    assert s.push(Request("http://a.test/2", priority=5))
    assert s.push(Request("http://a.test/3", priority=0))
    assert not s.push(Request("http://a.test/1#x"))  # dup
    assert s.push(Request("http://a.test/1", dont_filter=True))
    order = []
    while len(s):
        r, _ = s.pop_ready(0)
        order.append(r.url.rsplit("/", 1)[1])
    assert order == ["2", "1", "3", "1"]


def test_scheduler_per_domain_and_delay():
    s = Scheduler(per_domain=1)
    s.push(Request("http://a.test/1"))
    s.push(Request("http://a.test/2"))
    s.push(Request("http://b.test/1"))
    r1, _ = s.pop_ready(0)
    s.started(r1, next_time=0)
    r2, _ = s.pop_ready(0)
    assert r2.url == "http://b.test/1"  # a.test is at its limit
    s.started(r2, 0)
    r3, wait = s.pop_ready(0)
    assert r3 is None and wait is None
    s.finished(r1)
    s.started(r1, next_time=10)  # simulate delay window
    s.finished(r1)
    r3, wait = s.pop_ready(1.0)
    assert r3 is None and wait == pytest.approx(9.0)
    r3, _ = s.pop_ready(10.5)
    assert r3.url == "http://a.test/2"


def test_autothrottle_math():
    at = AutoThrottle(start_delay=1.0, target_concurrency=2.0, min_delay=0.1, max_delay=5.0)
    assert at.delay_for("d") == 1.0
    # latency .2 -> target .1 ; avg(1.0,.1)=.55 -> .55
    assert at.observe("d", 0.2) == pytest.approx(0.55)
    # latency 4 -> target 2; avg(.55,2)=1.275 -> max(target)=2
    assert at.observe("d", 4.0) == pytest.approx(2.0)
    # bad response cannot lower delay
    assert at.observe("d", 0.0, ok=False) == pytest.approx(2.0)
    # clamp
    assert at.observe("d", 100.0) == 5.0
    assert at.observe("d", 0.0) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        AutoThrottle(target_concurrency=0)


# ------------------------------------------------------------------ crawl
def test_basic_crawl_dedup_and_stats():
    sess = FakeSession({
        "http://a.test/": page(["/1", "/2", "/1", "/1#f"]),
        "http://a.test/1": page(["/", "/2"]),
        "http://a.test/2": page([]),
    })

    class S(make(sess)):
        async def parse(self, response):
            yield {"url": response.url}
            for h in response.text.split('href="')[1:]:
                yield Request(response.url.rsplit("/", 1)[0] + h.split('"')[0] if h.startswith("/") else h.split('"')[0])

    res = S().start()
    assert isinstance(res, CrawlResult) and not res.paused
    assert sorted(i["url"] for i in res.items) == ["http://a.test/", "http://a.test/1", "http://a.test/2"]
    assert res.stats["request_count"] == 3 and res.stats["item_count"] == 3
    assert res.stats["dedup_filtered"] >= 3
    assert res.stats["finish_reason"] == "finished"
    assert sess.entered and sess.exited


def test_callbacks_cb_kwargs_priority_and_depth():
    sess = FakeSession({"http://a.test/": "x", "http://a.test/hi": "x", "http://a.test/lo": "x"})
    order = []

    class S(make(sess, concurrent_requests=1)):
        async def parse(self, response):
            yield Request("http://a.test/lo", callback=self.other, priority=1, cb_kwargs={"tag": "lo"})
            yield Request("http://a.test/hi", callback="other", priority=9, cb_kwargs={"tag": "hi"})

        def other(self, response, tag):
            order.append((tag, response.meta["depth"]))
            return {"tag": tag}

    res = S().start()
    assert order == [("hi", 1), ("lo", 1)]
    assert [i["tag"] for i in res.items] == ["hi", "lo"]


def test_sync_session_and_sync_callback_and_process_item():
    sess = FakeSession({"http://a.test/": "<b>1</b>"}, sync=True)

    class S(make(sess)):
        def parse(self, response):
            return [{"n": 1}, {"n": 2}, None]

        def process_item(self, item):
            return None if item["n"] == 2 else dict(item, ok=True)

    res = S().start()
    assert res.items == [{"n": 1, "ok": True}]
    assert res.stats["item_dropped"] == 1


def test_named_sessions_sid_and_post():
    a = FakeSession({"http://a.test/": "x", "http://a.test/p": "posted"})
    b = FakeSession({"http://a.test/b": "from b"})

    class S(Spider):
        start_urls = ["http://a.test/"]

        def configure_sessions(self, m):
            m.add("a", a, default=True)
            m.add("b", b)

        async def parse(self, response):
            yield Request("http://a.test/b", sid="b", callback="done")
            yield Request("http://a.test/p", method="POST", data={"k": "v"}, headers={"X": "1"}, callback="done")

        def done(self, response):
            return {"u": response.url, "t": response.text}

    res = S().start()
    assert {i["t"] for i in res.items} == {"from b", "posted"}
    assert [c[1] for c in b.calls] == ["http://a.test/b"]
    post = [c for c in a.calls if c[0] == "POST"][0]
    assert post[2]["data"] == {"k": "v"} and post[2]["headers"] == {"X": "1"}


def test_allowed_domains_offsite():
    sess = FakeSession({"http://a.test/": "x", "http://sub.a.test/": "x", "http://evil.test/": "x"})

    class S(make(sess, allowed_domains=["a.test"])):
        async def parse(self, response):
            if response.url == "http://a.test/":
                yield Request("http://sub.a.test/")
                yield Request("http://evil.test/")
                yield Request("mailto:x@y")
            yield {"u": response.url}

    res = S().start()
    assert sorted(i["u"] for i in res.items) == ["http://a.test/", "http://sub.a.test/"]
    assert res.stats["offsite_filtered"] == 1 and res.stats["invalid_url_count"] == 1


def test_concurrency_limits():
    routes = {"http://a.test/": "x"}
    for i in range(12):
        routes["http://a.test/%d" % i] = "x"
        routes["http://b.test/%d" % i] = "x"
    sess = FakeSession(routes, latency=0.02)

    class S(make(sess, concurrent_requests=6, concurrent_requests_per_domain=2)):
        async def parse(self, response):
            if response.url == "http://a.test/":
                for i in range(12):
                    yield Request("http://a.test/%d" % i, callback="leaf")
                    yield Request("http://b.test/%d" % i, callback="leaf")

        def leaf(self, response):
            return {"u": response.url}

    res = S().start()
    assert len(res.items) == 24
    assert sess.max_active <= 4  # 2 domains x 2
    assert sess.max_active >= 3


def test_download_delay():
    sess = FakeSession({"http://a.test/": "x", "http://a.test/1": "x", "http://a.test/2": "x"})

    class S(make(sess, download_delay=0.1)):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/1")
                yield Request("http://a.test/2")

    t = time.monotonic()
    S().start()
    assert time.monotonic() - t >= 0.19


def test_autothrottle_adapts_delay_in_crawl():
    sess = FakeSession({"http://a.test/": "x", "http://a.test/1": "x"}, latency=0.05)

    class S(make(sess, autothrottle=True, autothrottle_start_delay=0.0, autothrottle_target_concurrency=1.0)):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/1")

    res = S().start()
    d = res.stats["autothrottle_delay_a.test"]
    assert 0.04 <= d <= 0.2


# ------------------------------------------------- retries / proxies / blocked
def test_retry_with_proxy_rotation_and_blocked_detection():
    def route(url, n, kw):
        return (403, "denied") if n < 3 else (200, "ok")

    sess = FakeSession({"http://a.test/": route})
    rot = FakeRotator(["p1", "p2", "p3", "p4"])
    res = make(sess, proxy_rotator=rot, max_retries=3)().start()
    assert [c[2].get("proxy") for c in sess.calls] == ["p1", "p2", "p3"]
    assert rot.failed == ["p1", "p2"] and rot.ok == ["p3"]
    assert res.stats["retry_count"] == 2 and res.stats["blocked_count"] == 2
    assert len(res.items) == 1


def test_retries_exhausted_calls_on_failure():
    sess = FakeSession({"http://a.test/": (503, "x")})
    failures = []

    class S(make(sess, max_retries=2)):
        def on_failure(self, request, response=None, exc=None):
            failures.append((request.url, response.status))

    res = S().start()
    assert len(sess.calls) == 3 and res.items == []
    assert res.stats["failed_count"] == 1 and failures == [("http://a.test/", 503)]


def test_custom_is_blocked_hook():
    sess = FakeSession({"http://a.test/": lambda u, n, kw: "captcha" if n == 1 else "fine"})

    class S(make(sess)):
        def is_blocked(self, response):
            return "captcha" in response.text

    res = S().start()
    assert res.stats["blocked_count"] == 1 and len(res.items) == 1


def test_exception_is_retried_and_404_dropped():
    def boom(url, n, kw):
        if n == 1:
            raise ConnectionError("net down")
        return "ok"

    sess = FakeSession({"http://a.test/": boom, "http://a.test/x": (404, "nf")})

    class S(make(sess)):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/x")
            yield {"u": response.url}

    res = S().start()
    assert res.stats["error_count"] == 1 and res.stats["retry_count"] == 1
    assert res.stats["http_error_count"] == 1 and [i["u"] for i in res.items] == ["http://a.test/"]


def test_retry_after_header_honoured():
    sess = FakeSession({"http://a.test/": lambda u, n, kw: (429, "slow", {"Retry-After": "0.2"}) if n == 1 else "ok"})
    t = time.monotonic()
    res = make(sess, retry_backoff=0.0)().start()
    assert time.monotonic() - t >= 0.19 and len(res.items) == 1


def test_callback_error_does_not_kill_crawl():
    sess = FakeSession({"http://a.test/": "x", "http://a.test/1": "x"})
    errs = []

    class S(make(sess)):
        async def parse(self, response):
            yield Request("http://a.test/1", callback="bad")
            yield {"ok": 1}

        def bad(self, response):
            raise ValueError("x")

        def on_callback_error(self, request, exc):
            errs.append(exc)

    res = S().start()
    assert res.stats["callback_errors"] == 1 and len(errs) == 1 and res.items == [{"ok": 1}]


# ------------------------------------------------------------------ robots
def test_robots_txt_obey_and_default_off():
    routes = {
        "http://a.test/robots.txt": "User-agent: *\nDisallow: /private\nCrawl-delay: 0",
        "http://a.test/": "x", "http://a.test/private/x": "x", "http://a.test/ok": "x",
    }

    class Mk(make(FakeSession(routes))):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/private/x")
                yield Request("http://a.test/ok")
            yield {"u": response.url}

    s_on = FakeSession(routes)
    res = make(s_on, robots_txt_obey=True)
    class On(res):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/private/x")
                yield Request("http://a.test/ok")
            yield {"u": response.url}
    r = On().start()
    assert sorted(i["u"] for i in r.items) == ["http://a.test/", "http://a.test/ok"]
    assert r.stats["robots_blocked"] == 1

    s_off = FakeSession(routes)
    class Off(make(s_off)):
        parse = On.parse
    r2 = Off().start()
    assert len(r2.items) == 3
    assert not any(c[1].endswith("robots.txt") for c in s_off.calls)


# --------------------------------------------------------- pause / resume
def test_pause_resume_roundtrip(tmp_path):
    routes = {"http://a.test/": "x"}
    for i in range(6):
        routes["http://a.test/%d" % i] = "x"
    sess1 = FakeSession(routes)
    cd = str(tmp_path / "crawl")

    def build(sess, pause_at):
        class S(make(sess, concurrent_requests=1, crawldir=cd)):
            async def parse(self, response):
                if response.url.endswith("/"):
                    for i in range(6):
                        yield Request("http://a.test/%d" % i, callback="leaf", cb_kwargs={"i": i})
                yield {"u": response.url}

            async def leaf(self, response, i):
                if i == pause_at:
                    self.pause()
                yield {"u": response.url, "i": i}
        return S()

    r1 = build(sess1, 2).start()
    assert r1.paused and r1.stats["finish_reason"] == "paused"
    assert len(r1.items) == 4  # root + 0,1,2
    ck = json.load(open(tmp_path / "crawl" / "checkpoint.json"))
    assert len(ck["queue"]) == 3 and len(ck["items"]) == 4 and len(ck["seen"]) == 7

    sess2 = FakeSession(routes)
    r2 = build(sess2, -1).start()
    assert not r2.paused
    assert sorted(i["u"] for i in r2.items) == sorted("http://a.test/%s" % x for x in ["", "0", "1", "2", "3", "4", "5"])
    assert [c[1] for c in sess2.calls] == ["http://a.test/3", "http://a.test/4", "http://a.test/5"]  # no refetch
    assert r2.stats["resume_count"] == 1
    assert not (tmp_path / "crawl" / "checkpoint.json").exists()


def test_max_items_and_max_requests():
    routes = {"http://a.test/": "x"}
    for i in range(10):
        routes["http://a.test/%d" % i] = "x"
    sess = FakeSession(routes)

    class S(make(sess, concurrent_requests=1, max_requests=4)):
        async def parse(self, response):
            if response.url.endswith("/"):
                for i in range(10):
                    yield Request("http://a.test/%d" % i)
            yield {"u": response.url}

    r = S().start()
    assert r.stats["request_count"] == 4 and not r.paused
    assert r.stats["finish_reason"] == "closespider_requestcount"


# ---------------------------------------------------------- streaming/export
def test_stream_and_incremental_jsonl(tmp_path):
    sess = FakeSession({"http://a.test/": "x", "http://a.test/1": "x"})

    class S(make(sess)):
        async def parse(self, response):
            if response.url.endswith("/"):
                yield Request("http://a.test/1")
            yield {"u": response.url}

    async def run():
        got = []
        sp = S()
        async for item in sp.stream(jsonl_path=str(tmp_path / "s.jsonl")):
            got.append(item)
        return got, sp

    got, sp = asyncio.run(run())
    assert len(got) == 2 and sp.result.items == [] and sp.result.stats["item_count"] == 2
    lines = (tmp_path / "s.jsonl").read_text().strip().splitlines()
    assert [json.loads(l)["u"] for l in lines] == [g["u"] for g in got]


def test_stream_early_break_pauses(tmp_path):
    routes = {"http://a.test/": "x"}
    for i in range(20):
        routes["http://a.test/%d" % i] = "x"
    sess = FakeSession(routes, latency=0.01)

    class S(make(sess, concurrent_requests=1, crawldir=str(tmp_path / "cd"))):
        async def parse(self, response):
            if response.url.endswith("/"):
                for i in range(20):
                    yield Request("http://a.test/%d" % i)
            yield {"u": response.url}

    async def run():
        sp = S()
        agen = sp.stream()
        async for item in agen:
            if item["u"] != "http://a.test/":
                break
        await agen.aclose()
        return sp

    sp = asyncio.run(run())
    assert sp.result.paused and (tmp_path / "cd" / "checkpoint.json").exists()


def test_exports(tmp_path):
    items = [{"a": 1, "b": "x,y", "c": [1, 2]}, {"a": 2, "d": {"k": "v"}, "e": None, "1 bad": True}]
    res = CrawlResult(items=items, stats={})
    res.to_json(tmp_path / "o.json")
    assert json.load(open(tmp_path / "o.json")) == items
    res.to_jsonl(tmp_path / "o.jsonl")
    assert [json.loads(l) for l in open(tmp_path / "o.jsonl")] == items
    res.to_csv(tmp_path / "o.csv")
    rows = list(csv.DictReader(open(tmp_path / "o.csv", encoding="utf-8")))
    assert rows[0]["b"] == "x,y" and json.loads(rows[0]["c"]) == [1, 2] and json.loads(rows[1]["d"]) == {"k": "v"}
    assert set(rows[0]) == {"a", "b", "c", "d", "e", "1 bad"}
    res.to_xml(tmp_path / "o.xml")
    root = ET.parse(tmp_path / "o.xml").getroot()
    first = root.findall("item")[0]
    assert first.find("a").text == "1" and [v.text for v in first.find("c")] == ["1", "2"]
    assert root.findall("item")[1].find("d/k").text == "v" and root.findall("item")[1].find("_1_bad").text == "true"


def test_jsonl_writer(tmp_path):
    p = tmp_path / "w.jsonl"
    with JSONLWriter(p) as w:
        w.write({"a": 1})
        assert p.read_text().count("\n") == 1  # flushed immediately
    with JSONLWriter(p, append=True) as w:
        w.write({"é": 2})
    assert [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] == [{"a": 1}, {"é": 2}]


def test_start_inside_running_loop():
    sess = FakeSession({"http://a.test/": "x"})

    async def run():
        return make(sess)().start()

    assert len(asyncio.run(run()).items) == 1
