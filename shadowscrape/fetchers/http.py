"""HTTP fetchers on curl_cffi with browser TLS/JA3/HTTP2 impersonation.

* :class:`Fetcher` / :class:`AsyncFetcher` - stateless classmethods.
* :class:`FetcherSession` / :class:`AsyncFetcherSession` - persistent cookies
  and a sticky impersonation profile, usable as context managers.

Redirects are followed manually so the chain is recorded in
``Response.history`` and the Referer/Sec-Fetch-Site headers stay realistic
on every hop. Failed attempts (network errors and 429/5xx statuses) are
retried with exponential backoff plus jitter, optionally rotating proxies.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import urljoin, urlsplit

from curl_cffi.requests import AsyncSession, Session

from shadowscrape.proxy import ProxyRotator, normalize_proxy
from shadowscrape.response import Response

# (target, weight). Targets must exist in the installed curl_cffi.
IMPERSONATION_POOL: List[Tuple[str, int]] = [
    ("chrome136", 5),
    ("chrome133a", 4),
    ("chrome131", 4),
    ("chrome124", 3),
    ("chrome120", 2),
    ("edge101", 1),
    ("firefox135", 2),
    ("firefox133", 1),
    ("safari184", 2),
    ("safari180", 1),
]

DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SEARCH_REFERERS = [
    "https://www.google.com/",
    "https://www.google.com/",
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
]
ACCEPT_LANGUAGES = {
    "chrome": ["en-US,en;q=0.9", "en-US,en;q=0.9", "en-GB,en;q=0.9,en-US;q=0.8", "en-US,en;q=0.9,de;q=0.8"],
    "firefox": ["en-US,en;q=0.5", "en-GB,en;q=0.5"],
    "safari": ["en-US,en;q=0.9", "en-GB,en;q=0.9"],
}
SEC_FETCH_FAMILIES = ("chrome", "edge", "firefox", "tor")
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_MAX_RETRY_AFTER = 60.0


def impersonation_family(target: str) -> str:
    """Browser family (``chrome``/``firefox``/``safari``/``edge``/``tor``) of a curl_cffi target."""
    t = (target or "").lower()
    for fam in ("chrome", "edge", "firefox", "safari", "tor"):
        if t.startswith(fam):
            return fam
    return "chrome"


def pick_impersonation(spec: Union[None, str, Sequence[str]] = None) -> str:
    """Resolve an ``impersonate`` spec to one concrete target.

    ``None``/``"random"`` samples the weighted default pool; a list picks
    uniformly from it; any other string is returned unchanged.
    """
    if spec is None or spec == "random":
        targets, weights = zip(*IMPERSONATION_POOL)
        return random.choices(targets, weights=weights, k=1)[0]
    if isinstance(spec, str):
        return spec
    return random.choice(list(spec))


def _is_fixed(spec: Any) -> bool:
    return isinstance(spec, str) and spec != "random"


def _site(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _sec_fetch_site(referer: Optional[str], url: str) -> str:
    if not referer:
        return "none"
    a, b = urlsplit(referer), urlsplit(url)
    if (a.scheme, a.netloc) == (b.scheme, b.netloc):
        return "same-origin"
    if _site(a.hostname or "") == _site(b.hostname or ""):
        return "same-site"
    return "cross-site"


def backoff_delay(attempt: int, base: float, cap: float = 60.0) -> float:
    """Exponential backoff with jitter: ``base * 2**attempt`` scaled by 0.5-1.0 plus up to ``base`` of noise."""
    raw = min(base * (2 ** attempt), cap)
    return raw * random.uniform(0.5, 1.0) + random.uniform(0, base * 0.25)


def _retry_after(resp: Response) -> Optional[float]:
    for k, v in resp.headers.items():
        if k.lower() == "retry-after":
            try:
                return min(max(float(v), 0.0), _MAX_RETRY_AFTER)
            except ValueError:
                return None
    return None


class _Opts:
    """Resolved per-request options (session defaults overlaid by call args)."""

    def __init__(self, defaults: Dict[str, Any], call: Dict[str, Any]) -> None:
        merged = dict(defaults)
        for k, v in call.items():
            if v is not None:
                merged[k] = v
        h: Dict[str, str] = {}
        h.update(defaults.get("headers") or {})
        h.update(call.get("headers") or {})
        merged["headers"] = h
        self.o = merged

    def get(self, key: str, default: Any = None) -> Any:
        v = self.o.get(key)
        return default if v is None else v


def _parse_set_cookies(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        values = raw.get_list("set-cookie")
    except Exception:
        values = []
    for sc in values:
        first = sc.split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class _Engine:
    """Shared (sync/async) request preparation and retry bookkeeping."""

    def __init__(self, defaults: Dict[str, Any]) -> None:
        self.defaults = {k: v for k, v in defaults.items() if v is not None}
        self.sticky_target: Optional[str] = None
        self.last_url: Optional[str] = None
        self.persistent = False

    # -- impersonation ------------------------------------------------------
    def target(self, opts: _Opts, attempt: int) -> str:
        spec = opts.o.get("impersonate")
        if self.persistent:
            if self.sticky_target is None or (_is_fixed(spec) and spec != self.sticky_target):
                self.sticky_target = pick_impersonation(spec)
            return self.sticky_target
        if _is_fixed(spec):
            return spec
        return pick_impersonation(spec)

    # -- headers ------------------------------------------------------------
    def headers_for(self, opts: _Opts, method: str, url: str, target: str, referer: Optional[str], is_json: bool) -> Dict[str, str]:
        user = dict(opts.o.get("headers") or {})
        lower = {k.lower() for k in user}
        out: Dict[str, str] = dict(user)
        if not opts.get("stealthy_headers", True):
            if referer and "referer" not in lower:
                out["Referer"] = referer
            return out
        fam = impersonation_family(target)
        afam = "chrome" if fam in ("edge", "tor") else fam
        if "accept-language" not in lower:
            out["Accept-Language"] = random.choice(ACCEPT_LANGUAGES.get(afam, ACCEPT_LANGUAGES["chrome"]))
        if referer is None and "referer" not in lower and method == "GET" and self.last_url is None:
            referer = random.choice(SEARCH_REFERERS)
        if referer and "referer" not in lower:
            out["Referer"] = referer
        if fam in SEC_FETCH_FAMILIES:
            eff = referer if "referer" not in lower else user.get("Referer") or user.get("referer")
            if "sec-fetch-site" not in lower:
                out["Sec-Fetch-Site"] = _sec_fetch_site(eff, url)
            if method != "GET" or is_json:
                # Programmatic (XHR/fetch style) request rather than navigation.
                if "sec-fetch-mode" not in lower:
                    out["Sec-Fetch-Mode"] = "cors"
                if "sec-fetch-dest" not in lower:
                    out["Sec-Fetch-Dest"] = "empty"
        if is_json and "accept" not in lower:
            out["Accept"] = "application/json, text/plain, */*"
        if method != "GET" and "origin" not in lower and referer is None:
            p = urlsplit(url)
            out["Origin"] = "%s://%s" % (p.scheme, p.netloc)
        return out

    def referer_for(self, opts: _Opts, url: str, hop_from: Optional[str]) -> Optional[str]:
        if hop_from:
            return hop_from
        if self.last_url and urlsplit(self.last_url).hostname == urlsplit(url).hostname:
            return self.last_url
        return None

    # -- request building ---------------------------------------------------
    def build(self, opts: _Opts, method: str, url: str, target: str, proxy: Any, hop_from: Optional[str],
              params: Any, data: Any, json: Any) -> Dict[str, Any]:
        referer = self.referer_for(opts, url, hop_from)
        kw: Dict[str, Any] = {
            "headers": self.headers_for(opts, method, url, target, referer, json is not None),
            "impersonate": target,
            "allow_redirects": False,
            "timeout": opts.get("timeout", 30),
        }
        if params is not None:
            kw["params"] = params
        if data is not None:
            kw["data"] = data
        if json is not None:
            kw["json"] = json
        if opts.o.get("cookies"):
            kw["cookies"] = opts.o["cookies"]
        px = normalize_proxy(proxy)
        if px:
            kw["proxy"] = px
        for extra in ("verify", "auth", "http_version", "interface"):
            if opts.o.get(extra) is not None:
                kw[extra] = opts.o[extra]
        return kw

    def make_response(self, raw: Any, kw: Dict[str, Any], history: List[Response], started: float,
                      cookies: Dict[str, str], meta: Optional[Dict[str, Any]] = None) -> Response:
        hdrs: Dict[str, str] = {}
        for k, v in raw.headers.multi_items() if hasattr(raw.headers, "multi_items") else raw.headers.items():
            hdrs[k] = hdrs[k] + ", " + v if k in hdrs and k.lower() != "set-cookie" else v
        return Response(
            raw.content,
            url=str(raw.url),
            status=raw.status_code,
            reason=raw.reason or "",
            headers=hdrs,
            cookies=cookies,
            request_headers=dict(kw.get("headers") or {}),
            history=list(history),
            meta=meta or {},
            elapsed=time.monotonic() - started,
        )

    @staticmethod
    def redirect_target(resp: Response, url: str) -> Optional[str]:
        if resp.status not in _REDIRECT_CODES:
            return None
        for k, v in resp.headers.items():
            if k.lower() == "location" and v:
                return urljoin(url, v)
        return None

    @staticmethod
    def next_method(status: int, method: str) -> Tuple[str, bool]:
        """(new method, keep body?) for a redirect with ``status``."""
        if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
            return "GET", False
        return method, True

    @staticmethod
    def note_proxy(rotator: Optional[ProxyRotator], proxy: Any, failed: bool) -> None:
        if rotator is None or proxy is None:
            return
        (rotator.mark_failed if failed else rotator.mark_ok)(proxy)

    @staticmethod
    def retryable_exc(exc: BaseException) -> bool:
        return not isinstance(exc, (ValueError, TypeError, KeyboardInterrupt))


def _defaults_from(**kw: Any) -> Dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def _call_opts(**kw: Any) -> Dict[str, Any]:
    return {k: v for k, v in kw.items()}


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------
class FetcherSession:
    """Synchronous session: persistent cookies and a sticky browser profile.

    Constructor options become defaults for every request; each ``get/post/...``
    call may override them. ``impersonate`` may be a target name, a list, or
    ``"random"``/``None``: it is resolved once per session so TLS fingerprint,
    User-Agent and client hints stay consistent across requests.
    """

    def __init__(
        self,
        *,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Any = None,
        proxy_rotator: Optional[ProxyRotator] = None,
        timeout: float = 30,
        retries: int = 3,
        retry_delay: float = 1.0,
        impersonate: Union[None, str, Sequence[str]] = None,
        follow_redirects: bool = True,
        stealthy_headers: bool = True,
        retry_statuses: Optional[Sequence[int]] = None,
        max_redirects: int = 15,
        verify: Optional[bool] = None,
        **extra: Any,
    ) -> None:
        defaults = _defaults_from(
            headers=headers, proxy=proxy, proxy_rotator=proxy_rotator, timeout=timeout, retries=retries,
            retry_delay=retry_delay, impersonate=impersonate, follow_redirects=follow_redirects,
            stealthy_headers=stealthy_headers, retry_statuses=retry_statuses, max_redirects=max_redirects,
            verify=verify, **extra,
        )
        self._engine = _Engine(defaults)
        self._engine.persistent = True
        self._session: Optional[Session] = None
        self._initial_cookies = dict(cookies or {})

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "FetcherSession":
        self._ensure()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _ensure(self) -> Session:
        if self._session is None:
            self._session = Session()
            for k, v in self._initial_cookies.items():
                self._session.cookies.set(k, v)
        return self._session

    def close(self) -> None:
        """Close the underlying connection pool."""
        if self._session is not None:
            self._session.close()
            self._session = None

    @property
    def cookies(self) -> Dict[str, str]:
        """Current cookie jar as ``name -> value``."""
        s = self._ensure()
        return {c.name: c.value for c in s.cookies.jar}

    # -- verbs --------------------------------------------------------------
    def request(self, method: str, url: str, *, headers=None, cookies=None, proxy=None, proxy_rotator=None,
                timeout=None, retries=None, retry_delay=None, impersonate=None, follow_redirects=None,
                params=None, data=None, json=None, stealthy_headers=None, retry_statuses=None,
                max_redirects=None, **extra: Any) -> Response:
        """Perform a request with retries and redirect tracking."""
        eng = self._engine
        opts = _Opts(eng.defaults, _call_opts(
            headers=headers, cookies=cookies, proxy=proxy, proxy_rotator=proxy_rotator, timeout=timeout,
            retries=retries, retry_delay=retry_delay, impersonate=impersonate, follow_redirects=follow_redirects,
            stealthy_headers=stealthy_headers, retry_statuses=retry_statuses, max_redirects=max_redirects, **extra))
        session = self._ensure()
        return _run_sync(session, eng, opts, method.upper(), url, params, data, json)

    def get(self, url: str, **kw: Any) -> Response:
        """GET ``url``."""
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        """POST to ``url``."""
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any) -> Response:
        """PUT to ``url``."""
        return self.request("PUT", url, **kw)

    def delete(self, url: str, **kw: Any) -> Response:
        """DELETE ``url``."""
        return self.request("DELETE", url, **kw)

    def head(self, url: str, **kw: Any) -> Response:
        """HEAD ``url``."""
        return self.request("HEAD", url, **kw)


def _run_sync(session: Session, eng: _Engine, opts: _Opts, method: str, url: str,
              params: Any, data: Any, json: Any) -> Response:
    retries = int(opts.get("retries", 3))
    delay = float(opts.get("retry_delay", 1.0))
    statuses = frozenset(opts.get("retry_statuses", DEFAULT_RETRY_STATUSES))
    rotator: Optional[ProxyRotator] = opts.o.get("proxy_rotator")
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        target = eng.target(opts, attempt)
        proxy = rotator.next() if rotator is not None else opts.o.get("proxy")
        started = time.monotonic()
        try:
            resp = _follow_sync(session, eng, opts, method, url, target, proxy, params, data, json, started)
        except Exception as exc:  # network/TLS/timeouts
            last_exc = exc
            eng.note_proxy(rotator, proxy, True)
            if attempt >= retries or not eng.retryable_exc(exc):
                raise
            time.sleep(backoff_delay(attempt, delay))
            continue
        if resp.status in statuses:
            eng.note_proxy(rotator, proxy, True)
            if attempt < retries:
                ra = _retry_after(resp)
                time.sleep(ra if ra is not None else backoff_delay(attempt, delay))
                continue
            eng.last_url = resp.url
            return resp
        eng.note_proxy(rotator, proxy, False)
        eng.last_url = resp.url
        return resp
    raise last_exc  # pragma: no cover


def _follow_sync(session, eng, opts, method, url, target, proxy, params, data, json, started) -> Response:
    history: List[Response] = []
    cookies: Dict[str, str] = {}
    hop_from: Optional[str] = None
    cur = url
    max_redirects = int(opts.get("max_redirects", 15))
    follow = opts.get("follow_redirects", True)
    while True:
        kw = eng.build(opts, method, cur, target, proxy, hop_from, params, data, json)
        raw = session.request(method, cur, **kw)
        cookies.update(_parse_set_cookies(raw.headers))
        resp = eng.make_response(raw, kw, history, started, cookies)
        loc = eng.redirect_target(resp, cur) if follow else None
        if loc is None:
            return resp
        if len(history) >= max_redirects:
            raise RuntimeError("Too many redirects (%d) fetching %s" % (max_redirects, url))
        history.append(resp)
        method, keep = eng.next_method(resp.status, method)
        if not keep:
            data = json = None
        params = None
        hop_from, cur = cur, loc


class Fetcher:
    """Stateless HTTP client: each classmethod call uses a fresh connection.

    >>> page = Fetcher.get("https://example.com")           # doctest: +SKIP
    >>> page.css("h1::text").get()                          # doctest: +SKIP
    """

    @classmethod
    def request(cls, method: str, url: str, **kw: Any) -> Response:
        """Perform ``method`` on ``url``. See :class:`FetcherSession` for options."""
        session_opts = {k: kw.pop(k) for k in list(kw) if k in ("cookies",)}
        with FetcherSession() as s:
            return s.request(method, url, **session_opts, **kw)

    @classmethod
    def get(cls, url: str, **kw: Any) -> Response:
        """GET ``url`` with browser impersonation, retries and redirect tracking."""
        return cls.request("GET", url, **kw)

    @classmethod
    def post(cls, url: str, **kw: Any) -> Response:
        """POST (use ``data=`` for form bodies or ``json=`` for JSON)."""
        return cls.request("POST", url, **kw)

    @classmethod
    def put(cls, url: str, **kw: Any) -> Response:
        """PUT to ``url``."""
        return cls.request("PUT", url, **kw)

    @classmethod
    def delete(cls, url: str, **kw: Any) -> Response:
        """DELETE ``url``."""
        return cls.request("DELETE", url, **kw)


# --------------------------------------------------------------------------
# Async
# --------------------------------------------------------------------------
class AsyncFetcherSession:
    """Asynchronous counterpart of :class:`FetcherSession` (``async with``)."""

    def __init__(
        self,
        *,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Any = None,
        proxy_rotator: Optional[ProxyRotator] = None,
        timeout: float = 30,
        retries: int = 3,
        retry_delay: float = 1.0,
        impersonate: Union[None, str, Sequence[str]] = None,
        follow_redirects: bool = True,
        stealthy_headers: bool = True,
        retry_statuses: Optional[Sequence[int]] = None,
        max_redirects: int = 15,
        verify: Optional[bool] = None,
        **extra: Any,
    ) -> None:
        defaults = _defaults_from(
            headers=headers, proxy=proxy, proxy_rotator=proxy_rotator, timeout=timeout, retries=retries,
            retry_delay=retry_delay, impersonate=impersonate, follow_redirects=follow_redirects,
            stealthy_headers=stealthy_headers, retry_statuses=retry_statuses, max_redirects=max_redirects,
            verify=verify, **extra,
        )
        self._engine = _Engine(defaults)
        self._engine.persistent = True
        self._session: Optional[AsyncSession] = None
        self._initial_cookies = dict(cookies or {})

    async def __aenter__(self) -> "AsyncFetcherSession":
        self._ensure()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _ensure(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession()
            for k, v in self._initial_cookies.items():
                self._session.cookies.set(k, v)
        return self._session

    async def close(self) -> None:
        """Close the underlying connection pool."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def cookies(self) -> Dict[str, str]:
        """Current cookie jar as ``name -> value``."""
        return {c.name: c.value for c in self._ensure().cookies.jar}

    async def request(self, method: str, url: str, *, headers=None, cookies=None, proxy=None, proxy_rotator=None,
                      timeout=None, retries=None, retry_delay=None, impersonate=None, follow_redirects=None,
                      params=None, data=None, json=None, stealthy_headers=None, retry_statuses=None,
                      max_redirects=None, **extra: Any) -> Response:
        """Perform a request with retries and redirect tracking."""
        eng = self._engine
        opts = _Opts(eng.defaults, _call_opts(
            headers=headers, cookies=cookies, proxy=proxy, proxy_rotator=proxy_rotator, timeout=timeout,
            retries=retries, retry_delay=retry_delay, impersonate=impersonate, follow_redirects=follow_redirects,
            stealthy_headers=stealthy_headers, retry_statuses=retry_statuses, max_redirects=max_redirects, **extra))
        return await _run_async(self._ensure(), eng, opts, method.upper(), url, params, data, json)

    async def get(self, url: str, **kw: Any) -> Response:
        """GET ``url``."""
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> Response:
        """POST to ``url``."""
        return await self.request("POST", url, **kw)

    async def put(self, url: str, **kw: Any) -> Response:
        """PUT to ``url``."""
        return await self.request("PUT", url, **kw)

    async def delete(self, url: str, **kw: Any) -> Response:
        """DELETE ``url``."""
        return await self.request("DELETE", url, **kw)

    async def head(self, url: str, **kw: Any) -> Response:
        """HEAD ``url``."""
        return await self.request("HEAD", url, **kw)


