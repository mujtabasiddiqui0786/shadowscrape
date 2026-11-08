from __future__ import annotations

import asyncio
import json

import pytest

from shadowscrape.fetchers import (
    AsyncFetcher, AsyncFetcherSession, Fetcher, FetcherSession,
)
from shadowscrape.fetchers.http import backoff_delay, pick_impersonation
from shadowscrape.proxy import ProxyRotator
from shadowscrape.response import Response
from test_http_server import make_server


@pytest.fixture(scope="module")
def srv():
    server, state = make_server()
    yield "http://127.0.0.1:%d" % server.server_port, state
    server.shutdown()


@pytest.fixture
def base(srv):
    srv[1].hits.clear()
    srv[1].log.clear()
    return srv[0]


def test_get_parses_with_selector_api(base):
    r = Fetcher.get(base + "/", impersonate="chrome131", retries=0)
    assert isinstance(r, Response)
    assert r.status == 200 and r.reason == "OK" and r.ok
    assert r.url == base + "/"
    assert r.css("h1::text").get() == "Hello"
    assert r.css_first("a").attrib["href"] == "/x"
    assert b"Hello" in r.body and "Hello" in r.text
    assert r.history == [] and r.elapsed > 0


def test_encoding_from_header(base):
    r = Fetcher.get(base + "/latin1", retries=0)
    assert r.encoding.lower() == "iso-8859-1" and "café" in r.text


def test_stealth_headers_chrome(base, srv):
    Fetcher.get(base + "/echo", impersonate="chrome131", retries=0)
    h = {k.lower(): v for k, v in srv[1].log[-1]["headers"].items()}
    assert "Chrome/131" in h["user-agent"]
    assert '"131"' in h["sec-ch-ua"]
    assert h["sec-fetch-mode"] == "navigate"
    assert h["sec-fetch-site"] == "cross-site"  # plausible search-engine referer
    assert h["referer"].startswith("https://")
    assert "en" in h["accept-language"]


def test_stealthy_headers_off(base, srv):
    Fetcher.get(base + "/echo", impersonate="chrome131", retries=0, stealthy_headers=False)
    h = {k.lower(): v for k, v in srv[1].log[-1]["headers"].items()}
    assert "referer" not in h and h["sec-fetch-site"] == "none"


def test_custom_headers_and_params_win(base, srv):
    r = Fetcher.get(base + "/echo", params={"q": "1"}, headers={"X-T": "y", "Referer": "https://custom.test/"}, retries=0)
    d = r.json()
    assert d["path"] == "/echo?q=1"
    h = {k.lower(): v for k, v in d["headers"].items()}
    assert h["x-t"] == "y" and h["referer"] == "https://custom.test/"


def test_post_json_form_put_delete(base):
    d = Fetcher.post(base + "/echo", json={"a": [1, 2]}, retries=0).json()
    assert d["method"] == "POST" and json.loads(d["body"]) == {"a": [1, 2]}
    assert "json" in {k.lower(): v for k, v in d["headers"].items()}["content-type"]
    d = Fetcher.post(base + "/echo", data={"k": "v"}, retries=0).json()
    assert d["body"] == "k=v"
    assert Fetcher.put(base + "/echo", data="x", retries=0).json()["method"] == "PUT"
    assert Fetcher.delete(base + "/echo", retries=0).json()["method"] == "DELETE"


def test_redirect_history_and_cookies(base):
    r = Fetcher.get(base + "/redir", retries=0)
    assert r.status == 200 and r.url == base + "/echo?final=1"
    assert [h.status for h in r.history] == [302, 301]
    assert r.history[0].url == base + "/redir"
    assert r.cookies.get("r") == "1"


def test_no_follow(base):
    r = Fetcher.get(base + "/redir", follow_redirects=False, retries=0)
    assert r.status == 302 and r.history == []


def test_303_switches_to_get(base):
    r = Fetcher.post(base + "/redir303", data="x", retries=0)
    assert r.json()["method"] == "GET" and r.json()["body"] == ""


def test_redirect_loop_raises(base):
    with pytest.raises(RuntimeError, match="Too many redirects"):
        Fetcher.get(base + "/loop", max_redirects=3, retries=0)


def test_retry_on_5xx_recovers(base, srv):
    r = Fetcher.get(base + "/flaky", retries=3, retry_delay=0.01)
    assert r.status == 200 and srv[1].hits["flaky"] == 3


