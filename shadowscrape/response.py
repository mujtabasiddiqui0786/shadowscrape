"""HTTP/browser response object that is also a parser ``Selector``."""
from __future__ import annotations

import json as _json
import re
from typing import Any, Dict, List, Optional

from shadowscrape.parser import Selector

_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)


def _sniff_encoding(headers: Dict[str, str], body: bytes, default: str = "utf-8") -> str:
    """Pick a text encoding from Content-Type, a BOM, or an HTML ``<meta>``."""
    ctype = ""
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            ctype = v
            break
    m = _CHARSET_RE.search(ctype.encode("latin-1", "ignore"))
    if not m:
        if body.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        m = _CHARSET_RE.search(body[:4096])
    if m:
        name = m.group(1).decode("ascii", "ignore")
        try:
            "".encode(name)
            return name
        except LookupError:
            pass
    return default


class Response(Selector):
    """The result of a fetch. A :class:`~shadowscrape.parser.Selector` over the
    document root, plus HTTP metadata.

    Attributes:
        status: HTTP status code.
        reason: reason phrase (``"OK"``).
        headers: response headers (lower-case-insensitive lookup is *not*
            applied; keys are as received, repeated headers joined by ``", "``).
        cookies: cookies set by the response chain (``name -> value``).
        url: final URL after redirects.
        body: raw bytes.
        text: the full decoded body (note: this deliberately differs from
            ``Selector.text``, which is an element's own text; use
            ``get_all_text()`` for the visible text of the page).
        encoding: encoding used to decode ``text``.
        request_headers: headers explicitly set on the request by shadowscrape
            or the caller (the TLS layer adds its own browser defaults).
        history: earlier responses in the redirect chain (oldest first,
            bodies not retained).
        meta: free-form dict (spiders put request meta here).
        captured_xhr: browser-captured XHR/fetch calls, each
            ``{url, method, status, headers, body}``.
        elapsed: seconds taken by the final request (including redirects).
    """

    def __init__(
        self,
        content: Any = "",
        *,
        url: str = "",
        status: int = 200,
        reason: str = "",
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        encoding: Optional[str] = None,
        request_headers: Optional[Dict[str, str]] = None,
        history: Optional[List[Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        captured_xhr: Optional[List[Dict[str, Any]]] = None,
        elapsed: float = 0.0,
        **selector_kwargs: Any,
    ) -> None:
        if body is None:
            body = content if isinstance(content, (bytes, bytearray)) else str(content or "").encode(encoding or "utf-8")
        body = bytes(body)
        hdrs = dict(headers or {})
        enc = encoding or _sniff_encoding(hdrs, body)
        # Private state first: Selector.__init__ may touch url/encoding.
        self._r_body = body
        self._r_encoding = enc
        self._r_url = url
        self._r_text = body.decode(enc, "replace")
        self.status = int(status)
        self.reason = reason
        self.headers = hdrs
        self.cookies = dict(cookies or {})
        self.request_headers = dict(request_headers or {})
        self.history = list(history or [])
        self.meta = dict(meta or {})
        self.captured_xhr = list(captured_xhr or [])
        self.elapsed = elapsed
        if "root" not in selector_kwargs:
            selector_kwargs.setdefault("content", self._r_text if self._r_text.strip() else "<html></html>")
            selector_kwargs.setdefault("encoding", "utf-8")
        else:
            selector_kwargs.pop("content", None)
        self._r_in_init = True
        try:
            super().__init__(url=url, **selector_kwargs)
        finally:
            self._r_in_init = False

    # -- fields that must not be shadowed by Selector internals -------------
    @property
    def body(self) -> bytes:  # type: ignore[override]
        return self._r_body

    @property
    def text(self) -> str:  # type: ignore[override]
        return self._r_text

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._r_encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        if not getattr(self, "_r_in_init", False):  # Selector.__init__ must not clobber ours
            self._r_encoding = value

    @property
    def url(self) -> str:  # type: ignore[override]
        return self._r_url

    @url.setter
    def url(self, value: str) -> None:
        if not getattr(self, "_r_in_init", False) or not self._r_url:
            self._r_url = value

    # -- helpers ------------------------------------------------------------
    @property
    def ok(self) -> bool:
        """True for status codes below 400."""
        return self.status < 400

    def json(self) -> Any:  # type: ignore[override]
        """Parse the body as JSON."""
        return _json.loads(self._r_text)

    def raise_for_status(self) -> "Response":
        """Raise ``RuntimeError`` for 4xx/5xx statuses, else return self."""
        if self.status >= 400:
            raise RuntimeError("HTTP %d %s for %s" % (self.status, self.reason, self.url))
        return self

    @classmethod
    def from_parts(cls, **kwargs: Any) -> "Response":
        """Alias constructor taking the same keyword fields as ``__init__``."""
        return cls(**kwargs)

    def __repr__(self) -> str:
        return "<Response [%d] %s>" % (self.status, self.url)