async def _run_async(session: AsyncSession, eng: _Engine, opts: _Opts, method: str, url: str,
                     params: Any, data: Any, json: Any) -> Response:
    retries = int(opts.get("retries", 3))
    delay = float(opts.get("retry_delay", 1.0))
    statuses = frozenset(opts.get("retry_statuses", DEFAULT_RETRY_STATUSES))
    rotator: Optional[ProxyRotator] = opts.o.get("proxy_rotator")
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        target = eng.target(opts, attempt)
        proxy = rotator.next() if rotator is not None else opts.o.get("proxy")
        started = time.monotonic()
        try:
            resp = await _follow_async(session, eng, opts, method, url, target, proxy, params, data, json, started)
        except Exception as exc:
            last_exc = exc
            eng.note_proxy(rotator, proxy, True)
            if attempt >= retries or not eng.retryable_exc(exc):
                raise
            await asyncio.sleep(backoff_delay(attempt, delay))
            continue
        if resp.status in statuses:
            eng.note_proxy(rotator, proxy, True)
            if attempt < retries:
                ra = _retry_after(resp)
                await asyncio.sleep(ra if ra is not None else backoff_delay(attempt, delay))
                continue
            eng.last_url = resp.url
            return resp
        eng.note_proxy(rotator, proxy, False)
        eng.last_url = resp.url
        return resp
    raise last_exc  # pragma: no cover


