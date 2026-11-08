"""Priority scheduler with de-duplication, per-domain limits and AutoThrottle."""
from __future__ import annotations

import heapq
import itertools
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from .request import Request


def domain_of(url: str) -> str:
    """Lower-cased ``host[:port]`` of a URL (the scheduling key)."""
    return urlsplit(url).netloc.lower()


class AutoThrottle:
    """Adaptive per-domain delay (same maths as Scrapy's AutoThrottle).

    After each response the target delay is ``latency / target_concurrency``;
    the new delay is the mean of the previous delay and the target, never below
    the target, clamped to ``[min_delay, max_delay]``.  Non-OK responses can
    never *decrease* the delay.
    """

    def __init__(self, start_delay: float = 0.5, target_concurrency: float = 1.0,
                 min_delay: float = 0.0, max_delay: float = 60.0) -> None:
        if target_concurrency <= 0:
            raise ValueError("target_concurrency must be > 0")
        self.start_delay = start_delay
        self.target_concurrency = target_concurrency
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.delays: Dict[str, float] = {}

    def delay_for(self, domain: str) -> float:
        return self.delays.get(domain, min(max(self.start_delay, self.min_delay), self.max_delay))

    def observe(self, domain: str, latency: float, ok: bool = True) -> float:
        prev = self.delay_for(domain)
        target = latency / self.target_concurrency
        new = max(target, (prev + target) / 2.0)
        new = min(max(self.min_delay, new), self.max_delay)
        if not ok and new <= prev:
            return prev
        self.delays[domain] = new
        return new


class _DomainState:
    __slots__ = ("active", "next_time")

    def __init__(self) -> None:
        self.active = 0
        self.next_time = 0.0


class Scheduler:
    """Priority queue (higher ``priority`` first, FIFO within a priority)."""

    def __init__(self, per_domain: int = 0) -> None:
        self.per_domain = per_domain
        self.seen: Set[str] = set()
        self._heap: List[Tuple[int, int, Request]] = []
        self._counter = itertools.count()
        self._domains: Dict[str, _DomainState] = {}

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, request: Request, force: bool = False) -> bool:
        """Queue a request; returns False when filtered as a duplicate."""
        if not (force or request.dont_filter):
            fp = request.fingerprint()
            if fp in self.seen:
                return False
            self.seen.add(fp)
        elif not force:
            self.seen.add(request.fingerprint())
        heapq.heappush(self._heap, (-request.priority, next(self._counter), request))
        return True

    def requests(self) -> List[Request]:
        """Queued requests in dispatch order."""
        return [e[2] for e in sorted(self._heap)]

    def _state(self, domain: str) -> _DomainState:
        st = self._domains.get(domain)
        if st is None:
            st = self._domains[domain] = _DomainState()
        return st

    def pop_ready(self, now: float) -> Tuple[Optional[Request], Optional[float]]:
        """Pop the best request that may start now.

        Returns ``(request, None)`` or ``(None, wait_seconds_or_None)``.
        """
        skipped: List[Tuple[int, int, Request]] = []
        wait: Optional[float] = None
        chosen: Optional[Request] = None
        while self._heap:
            entry = heapq.heappop(self._heap)
            req = entry[2]
            st = self._state(domain_of(req.url))
            w = 0.0
            nb = req.meta.get("_not_before", 0.0)
            if nb > now:
                w = nb - now
            blocked_by_conc = bool(self.per_domain and st.active >= self.per_domain)
            if st.next_time > now:
                w = max(w, st.next_time - now)
            if w > 0 or blocked_by_conc:
                skipped.append(entry)
                if w > 0 and not blocked_by_conc:
                    wait = w if wait is None else min(wait, w)
                continue
            chosen = req
            break
        for e in skipped:
            heapq.heappush(self._heap, e)
        return chosen, (None if chosen else wait)

    def started(self, request: Request, next_time: float) -> None:
        st = self._state(domain_of(request.url))
        st.active += 1
        st.next_time = next_time

    def finished(self, request: Request) -> None:
        st = self._state(domain_of(request.url))
        st.active = max(0, st.active - 1)

    def load(self, requests: List[Request], seen: List[str]) -> None:
        self.seen.update(seen)
        for r in requests:
            self.push(r, force=True)
