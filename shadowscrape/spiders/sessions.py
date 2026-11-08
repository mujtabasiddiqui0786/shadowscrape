"""Adapter layer: any fetcher Session (sync or async) behind one async ``fetch``."""
from __future__ import annotations

import asyncio
import functools
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from .request import Request


def _looks_like_browser(obj: Any) -> bool:
    cls = obj if inspect.isclass(obj) else type(obj)
    name = cls.__name__.lower()
    return "dynamic" in name or "stealth" in name or cls.__module__.endswith("browser")


class SessionAdapter:
    """Wraps a session object (or class/factory) exposing ``get/post/...`` or ``fetch``.

    * async sessions are awaited directly;
    * sync sessions run in worker threads.  Browser-style sessions (Dynamic/
      Stealthy, whose sync Playwright objects are thread-affine) are pinned to a
      single dedicated thread; override with ``threads=``.
    * ``supports_proxy=False`` stops the per-request ``proxy=`` kwarg being passed.
    """

    def __init__(self, session: Any, *, threads: Optional[int] = None, supports_proxy: bool = True) -> None:
        self._source = session
        self.session: Any = None
        self.supports_proxy = supports_proxy
        self._threads = threads
        self._executor: Optional[ThreadPoolExecutor] = None
        self._entered: Any = None

    def _instantiate(self) -> Any:
        src = self._source
        if inspect.isclass(src):
            return src()
        if not any(hasattr(src, a) for a in ("get", "fetch")) and callable(src):
            return src()  # zero-arg factory
        return src

    async def open(self) -> None:
        if self.session is not None:
            return
        threads = self._threads
        if threads is None and _looks_like_browser(self._source):
            threads = 1
        if threads:
            self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="ss-session")
        self.session = self._instantiate()
        sess = self.session
        if hasattr(sess, "__aenter__"):
            res = await sess.__aenter__()
            self._entered = ("a", res)
            if res is not None and res is not sess and (hasattr(res, "get") or hasattr(res, "fetch")):
                self.session = res
        elif hasattr(sess, "__enter__"):
            res = await self._run_sync(sess.__enter__)
            self._entered = ("s", res)
            if res is not None and res is not sess and (hasattr(res, "get") or hasattr(res, "fetch")):
                self.session = res
        if self._entered is not None:
            self._exit_obj = sess

    async def close(self) -> None:
        if self._entered is not None:
            kind = self._entered[0]
            self._entered = None
            try:
                if kind == "a":
                    await self._exit_obj.__aexit__(None, None, None)
                else:
                    await self._run_sync(self._exit_obj.__exit__, None, None, None)
            except Exception:
                pass
        elif self.session is not None and hasattr(self.session, "close"):
            try:
                r = self.session.close()
                if inspect.isawaitable(r):
                    await r
            except Exception:
                pass
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        self.session = None

    async def _run_sync(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    def _method(self, name: str) -> Any:
        sess = self.session
        fn = getattr(sess, name, None)
        if fn is None and name == "get":
            fn = getattr(sess, "fetch", None)
        if fn is None and name != "get":
            fn = getattr(sess, "request", None)
            if fn is not None:
                return functools.partial(fn, name.upper())
        if fn is None:
            raise AttributeError("session %r has no %r method" % (sess, name))
        return fn

    async def fetch(self, request: Request, *, proxy: Any = None, timeout: Optional[float] = None) -> Any:
        if self.session is None:
            await self.open()
        kwargs: Dict[str, Any] = {}
        if request.headers:
            kwargs["headers"] = dict(request.headers)
        if request.data is not None:
            kwargs["data"] = request.data
        if proxy and self.supports_proxy:
            kwargs["proxy"] = proxy
        if timeout:
            kwargs["timeout"] = timeout
        kwargs.update(request.meta.get("fetch_kwargs") or {})
        fn = self._method(request.method.lower())
        if asyncio.iscoroutinefunction(fn) or asyncio.iscoroutinefunction(getattr(fn, "func", None)):
            return await fn(request.url, **kwargs)
        res = await self._run_sync(fn, request.url, **kwargs)
        if inspect.isawaitable(res):
            res = await res
        return res


class SessionManager:
    """Registry of named sessions, passed to ``Spider.configure_sessions``."""

    def __init__(self) -> None:
        self._adapters: Dict[str, SessionAdapter] = {}
        self.default_sid: Optional[str] = None

    @property
    def sessions(self) -> Dict[str, SessionAdapter]:
        return self._adapters

    def add(self, sid: str, session: Any, *, default: bool = False, threads: Optional[int] = None,
            supports_proxy: bool = True) -> "SessionManager":
        """Register ``session`` (instance, class or zero-arg factory) under ``sid``."""
        self._adapters[sid] = SessionAdapter(session, threads=threads, supports_proxy=supports_proxy)
        if default or self.default_sid is None:
            self.default_sid = sid
        return self

    def get(self, sid: Optional[str]) -> SessionAdapter:
        sid = sid or self.default_sid
        if sid is None or sid not in self._adapters:
            raise KeyError("unknown session id %r (registered: %s)" % (sid, sorted(self._adapters)))
        return self._adapters[sid]

    async def open(self) -> None:
        for a in self._adapters.values():
            await a.open()

    async def close(self) -> None:
        for a in self._adapters.values():
            await a.close()

    async def fetch(self, request: Request, *, proxy: Any = None, timeout: Optional[float] = None) -> Any:
        """Fetch through the request's session and attach ``meta``/``request`` to the response."""
        response = await self.get(request.sid).fetch(request, proxy=proxy, timeout=timeout)
        meta = getattr(response, "meta", None)
        if isinstance(meta, dict):
            meta.update(request.meta)
        else:
            try:
                response.meta = dict(request.meta)
            except Exception:
                pass
        try:
            response.request = request
        except Exception:
            pass
        return response