async def _follow_async(session, eng, opts, method, url, target, proxy, params, data, json, started) -> Response:
    history: List[Response] = []
    cookies: Dict[str, str] = {}
    hop_from: Optional[str] = None
    cur = url
    max_redirects = int(opts.get("max_redirects", 15))
    follow = opts.get("follow_redirects", True)
    while True:
        kw = eng.build(opts, method, cur, target, proxy, hop_from, params, data, json)
        raw = await session.request(method, cur, **kw)
        cookies.update(_parse_set_cookies(raw.headers))
        resp = eng.make_response(raw, kw, history, started, cookies)
        loc = eng.redirect_target(resp, cur) if follow else None
        if loc is None:
            return resp
        if len(history) >= max_redirects:
            raise RuntimeError("Too many redirects (%d) fetching %s" % (max_redirects, url))
        history.append(resp)
        method, keep = eng.next_method(resp.status, method)
        if not keep:
            data = json = None
        params = None
        hop_from, cur = cur, loc


class AsyncFetcher:
    """Stateless async HTTP client (``await AsyncFetcher.get(url)``)."""

    @classmethod
    async def request(cls, method: str, url: str, **kw: Any) -> Response:
        """Perform ``method`` on ``url``. See :class:`AsyncFetcherSession` for options."""
        cookies = kw.pop("cookies", None)
        async with AsyncFetcherSession() as s:
            return await s.request(method, url, cookies=cookies, **kw)

    @classmethod
    async def get(cls, url: str, **kw: Any) -> Response:
        """GET ``url``."""
        return await cls.request("GET", url, **kw)

    @classmethod
    async def post(cls, url: str, **kw: Any) -> Response:
        """POST to ``url``."""
        return await cls.request("POST", url, **kw)

    @classmethod
    async def put(cls, url: str, **kw: Any) -> Response:
        """PUT to ``url``."""
        return await cls.request("PUT", url, **kw)

    @classmethod
    async def delete(cls, url: str, **kw: Any) -> Response:
        """DELETE ``url``."""
        return await cls.request("DELETE", url, **kw)
