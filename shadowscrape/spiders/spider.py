"""The Spider base class and crawl engine."""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import signal
import threading
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit

from . import checkpoint as _ckpt
from ._util import aiter_results, host_matches
from .request import Request
from .result import CrawlResult, JSONLWriter
from .robots import RobotsCache
from .scheduler import AutoThrottle, Scheduler, domain_of
from .sessions import SessionManager
from .stats import StatsCollector

log = logging.getLogger("shadowscrape.spider")
_END = object()


class Spider:
    """Scrapy-like spider.  Subclass, set ``start_urls`` and implement ``parse``.

    ``parse(response)`` (sync/async function or generator) yields ``dict`` items
    or :class:`Request` objects.  Run with :meth:`start` (blocking, returns a
    :class:`CrawlResult`), ``await spider.crawl()`` or ``async for item in
    spider.stream()``.  Ctrl+C pauses gracefully; with ``crawldir`` set the queue,
    seen-set and items are checkpointed and a re-run resumes.
    """

    name: str = "spider"
    start_urls: Sequence[str] = ()
    allowed_domains: Optional[Sequence[str]] = None

    # concurrency / politeness
    concurrent_requests: int = 16
    concurrent_requests_per_domain: int = 8
    download_delay: float = 0.0
    randomize_delay: bool = False
    autothrottle: bool = False
    autothrottle_start_delay: float = 0.5
    autothrottle_target_concurrency: float = 1.0
    autothrottle_min_delay: float = 0.0
    autothrottle_max_delay: float = 60.0
    robots_txt_obey: bool = False
    robots_user_agent: str = "*"

    # retries / blocking
    max_retries: int = 3
    retry_backoff: float = 0.5
    retry_backoff_max: float = 30.0
    blocked_status_codes: Sequence[int] = (403, 429, 503)
    retry_status_codes: Sequence[int] = (408, 500, 502, 504, 522, 524)
    handle_status_codes: Sequence[int] = ()  # >=400 codes still passed to callbacks
    proxy_rotator: Any = None
    request_timeout: Optional[float] = None

    # limits / persistence
    max_requests: Optional[int] = None
    max_items: Optional[int] = None
    crawldir: Optional[str] = None
    keep_items: bool = True

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.start_urls = list(self.start_urls)
        self.stats = StatsCollector()
        self.result: Optional[CrawlResult] = None
        self._stop = False
        self._stop_reason: Optional[str] = None
        self._sigint_count = 0
        self._running = False

    # ------------------------------------------------------------------ hooks
    def configure_sessions(self, manager: SessionManager) -> None:
        """Register named sessions: ``manager.add("fast", AsyncFetcherSession())``.

        The default implementation registers a default ``AsyncFetcherSession``.
        """
        from shadowscrape.fetchers import AsyncFetcherSession  # lazy: owned by another module

        manager.add("default", AsyncFetcherSession(), default=True)

    async def start_requests(self) -> AsyncIterator[Request]:
        """Initial requests; defaults to one per ``start_urls`` entry."""
        for url in self.start_urls:
            yield Request(url, dont_filter=True)

    async def parse(self, response: Any) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError("Spider.parse must be implemented")
        yield  # noqa

    def is_blocked(self, response: Any) -> bool:
        """Blocked-response detection hook (default: status in ``blocked_status_codes``)."""
        return int(getattr(response, "status", 200) or 200) in tuple(self.blocked_status_codes)

    def process_item(self, item: Any) -> Any:
        """Item hook; return ``None`` to drop the item."""
        return item

    def on_failure(self, request: Request, response: Any = None, exc: Optional[BaseException] = None) -> None:
        """Called when a request permanently fails (retries exhausted)."""

    def on_callback_error(self, request: Request, exc: BaseException) -> None:
        """Called when a callback raises."""

    def follow(self, response: Any, url: str, callback: Any = None, **kwargs: Any) -> Request:
        """Build a Request for ``url`` resolved against ``response.url``."""
        return Request(urljoin(getattr(response, "url", ""), url), callback=callback, **kwargs)

    # ---------------------------------------------------------------- control
    def pause(self, reason: str = "paused") -> None:
        """Request a graceful stop after in-flight requests finish (checkpoints if ``crawldir``)."""
        self._stop = True
        self._stop_reason = self._stop_reason or reason
        wake = getattr(self, "_wake", None)
        if wake is not None:
            wake.set()

    # ------------------------------------------------------------- public run
    def start(self, *, jsonl_path: Optional[str] = None) -> CrawlResult:
        """Run the crawl to completion (blocking) and return a :class:`CrawlResult`."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.crawl(jsonl_path=jsonl_path))
        box: Dict[str, Any] = {}

        def runner() -> None:
            try:
                box["r"] = asyncio.run(self.crawl(jsonl_path=jsonl_path))
            except BaseException as e:  # noqa
                box["e"] = e

        t = threading.Thread(target=runner, name="shadowscrape-spider")
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return box["r"]

    async def crawl(self, *, jsonl_path: Optional[str] = None) -> CrawlResult:
        """Async variant of :meth:`start`."""
        return await self._execute(jsonl_path=jsonl_path, store_items=self.keep_items)

    async def stream(self, *, jsonl_path: Optional[str] = None) -> AsyncIterator[Any]:
        """Yield items as they are scraped (items are not accumulated in memory)."""
        q: "asyncio.Queue[Any]" = asyncio.Queue()
        task = asyncio.ensure_future(self._execute(on_item=q.put_nowait, store_items=False, jsonl_path=jsonl_path))
        task.add_done_callback(lambda _t: q.put_nowait(_END))
        try:
            while True:
                item = await q.get()
                if item is _END:
                    break
                yield item
        finally:
            if not task.done():
                self.pause("stream_closed")
            await asyncio.gather(task, return_exceptions=True)
        if task.exception() is not None:  # surface engine errors
            raise task.exception()  # type: ignore[misc]

    # ----------------------------------------------------------------- engine
    async def _execute(self, *, on_item: Optional[Callable[[Any], None]] = None, store_items: bool = True,
                       jsonl_path: Optional[str] = None) -> CrawlResult:
        if self._running:
            raise RuntimeError("spider is already running")
        self._running = True
        self._stop, self._stop_reason, self._sigint_count = False, None, 0
        self.stats = StatsCollector()
        self._on_item, self._store_items = on_item, store_items
        self._items: List[Any] = []
        self._wake = asyncio.Event()
        self._inflight = 0
        self._sched = Scheduler(per_domain=self.concurrent_requests_per_domain)
        self._throttle = AutoThrottle(self.autothrottle_start_delay, self.autothrottle_target_concurrency,
                                      self.autothrottle_min_delay, self.autothrottle_max_delay)
        self._robots_delay: Dict[str, float] = {}
        self._worker_tasks: List["asyncio.Future[Any]"] = []
        self._manager = SessionManager()
        self._sink: Optional[JSONLWriter] = None
        t_start = time.monotonic()
        prev_elapsed = 0.0
        loop = asyncio.get_running_loop()
        sig_installed = False
        try:
            self.configure_sessions(self._manager)
            if not self._manager.sessions:
                raise RuntimeError("configure_sessions registered no sessions")
            await self._manager.open()
            self._robots = RobotsCache(self._fetch_plain, self.robots_user_agent)

            resumed = self._restore()
            if resumed:
                prev_elapsed = float(self.stats.get("elapsed_seconds", 0.0))
            if jsonl_path:
                self._sink = JSONLWriter(jsonl_path, append=resumed)
            if not resumed:
                self.stats.set("start_time", time.time())
                async for req in aiter_results(self.start_requests()):
                    if isinstance(req, str):
                        req = Request(req, dont_filter=True)
                    self._enqueue(req, start=True)
            try:
                loop.add_signal_handler(signal.SIGINT, self._on_sigint)
                sig_installed = True
            except (NotImplementedError, RuntimeError, ValueError, AttributeError):
                pass

            n = max(1, int(self.concurrent_requests))
            self._worker_tasks = [asyncio.ensure_future(self._worker()) for _ in range(n)]
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

            paused = bool(self._stop) and self._stop_reason in ("paused", "stream_closed", "sigint") and len(self._sched) > 0
            self.stats.set("finish_reason", self._stop_reason if self._stop else "finished")
            elapsed = prev_elapsed + (time.monotonic() - t_start)
            self.stats.set("elapsed_seconds", round(elapsed, 6))
            self.stats.set("end_time", time.time())
            rc = self.stats.get("response_count", 0)
            self.stats.set("avg_latency", (self.stats.get("latency_total", 0.0) / rc) if rc else 0.0)
            self.stats.set("requests_per_second", (self.stats.get("request_count", 0) / elapsed) if elapsed > 0 else 0.0)
            self.stats.set("paused", paused)
            if self.crawldir:
                if paused:
                    self._save_checkpoint()
                else:
                    _ckpt.remove(self.crawldir)
            self.result = CrawlResult(items=list(self._items), stats=self.stats.to_dict(), paused=paused)
            return self.result
        finally:
            if sig_installed:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except Exception:
                    pass
            if self._sink:
                self._sink.close()
            await self._manager.close()
            self._running = False

    def _on_sigint(self) -> None:
        self._sigint_count += 1
        if self._sigint_count == 1:
            log.warning("Ctrl+C: pausing after in-flight requests (press again to abort now)")
            self.pause("sigint")
        else:
            for t in self._worker_tasks:
                t.cancel()

    # -- checkpoint ---------------------------------------------------------
    def _save_checkpoint(self) -> None:
        state = {
            "queue": [r.to_dict() for r in self._sched.requests()],
            "seen": sorted(self._sched.seen),
            "items": self._items if self._store_items else [],
            "stats": self.stats.to_dict(),
        }
        _ckpt.save(self.crawldir, state)  # type: ignore[arg-type]

    def _restore(self) -> bool:
        if not self.crawldir:
            return False
        state = _ckpt.load(self.crawldir)
        if not state:
            return False
        self._sched.load([Request.from_dict(d) for d in state.get("queue", [])], state.get("seen", []))
        self._items = list(state.get("items", []))
        self.stats = StatsCollector(state.get("stats", {}))
        self.stats.inc("resume_count")
        log.info("Resumed from checkpoint: %d queued, %d items", len(self._sched), len(self._items))
        return True

    # -- scheduling -----------------------------------------------------------
    def _offsite(self, url: str) -> bool:
        if not self.allowed_domains:
            return False
        return not host_matches(urlsplit(url).hostname or "", list(self.allowed_domains))

    def _enqueue(self, req: Request, *, parent: Optional[Request] = None, start: bool = False) -> bool:
        if urlsplit(req.url).scheme.lower() not in ("http", "https"):
            self.stats.inc("invalid_url_count")
            return False
        if parent is not None:
            req.meta.setdefault("depth", int(parent.meta.get("depth", 0)) + 1)
        else:
            req.meta.setdefault("depth", 0)
        if not start and self._offsite(req.url):
            self.stats.inc("offsite_filtered")
            return False
        if not self._sched.push(req):
            self.stats.inc("dedup_filtered")
            return False
        self.stats.inc("scheduled_count")
        self._wake.set()
        return True

    def _delay_for(self, domain: str) -> float:
        d = float(self.download_delay)
        if self.autothrottle:
            d = max(d, self._throttle.delay_for(domain))
        d = max(d, self._robots_delay.get(domain, 0.0))
        if self.randomize_delay and d > 0:
            d *= random.uniform(0.5, 1.5)
        return d

    async def _sleep(self, wait: Optional[float]) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=wait if wait else 0.5)
        except asyncio.TimeoutError:
            pass

    async def _worker(self) -> None:
        while not self._stop:
            now = time.monotonic()
            req, wait = self._sched.pop_ready(now)
            if req is None:
                if len(self._sched) == 0 and self._inflight == 0:
                    self._wake.set()
                    return
                await self._sleep(wait)
                continue
            if self.max_requests is not None and self.stats.get("request_count", 0) >= self.max_requests:
                self._sched.push(req, force=True)
                self.pause("closespider_requestcount")
                self._wake.set()
                return
            if self.max_items is not None and self.stats.get("item_count", 0) >= self.max_items:
                self._sched.push(req, force=True)
                self.pause("closespider_itemcount")
                self._wake.set()
                return
            dom = domain_of(req.url)
            self._inflight += 1
            self._sched.started(req, time.monotonic() + self._delay_for(dom))
            try:
                await self._process(req)
            except asyncio.CancelledError:
                self._sched.push(req, force=True)
                raise
            except Exception as exc:  # engine-level bug guard
                log.exception("unhandled error processing %s", req)
                self.stats.inc("engine_errors")
                self.stats.set("last_engine_error", repr(exc))
            finally:
                self._sched.finished(req)
                self._inflight -= 1
                self._wake.set()

    # -- downloading ------------------------------------------------------------
    async def _fetch_plain(self, url: str) -> Any:
        return await self._manager.fetch(Request(url), timeout=self.request_timeout)

    def _next_proxy(self) -> Any:
        return self.proxy_rotator.next() if self.proxy_rotator is not None else None

    def _proxy_failed(self, proxy: Any) -> None:
        if proxy is not None and self.proxy_rotator is not None:
            try:
                self.proxy_rotator.mark_failed(proxy)
            except Exception:
                log.debug("proxy mark_failed failed", exc_info=True)

    def _proxy_ok(self, proxy: Any) -> None:
        if proxy is not None and self.proxy_rotator is not None:
            try:
                self.proxy_rotator.mark_ok(proxy)
            except Exception:
                log.debug("proxy mark_ok failed", exc_info=True)

    @staticmethod
    def _header(response: Any, name: str) -> Optional[str]:
        headers = getattr(response, "headers", None) or {}
        for k, v in dict(headers).items():
            if str(k).lower() == name:
                return str(v)
        return None

    def _retry(self, req: Request, *, reason: str, response: Any = None, exc: Optional[BaseException] = None) -> None:
        n = int(req.meta.get("retry_times", 0))
        if n < self.max_retries:
            backoff = min(self.retry_backoff * (2 ** n), self.retry_backoff_max)
            ra = self._header(response, "retry-after") if response is not None else None
            if ra:
                try:
                    backoff = min(max(backoff, float(ra)), self.retry_backoff_max)
                except ValueError:
                    pass
            new = req.replace(dont_filter=True)
            new.meta["retry_times"] = n + 1
            new.meta["_not_before"] = time.monotonic() + backoff
            self.stats.inc("retry_count")
            self._sched.push(new, force=True)
            log.debug("retry %d/%d %s (%s)", n + 1, self.max_retries, req.url, reason)
        else:
            self.stats.inc("failed_count")
            self.stats.set("last_failure", "%s: %s" % (req.url, reason))
            try:
                self.on_failure(req, response, exc)
            except Exception:
                log.exception("on_failure raised")

    async def _process(self, req: Request) -> None:
        dom = domain_of(req.url)
        if self.robots_txt_obey:
            if not await self._robots.allowed(req.url):
                self.stats.inc("robots_blocked")
                return
            cd = await self._robots.crawl_delay(req.url)
            if cd:
                self._robots_delay[dom] = cd
        proxy: Any = None
        t0 = time.perf_counter()
        self.stats.inc("request_count")
        try:
            proxy = self._next_proxy()
            response = await self._manager.fetch(req, proxy=proxy, timeout=self.request_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats.inc("error_count")
            self._proxy_failed(proxy)
            self._retry(req, reason="%s: %s" % (type(exc).__name__, exc), exc=exc)
            return
        latency = time.perf_counter() - t0
        self.stats.inc("response_count")
        self.stats.inc("latency_total", latency)
        self.stats.max("latency_max", latency)
        status = int(getattr(response, "status", 200) or 200)
        self.stats.inc("status_%d" % status)

        blocked = self.is_blocked(response)
        if inspect.isawaitable(blocked):
            blocked = await blocked
        if blocked:
            self.stats.inc("blocked_count")
            self._proxy_failed(proxy)
            if self.autothrottle:
                self._throttle.observe(dom, latency, ok=False)
            self._retry(req, reason="blocked (status %d)" % status, response=response)
            return
        if status in tuple(self.retry_status_codes):
            self._proxy_failed(proxy)
            if self.autothrottle:
                self._throttle.observe(dom, latency, ok=False)
            self._retry(req, reason="status %d" % status, response=response)
            return
        if status >= 400 and status not in tuple(self.handle_status_codes):
            self.stats.inc("http_error_count")
            if self.autothrottle:
                self._throttle.observe(dom, latency, ok=False)
            return
        self._proxy_ok(proxy)
        if self.autothrottle:
            self._throttle.observe(dom, latency, ok=True)
        self.stats.set("autothrottle_delay_" + dom, self._throttle.delay_for(dom)) if self.autothrottle else None
        await self._run_callback(req, response)

    def _resolve_callback(self, req: Request) -> Callable[..., Any]:
        cb = req.callback
        if cb is None:
            return self.parse
        if isinstance(cb, str):
            return getattr(self, cb)
        return cb

    async def _run_callback(self, req: Request, response: Any) -> None:
        try:
            cb = self._resolve_callback(req)
            async for out in aiter_results(cb(response, **req.cb_kwargs)):
                if isinstance(out, Request):
                    self._enqueue(out, parent=req)
                elif out is not None:
                    await self._emit(out)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats.inc("callback_errors")
            log.exception("callback error for %s", req.url)
            try:
                self.on_callback_error(req, exc)
            except Exception:
                log.exception("on_callback_error raised")

    async def _emit(self, item: Any) -> None:
        res = self.process_item(item)
        if inspect.isawaitable(res):
            res = await res
        if res is None:
            self.stats.inc("item_dropped")
            return
        self.stats.inc("item_count")
        if self._store_items:
            self._items.append(res)
        if self._sink is not None:
            self._sink.write(res)
        if self._on_item is not None:
            self._on_item(res)
