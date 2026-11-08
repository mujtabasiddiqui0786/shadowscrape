"""Browser fetchers.

* :class:`DynamicFetcher` / :class:`DynamicSession` / :class:`AsyncDynamicSession`
  -- plain Playwright (real ``playwright`` package) with convenience options.
* :class:`StealthyFetcher` / :class:`StealthySession` / :class:`AsyncStealthySession`
  -- hardened Chromium (patchright if installed), consistent fingerprint
  profile, JS evasions, UA-CH/worker propagation, WebRTC block, humanized input
  and Cloudflare challenge interaction.  See :mod:`shadowscrape.stealth`.

Conventions
-----------
* ``timeout`` and ``wait`` are in **milliseconds** (Playwright convention).  For
  convenience a ``timeout`` of 300 or less is read as *seconds* (nobody wants a
  sub-300 ms navigation timeout), so values shared with the HTTP fetchers work.
* Sessions keep ONE browser context alive across ``fetch`` calls (cookies,
  localStorage, cf_clearance persist); each fetch uses a fresh page.
* A sync session must be used from the thread that started it (Playwright rule).
* Changing a launch-level option (``headless``, ``proxy``, ``fingerprint_profile``,
  ``block_webrtc``) in ``fetch`` restarts the session's browser context.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple, Union

_UNSET: Any = object()

#: Resource types blocked by ``disable_resources=True`` (page text/XHR still load).
BLOCKED_RESOURCE_TYPES = frozenset({"font", "image", "media", "beacon", "object", "imageset", "texttrack",
                                    "websocket", "csp_report", "stylesheet"})

_LAUNCH_KEYS = ("headless", "proxy", "block_webrtc", "fingerprint_profile")
_INSTALL_HINT = ("Chromium is not installed for Playwright. Run: python -m playwright install chromium "
                 "(or `shadowscrape install`).")


def _ms(timeout: Optional[float], default: float = 30000) -> float:
    if timeout is None:
        return default
    return float(timeout) * 1000 if 0 < timeout <= 300 else float(timeout)


def _compile(pattern: Union[None, str, Pattern[str]]) -> Optional[Pattern[str]]:
    if pattern is None:
        return None
    return pattern if hasattr(pattern, "search") else re.compile(pattern)


def _wrap_launch_error(exc: Exception) -> Exception:
    if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
        return RuntimeError(_INSTALL_HINT)
    return exc


def _make_response(**kw: Any) -> Any:
    from shadowscrape.response import Response  # lazy: response.py is owned by another module

    return Response.from_parts(**kw)


def _headers_to_dict(h: Any) -> Dict[str, str]:
    return {str(k): str(v) for k, v in dict(h or {}).items()}


def _xhr_entry(resp: Any, body: bytes) -> Dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    return {"url": resp.url, "method": resp.request.method, "status": resp.status,
            "headers": _headers_to_dict(resp.headers), "body": body, "text": text}


def _cookie_dict(cookies: List[Dict[str, Any]]) -> Dict[str, str]:
    return {c["name"]: c["value"] for c in cookies}


def _normalize_cookies(cookies: Any, url: str = "") -> List[Dict[str, Any]]:
    if not cookies:
        return []
    if isinstance(cookies, dict):
        return [{"name": k, "value": str(v), "url": url} for k, v in cookies.items()] if url else []
    return list(cookies)


class _Config:
    """Session-level options shared by every browser session class."""

    stealth = False

    def __init__(self, *, headless: Any = True, proxy: Any = None, timeout: Optional[float] = 30000,
                 wait_selector_state: str = "attached", network_idle: bool = False, disable_resources: bool = False,
                 extra_headers: Optional[Dict[str, str]] = None, cookies: Any = None, locale: Optional[str] = None,
                 timezone: Optional[str] = None, user_agent: Optional[str] = None, user_data_dir: Optional[str] = None,
                 channel: Optional[str] = None, executable_path: Optional[str] = None, extra_args: Optional[List[str]] = None,
                 viewport: Optional[Dict[str, int]] = None,
                 # stealth-only
                 solve_cloudflare: bool = True, humanize: bool = True, block_webrtc: bool = True,
                 fingerprint_profile: Any = None, geoip: bool = False, backend: str = "auto",
                 chrome_runtime: bool = False, noise: bool = True, max_pages: int = 5) -> None:
        self.headless = headless
        self.proxy = proxy
        self.timeout = timeout
        self.wait_selector_state = wait_selector_state
        self.network_idle = network_idle
        self.disable_resources = disable_resources
        self.extra_headers = dict(extra_headers or {})
        self.cookies = cookies
        self.locale = locale
        self.timezone = timezone
        self.user_agent = user_agent
        self.user_data_dir = user_data_dir
        self.channel = channel
        self.executable_path = executable_path
        self.extra_args = list(extra_args or [])
        self.viewport = viewport
        self.solve_cloudflare = solve_cloudflare
        self.humanize = humanize
        self.block_webrtc = block_webrtc
        self.fingerprint_profile = fingerprint_profile
        self.geoip = geoip
        self.backend = backend
        self.chrome_runtime = chrome_runtime
        self.noise = noise
        self.max_pages = max_pages
        self.profile: Any = None          # final Profile once started (stealth only)
        self.backend_used: Optional[str] = None
        self._pw: Any = None
        self._browser: Any = None
        self._ctx: Any = None
        self._tmp: Optional[str] = None
        self._key: Optional[str] = None

    # -- helpers -------------------------------------------------------------
    def _launch_key(self) -> str:
        prof = self.fingerprint_profile
        if hasattr(prof, "seed"):
            prof = ("profile", prof.os, prof.seed, prof.chrome_version)
        return json.dumps([self.headless, self.proxy if not hasattr(self.proxy, "next") else "rotator",
                           self.block_webrtc, prof], default=str, sort_keys=True)

    def _apply_launch_overrides(self, kw: Dict[str, Any]) -> bool:
        """Pop launch-level overrides from fetch kwargs; return True if the browser must restart."""
        changed = False
        for k in _LAUNCH_KEYS:
            v = kw.pop(k, _UNSET)
            if v is not _UNSET and v != getattr(self, k):
                setattr(self, k, v)
                changed = True
        return changed

    def _stealth_config(self) -> Any:
        from shadowscrape.stealth.launch import StealthConfig

        return StealthConfig(profile=self.fingerprint_profile, headless=self.headless, proxy=self.proxy,
                             block_webrtc=self.block_webrtc, geoip=self.geoip, user_data_dir=self.user_data_dir,
                             extra_args=self.extra_args, channel=self.channel, executable_path=self.executable_path,
                             backend=self.backend, chrome_runtime=self.chrome_runtime, noise=self.noise, locale=self.locale,
                             timezone=self.timezone)

    def _plain_context_options(self) -> Dict[str, Any]:
        o: Dict[str, Any] = {"viewport": self.viewport or {"width": 1280, "height": 720}}
        if self.locale:
            o["locale"] = self.locale
        if self.timezone:
            o["timezone_id"] = self.timezone
        if self.user_agent:
            o["user_agent"] = self.user_agent
        if self.extra_headers:
            o["extra_http_headers"] = self.extra_headers
        return o

    def _plain_launch_options(self) -> Dict[str, Any]:
        from shadowscrape.stealth.launch import normalize_proxy

        o: Dict[str, Any] = {"headless": bool(self.headless), "args": list(self.extra_args)}
        px = normalize_proxy(self.proxy)
        if px:
            o["proxy"] = px
        if self.channel:
            o["channel"] = self.channel
        if self.executable_path:
            o["executable_path"] = self.executable_path
        return o

    def _route_handler(self) -> Any:
        return None

    def _cleanup_tmp(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


# ======================================================================== sync
class _SyncSession(_Config):
    """Persistent synchronous browser session (context manager)."""

    def __enter__(self) -> "_SyncSession":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> "_SyncSession":
        """Launch the browser context (idempotent)."""
        if self._ctx is not None and self._key == self._launch_key():
            return self
        self.close()
        try:
            if self.stealth:
                from shadowscrape.stealth.launch import start_stealth_context_sync

                self._pw, self._ctx, self.profile, self.backend_used, self._tmp = start_stealth_context_sync(
                    self._stealth_config())
            else:
                from playwright.sync_api import sync_playwright

                self.backend_used = "playwright"
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(**self._plain_launch_options())
                self._ctx = self._browser.new_context(**self._plain_context_options())
            if self.cookies:
                self._ctx.add_cookies(_normalize_cookies(self.cookies))
            if self.extra_headers and self.stealth:
                self._ctx.set_extra_http_headers(self.extra_headers)
        except Exception as e:
            self.close()
            raise _wrap_launch_error(e)
        self._key = self._launch_key()
        return self

    def close(self) -> None:
        """Close the context/browser and stop Playwright."""
        for obj in (self._ctx, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._browser = self._pw = None
        self._key = None
        self._cleanup_tmp()

    # -- fetching ---------------------------------------------------------------
    def fetch(self, url: str, *, wait_selector: Optional[str] = None, wait_selector_state: Optional[str] = None,
              wait: float = 0, network_idle: Optional[bool] = None, page_action: Optional[Callable[[Any], Any]] = None,
              capture_xhr: Union[None, str, Pattern[str]] = None, disable_resources: Optional[bool] = None,
              timeout: Optional[float] = None, solve_cloudflare: Optional[bool] = None,
              humanize: Optional[bool] = None, extra_headers: Optional[Dict[str, str]] = None, **launch: Any) -> Any:
        """Load ``url`` and return a :class:`~shadowscrape.response.Response` of the rendered DOM.

        Order: goto -> (cloudflare solve) -> humanize -> ``page_action(page)`` -> network_idle ->
        ``wait_selector`` -> ``wait`` ms.  ``capture_xhr`` is a regex matched against the URL of every
        XHR/fetch response; matches land in ``response.captured_xhr``.
        """
        launch_changed = self._apply_launch_overrides(launch)
        if launch:
            raise TypeError("unexpected fetch() arguments: %s" % ", ".join(sorted(launch)))
        if launch_changed or self._ctx is None:
            self.start()
        assert self._ctx is not None
        tmo = _ms(timeout if timeout is not None else self.timeout)
        wss = wait_selector_state or self.wait_selector_state
        idle = self.network_idle if network_idle is None else network_idle
        disable = self.disable_resources if disable_resources is None else disable_resources
        cf = self.solve_cloudflare if solve_cloudflare is None else solve_cloudflare
        hum = self.humanize if humanize is None else humanize
        xhr_re = _compile(capture_xhr)

        import time

        t0 = time.time()
        page = self._ctx.new_page()
        captured: List[Dict[str, Any]] = []
        docs: List[Any] = []
        pending = [0]
        try:
            if self.stealth:
                self._ctx._ss_prepare(page)
            if extra_headers:
                page.set_extra_http_headers(extra_headers)
            page.set_default_timeout(tmo)
            if disable:
                page.route("**/*", lambda route: route.abort()
                           if route.request.resource_type in BLOCKED_RESOURCE_TYPES else route.continue_())

            def on_response(resp: Any) -> None:
                try:
                    req = resp.request
                    if req.resource_type == "document" and req.frame == page.main_frame:
                        docs.append(resp)
                    if xhr_re and req.resource_type in ("xhr", "fetch") and xhr_re.search(resp.url):
                        pending[0] += 1
                        try:
                            body = resp.body()
                        except Exception:
                            body = b""
                        finally:
                            pending[0] -= 1
                        captured.append(_xhr_entry(resp, body))
                except Exception:
                    pass

            page.on("response", on_response)
            main = page.goto(url, wait_until="load", timeout=tmo)
            if self.stealth and cf:
                from shadowscrape.stealth.turnstile import solve_sync

                res = solve_sync(page, timeout_ms=int(min(tmo, 60000)))
                self.last_challenge = res
            if self.stealth and hum:
                from shadowscrape.stealth.humanize import Humanizer

                Humanizer(page).idle()
            if page_action is not None:
                out = page_action(page)
                if out is not None and hasattr(out, "content") and hasattr(out, "goto"):
                    page = out
            if idle:
                try:
                    page.wait_for_load_state("networkidle", timeout=tmo)
                except Exception:
                    pass
            if wait_selector:
                page.wait_for_selector(wait_selector, state=wss, timeout=tmo)
            if wait:
                page.wait_for_timeout(wait)
            for _ in range(100):  # let in-flight XHR body reads finish
                if pending[0] <= 0:
                    break
                page.wait_for_timeout(20)
            final = docs[-1] if docs else main
            html = page.content()
            ctx_cookies = self._ctx.cookies()
            history = _build_history_sync(main)
            return _make_response(
                content=html, url=page.url, status=final.status if final else 200,
                reason=final.status_text if final else "", headers=_safe_headers_sync(final),
                cookies=_cookie_dict(ctx_cookies), encoding="utf-8",
                request_headers=_safe_req_headers_sync(main), history=history, captured_xhr=captured,
                elapsed=time.time() - t0)
        finally:
            try:
                page.close()
            except Exception:
                pass


def _safe_headers_sync(resp: Any) -> Dict[str, str]:
    try:
        return _headers_to_dict(resp.all_headers()) if resp else {}
    except Exception:
        return _headers_to_dict(getattr(resp, "headers", {}))


def _safe_req_headers_sync(resp: Any) -> Dict[str, str]:
    try:
        return _headers_to_dict(resp.request.all_headers()) if resp else {}
    except Exception:
        return {}


def _build_history_sync(main: Any) -> List[Any]:
    out: List[Any] = []
    try:
        req = main.request.redirected_from if main else None
        while req is not None:
            r = req.response()
            out.append(_make_response(content="", url=req.url, status=r.status if r else 302,
                                      headers=_headers_to_dict(r.headers) if r else {}))
            req = req.redirected_from
    except Exception:
        pass
    out.reverse()
    return out


# ======================================================================= async
class _AsyncSession(_Config):
    """Persistent asyncio browser session (``async with``)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._lock: Optional[asyncio.Lock] = None
        self._sem: Optional[asyncio.Semaphore] = None

    async def __aenter__(self) -> "_AsyncSession":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> "_AsyncSession":
        """Launch the browser context (idempotent, safe under concurrency)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
            self._sem = asyncio.Semaphore(self.max_pages)
        async with self._lock:
            if self._ctx is not None and self._key == self._launch_key():
                return self
            await self._close_unlocked()
            try:
                if self.stealth:
                    from shadowscrape.stealth.launch import start_stealth_context_async

                    self._pw, self._ctx, self.profile, self.backend_used, self._tmp = await start_stealth_context_async(
                        self._stealth_config())
                else:
                    from playwright.async_api import async_playwright

                    self.backend_used = "playwright"
                    self._pw = await async_playwright().start()
                    self._browser = await self._pw.chromium.launch(**self._plain_launch_options())
                    self._ctx = await self._browser.new_context(**self._plain_context_options())
                if self.cookies:
                    await self._ctx.add_cookies(_normalize_cookies(self.cookies))
                if self.extra_headers and self.stealth:
                    await self._ctx.set_extra_http_headers(self.extra_headers)
            except Exception as e:
                await self._close_unlocked()
                raise _wrap_launch_error(e)
            self._key = self._launch_key()
            return self

    async def close(self) -> None:
        """Close the context/browser and stop Playwright."""
        if self._lock is None:
            return
        async with self._lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._ctx = self._browser = self._pw = None
        self._key = None
        self._cleanup_tmp()

    async def fetch(self, url: str, *, wait_selector: Optional[str] = None, wait_selector_state: Optional[str] = None,
                    wait: float = 0, network_idle: Optional[bool] = None, page_action: Optional[Callable[[Any], Any]] = None,
                    capture_xhr: Union[None, str, Pattern[str]] = None, disable_resources: Optional[bool] = None,
                    timeout: Optional[float] = None, solve_cloudflare: Optional[bool] = None,
                    humanize: Optional[bool] = None, extra_headers: Optional[Dict[str, str]] = None, **launch: Any) -> Any:
        """Async twin of the sync ``fetch``; ``page_action`` may be a coroutine function."""
        launch_changed = self._apply_launch_overrides(launch)
        if launch:
            raise TypeError("unexpected fetch() arguments: %s" % ", ".join(sorted(launch)))
        if launch_changed or self._ctx is None:
            await self.start()
        assert self._ctx is not None and self._sem is not None
        tmo = _ms(timeout if timeout is not None else self.timeout)
        wss = wait_selector_state or self.wait_selector_state
        idle = self.network_idle if network_idle is None else network_idle
        disable = self.disable_resources if disable_resources is None else disable_resources
        cf = self.solve_cloudflare if solve_cloudflare is None else solve_cloudflare
        hum = self.humanize if humanize is None else humanize
        xhr_re = _compile(capture_xhr)

        import time

        async with self._sem:
            t0 = time.time()
            page = await self._ctx.new_page()
            captured: List[Dict[str, Any]] = []
            docs: List[Any] = []
            tasks: List[Any] = []
            try:
                if self.stealth:
                    await self._ctx._ss_prepare(page)
                if extra_headers:
                    await page.set_extra_http_headers(extra_headers)
                page.set_default_timeout(tmo)
                if disable:
                    async def route_h(route: Any) -> None:
                        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
                            await route.abort()
                        else:
                            await route.continue_()

                    await page.route("**/*", route_h)

                async def record(resp: Any) -> None:
                    try:
                        body = await resp.body()
                    except Exception:
                        body = b""
                    captured.append(_xhr_entry(resp, body))

                def on_response(resp: Any) -> None:
                    try:
                        req = resp.request
                        if req.resource_type == "document" and req.frame == page.main_frame:
                            docs.append(resp)
                        if xhr_re and req.resource_type in ("xhr", "fetch") and xhr_re.search(resp.url):
                            tasks.append(asyncio.ensure_future(record(resp)))
                    except Exception:
                        pass

                page.on("response", on_response)
                main = await page.goto(url, wait_until="load", timeout=tmo)
                if self.stealth and cf:
                    from shadowscrape.stealth.turnstile import solve_async

                    self.last_challenge = await solve_async(page, timeout_ms=int(min(tmo, 60000)))
                if self.stealth and hum:
                    from shadowscrape.stealth.humanize import AsyncHumanizer

                    await AsyncHumanizer(page).idle()
                if page_action is not None:
                    out = page_action(page)
                    if asyncio.iscoroutine(out) or isinstance(out, asyncio.Future):
                        out = await out
                    if out is not None and hasattr(out, "content") and hasattr(out, "goto"):
                        page = out
                if idle:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=tmo)
                    except Exception:
                        pass
                if wait_selector:
                    await page.wait_for_selector(wait_selector, state=wss, timeout=tmo)
                if wait:
                    await page.wait_for_timeout(wait)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                final = docs[-1] if docs else main
                html = await page.content()
                ctx_cookies = await self._ctx.cookies()
                history = await _build_history_async(main)
                try:
                    hdrs = _headers_to_dict(await final.all_headers()) if final else {}
                except Exception:
                    hdrs = {}
                try:
                    rh = _headers_to_dict(await main.request.all_headers()) if main else {}
                except Exception:
                    rh = {}
                return _make_response(
                    content=html, url=page.url, status=final.status if final else 200,
                    reason=final.status_text if final else "", headers=hdrs, cookies=_cookie_dict(ctx_cookies),
                    encoding="utf-8", request_headers=rh, history=history, captured_xhr=captured,
                    elapsed=time.time() - t0)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass


async def _build_history_async(main: Any) -> List[Any]:
    out: List[Any] = []
    try:
        req = main.request.redirected_from if main else None
        while req is not None:
            r = await req.response()
            out.append(_make_response(content="", url=req.url, status=r.status if r else 302,
                                      headers=_headers_to_dict(r.headers) if r else {}))
            req = req.redirected_from
    except Exception:
        pass
    out.reverse()
    return out


# ================================================================ public classes
class DynamicSession(_SyncSession):
    """Sync session on plain Playwright Chromium (no stealth patches)."""

    stealth = False


class AsyncDynamicSession(_AsyncSession):
    """Async session on plain Playwright Chromium (no stealth patches)."""

    stealth = False


class StealthySession(_SyncSession):
    """Sync stealth session: consistent fingerprint profile + evasions + humanized input + CF solving.

    Extra attributes after start: ``profile`` (the final :class:`~shadowscrape.stealth.profiles.Profile`),
    ``backend_used`` (``"patchright"``/``"playwright"``), ``last_challenge`` (result of the last CF solve).
    """

    stealth = True
    last_challenge: Optional[Dict[str, Any]] = None


class AsyncStealthySession(_AsyncSession):
    """Async stealth session (see :class:`StealthySession`)."""

    stealth = True
    last_challenge: Optional[Dict[str, Any]] = None


_FETCH_KEYS = {"wait_selector", "wait_selector_state", "wait", "network_idle", "page_action", "capture_xhr",
               "disable_resources", "timeout", "solve_cloudflare", "humanize", "extra_headers"}


def _split(kw: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    fetch_kw = {k: v for k, v in kw.items() if k in _FETCH_KEYS}
    init_kw = {k: v for k, v in kw.items() if k not in _FETCH_KEYS}
    if "timeout" in fetch_kw:
        init_kw["timeout"] = fetch_kw["timeout"]
    return init_kw, fetch_kw


class DynamicFetcher:
    """One-shot plain-Playwright fetcher: ``DynamicFetcher.fetch(url, wait_selector=..., ...)``."""

    session_class = DynamicSession
    async_session_class = AsyncDynamicSession

    @classmethod
    def fetch(cls, url: str, **kwargs: Any) -> Any:
        """Open a session, fetch ``url``, close.  Accepts all session and fetch options."""
        init_kw, fetch_kw = _split(kwargs)
        with cls.session_class(**init_kw) as s:
            return s.fetch(url, **fetch_kw)

    @classmethod
    async def async_fetch(cls, url: str, **kwargs: Any) -> Any:
        """Async one-shot fetch."""
        init_kw, fetch_kw = _split(kwargs)
        async with cls.async_session_class(**init_kw) as s:
            return await s.fetch(url, **fetch_kw)


class StealthyFetcher(DynamicFetcher):
    """One-shot stealth fetcher: ``StealthyFetcher.fetch(url, solve_cloudflare=True, humanize=True, ...)``."""

    session_class = StealthySession
    async_session_class = AsyncStealthySession


__all__ = ["DynamicFetcher", "DynamicSession", "AsyncDynamicSession", "StealthyFetcher", "StealthySession",
           "AsyncStealthySession", "BLOCKED_RESOURCE_TYPES"]