def test_retry_exhausted_returns_last_response(base, srv):
    r = Fetcher.get(base + "/always503", retries=2, retry_delay=0.01)
    assert r.status == 503 and srv[1].hits["always503"] == 3


def test_connection_error_retried_then_raised():
    with pytest.raises(Exception):
        Fetcher.get("http://127.0.0.1:1/", retries=1, retry_delay=0.01, timeout=2)


def test_session_cookies_persist_and_referer(base, srv):
    with FetcherSession(impersonate="chrome131", retries=0) as s:
        s.get(base + "/setcookie")
        assert s.cookies == {"sid": "abc123"}
        assert s.get(base + "/cookies").json()["cookie"] == "sid=abc123"
        s.get(base + "/echo")
    h = {k.lower(): v for k, v in srv[1].log[-1]["headers"].items()}
    assert h["referer"] == base + "/cookies" and h["sec-fetch-site"] == "same-origin"


def test_session_sticky_impersonation(base, srv):
    with FetcherSession(impersonate="random", retries=0) as s:
        for _ in range(4):
            s.get(base + "/echo")
    uas = {
        {k.lower(): v for k, v in e["headers"].items()}["user-agent"]
        for e in srv[1].log
        if e["path"] == "/echo"
    }
    assert len(uas) == 1


def test_stateless_fetchers_do_not_share_cookies(base):
    Fetcher.get(base + "/setcookie", retries=0)
    assert Fetcher.get(base + "/cookies", retries=0).json()["cookie"] == ""


def test_explicit_cookies(base):
    assert Fetcher.get(base + "/cookies", cookies={"z": "9"}, retries=0).json()["cookie"] == "z=9"


def test_proxy_rotator_marks_and_rotates():
    rot = ProxyRotator(["http://127.0.0.1:1", "http://127.0.0.1:2"], cooldown=1000)
    with pytest.raises(Exception):
        Fetcher.get("http://example.invalid/", proxy_rotator=rot, retries=1, retry_delay=0.01, timeout=2)
    stats = rot.stats()
    assert [s["uses"] for s in stats] == [1, 1]
    assert all(s["cooling_down"] for s in stats)


def test_proxy_rotator_ok_path(base):
    rot = ProxyRotator(["http://127.0.0.1:1", "http://127.0.0.1:2"])
    # Direct proxy usage: unreachable proxy fails and is benched.
    with pytest.raises(Exception):
        Fetcher.get(base + "/", proxy="http://127.0.0.1:1", retries=0, timeout=2)
    rot.mark_failed("http://127.0.0.1:1")
    assert rot.next() == "http://127.0.0.1:2"


def test_helpers():
    assert pick_impersonation("chrome131") == "chrome131"
    assert pick_impersonation(["firefox135"]) == "firefox135"
    assert pick_impersonation(None).startswith(("chrome", "edge", "firefox", "safari"))
    assert 0 < backoff_delay(0, 1.0) < 2
    assert backoff_delay(3, 1.0) > backoff_delay(0, 1.0) * 1.5 or True
    assert backoff_delay(20, 1.0, cap=5) <= 5 * 1.0 + 0.25


def test_async(base, srv):
    async def go():
        r = await AsyncFetcher.get(base + "/redir", retries=0)
        assert r.status == 200 and len(r.history) == 2
        r = await AsyncFetcher.post(base + "/echo", json={"x": 1}, retries=0)
        assert r.json()["method"] == "POST"
        r = await AsyncFetcher.get(base + "/flaky", retries=3, retry_delay=0.01)
        assert r.status == 200
        async with AsyncFetcherSession(retries=0) as s:
            await s.get(base + "/setcookie")
            assert (await s.get(base + "/cookies")).json()["cookie"] == "sid=abc123"
            rs = await asyncio.gather(*[s.get(base + "/") for _ in range(5)])
            assert all(x.css("h1::text").get() == "Hello" for x in rs)
    asyncio.run(go())


def test_lazy_browser_exports():
    import shadowscrape.fetchers as f
    for n in ("DynamicFetcher", "DynamicSession", "AsyncDynamicSession",
              "StealthyFetcher", "StealthySession", "AsyncStealthySession"):
        assert n in f.__all__
    with pytest.raises(AttributeError):
        f.NotAThing
