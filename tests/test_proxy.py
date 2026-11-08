from __future__ import annotations

import threading

import pytest

from shadowscrape.proxy import ProxyRotator, normalize_proxy

P = ["http://a:1", "http://b:2", "http://c:3"]


def test_cyclic():
    r = ProxyRotator(P)
    assert [r.next() for _ in range(6)] == P + P


def test_random_only_returns_members():
    r = ProxyRotator(P, strategy="random")
    assert {r.next() for _ in range(50)} <= set(P)


def test_least_used_balances():
    r = ProxyRotator(P, strategy="least_used")
    for _ in range(9):
        r.next()
    assert sorted(s["uses"] for s in r.stats()) == [3, 3, 3]


def test_failure_cooldown_and_recovery():
    r = ProxyRotator(P, cooldown=100)
    now = [0.0]
    r._clock = lambda: now[0]
    r.mark_failed("http://a:1")
    assert "http://a:1" not in [r.next() for _ in range(6)]
    now[0] = 101
    assert "http://a:1" in [r.next() for _ in range(3)]


def test_mark_ok_clears_cooldown_and_backoff_grows():
    r = ProxyRotator(P, cooldown=10, max_cooldown=25)
    now = [0.0]
    r._clock = lambda: now[0]
    r.mark_failed("http://a:1")
    r.mark_failed("http://a:1")  # 20s
    assert r._bench_until[0] == 20
    r.mark_failed("http://a:1")  # capped 25
    assert r._bench_until[0] == 25
    r.mark_ok("http://a:1")
    assert "http://a:1" in r.available


def test_all_benched_returns_soonest():
    r = ProxyRotator(P, cooldown=10)
    now = [0.0]
    r._clock = lambda: now[0]
    for i, p in enumerate(P):
        now[0] = i
        r.mark_failed(p)
    assert r.next() == "http://a:1"


def test_dict_proxies_and_normalize():
    d = {"server": "http://h:9", "username": "u", "password": "p@ss"}
    assert normalize_proxy(d) == "http://u:p%40ss@h:9"
    assert normalize_proxy("h:9") == "http://h:9"
    assert normalize_proxy({"https": "http://x:1"}) == "http://x:1"
    r = ProxyRotator([d])
    assert r.next() is d
    r.mark_failed(d)
    assert r.stats()[0]["cooling_down"]


def test_validation():
    with pytest.raises(ValueError):
        ProxyRotator([])
    with pytest.raises(ValueError):
        ProxyRotator(P, strategy="nope")


def test_thread_safety():
    r = ProxyRotator(P, strategy="least_used")
    def work():
        for _ in range(300):
            p = r.next()
            r.mark_ok(p)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(s["uses"] for s in r.stats()) == 2400
    assert max(s["uses"] for s in r.stats()) - min(s["uses"] for s in r.stats()) <= 1
