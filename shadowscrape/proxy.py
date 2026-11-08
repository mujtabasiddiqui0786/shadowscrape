"""Proxy rotation with failure cooldown.

:class:`ProxyRotator` hands out proxies using one of three strategies
(``cyclic``, ``random``, ``least_used``), temporarily benches proxies that
failed, and is safe to share between threads (and asyncio tasks, since the
critical sections never block).
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote, urlsplit, urlunsplit

ProxyType = Union[str, Dict[str, Any]]

STRATEGIES = ("cyclic", "random", "least_used")


def normalize_proxy(proxy: Optional[ProxyType]) -> Optional[str]:
    """Convert any supported proxy spelling to a single proxy URL string.

    Accepted forms: ``"http://host:port"``, ``"host:port"`` (assumed http),
    ``{"http": url, "https": url}`` / ``{"all": url}``, and Playwright-style
    ``{"server": url, "username": u, "password": p}``.
    """
    if proxy is None:
        return None
    if isinstance(proxy, str):
        return proxy if "://" in proxy else "http://" + proxy
    if isinstance(proxy, dict):
        if "server" in proxy:
            url = normalize_proxy(proxy["server"]) or ""
            user = proxy.get("username")
            if user:
                parts = urlsplit(url)
                if "@" not in parts.netloc:
                    cred = quote(str(user), safe="")
                    if proxy.get("password") is not None:
                        cred += ":" + quote(str(proxy["password"]), safe="")
                    parts = parts._replace(netloc=cred + "@" + parts.netloc)
                    url = urlunsplit(parts)
            return url
        for key in ("https", "http", "all"):
            if proxy.get(key):
                return normalize_proxy(proxy[key])
    raise ValueError("unsupported proxy specification: %r" % (proxy,))


def _key(proxy: ProxyType) -> str:
    return normalize_proxy(proxy) or ""


class ProxyRotator:
    """Rotate through a pool of proxies.

    Args:
        proxies: proxy URLs or dicts (see :func:`normalize_proxy`).
        strategy: ``"cyclic"`` (round robin), ``"random"`` or ``"least_used"``.
        cooldown: seconds a failed proxy is benched. Consecutive failures
            multiply the cooldown (capped at ``max_cooldown``).
        max_cooldown: upper bound for the multiplied cooldown.

    ``next()`` returns the original item given in ``proxies``. If every proxy
    is cooling down, the one that recovers soonest is returned rather than
    raising, so callers always get something usable.
    """

    def __init__(
        self,
        proxies: List[ProxyType],
        strategy: str = "cyclic",
        cooldown: float = 60.0,
        max_cooldown: float = 600.0,
    ) -> None:
        if not proxies:
            raise ValueError("ProxyRotator needs at least one proxy")
        if strategy not in STRATEGIES:
            raise ValueError("strategy must be one of %s" % (STRATEGIES,))
        self.strategy = strategy
        self.cooldown = float(cooldown)
        self.max_cooldown = float(max_cooldown)
        self._proxies: List[ProxyType] = []
        self._keys: List[str] = []
        for p in proxies:
            k = _key(p)
            if k not in self._keys:  # de-duplicate
                self._keys.append(k)
                self._proxies.append(p)
        n = len(self._proxies)
        self._uses = [0] * n
        self._failures = [0] * n  # consecutive failures
        self._total_failures = [0] * n
        self._bench_until = [0.0] * n
        self._cursor = 0
        self._lock = threading.Lock()
        self._clock = time.monotonic

    def __len__(self) -> int:
        return len(self._proxies)

    def _index(self, proxy: ProxyType) -> Optional[int]:
        try:
            return self._keys.index(_key(proxy))
        except (ValueError, TypeError):
            return None

    def _available_indices(self, now: float) -> List[int]:
        return [i for i in range(len(self._proxies)) if self._bench_until[i] <= now]

    @property
    def available(self) -> List[ProxyType]:
        """Proxies that are not cooling down right now."""
        with self._lock:
            now = self._clock()
            return [self._proxies[i] for i in self._available_indices(now)]

    def next(self) -> ProxyType:
        """Pick the next proxy according to the strategy."""
        with self._lock:
            now = self._clock()
            avail = self._available_indices(now)
            if not avail:
                idx = min(range(len(self._proxies)), key=lambda i: self._bench_until[i])
            elif self.strategy == "random":
                idx = random.choice(avail)
            elif self.strategy == "least_used":
                least = min(self._uses[i] for i in avail)
                idx = next(i for i in avail if self._uses[i] == least)
            else:  # cyclic
                n = len(self._proxies)
                idx = avail[0]
                for step in range(n):
                    cand = (self._cursor + step) % n
                    if cand in avail:
                        idx = cand
                        break
                self._cursor = (idx + 1) % n
            self._uses[idx] += 1
            return self._proxies[idx]

    def mark_failed(self, proxy: ProxyType) -> None:
        """Record a failure and bench the proxy for the cooldown period."""
        with self._lock:
            i = self._index(proxy)
            if i is None:
                return
            self._failures[i] += 1
            self._total_failures[i] += 1
            delay = min(self.cooldown * (2 ** (self._failures[i] - 1)), self.max_cooldown)
            self._bench_until[i] = self._clock() + delay

    def mark_ok(self, proxy: ProxyType) -> None:
        """Record a success: clears the failure streak and any cooldown."""
        with self._lock:
            i = self._index(proxy)
            if i is None:
                return
            self._failures[i] = 0
            self._bench_until[i] = 0.0

    def stats(self) -> List[Dict[str, Any]]:
        """Per-proxy usage statistics."""
        with self._lock:
            now = self._clock()
            return [
                {
                    "proxy": self._keys[i],
                    "uses": self._uses[i],
                    "consecutive_failures": self._failures[i],
                    "total_failures": self._total_failures[i],
                    "cooling_down": self._bench_until[i] > now,
                }
                for i in range(len(self._proxies))
            ]
