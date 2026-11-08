"""Request object, URL canonicalisation and fingerprinting."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize_url(url: str) -> str:
    """Normalise a URL (lowercase host, default ports, sorted query, no fragment)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if parts.username:
        auth = parts.username + (":" + parts.password if parts.password else "")
        netloc = auth + "@" + netloc
    if port and _DEFAULT_PORTS.get(scheme) != port:
        netloc += ":%d" % port
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def _body_repr(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:  # pragma: no cover
        return repr(data)


@dataclass
class Request:
    """A crawl request.

    ``callback`` may be a callable, the *name* of a Spider method (needed for
    checkpointing, callables that are spider methods are converted to names), or
    ``None`` (meaning ``Spider.parse``).  ``sid`` selects a named session.
    """

    url: str
    callback: Optional[Union[str, Callable[..., Any]]] = None
    method: str = "GET"
    headers: Optional[Dict[str, str]] = None
    data: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    dont_filter: bool = False
    sid: Optional[str] = None
    cb_kwargs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.method = (self.method or "GET").upper()

    def fingerprint(self) -> str:
        """Stable hash identifying this request for de-duplication."""
        raw = "\n".join([self.method, canonicalize_url(self.url), _body_repr(self.data)])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def replace(self, **changes: Any) -> "Request":
        """Return a copy with the given fields replaced (meta/cb_kwargs are copied)."""
        changes.setdefault("meta", dict(self.meta))
        changes.setdefault("cb_kwargs", dict(self.cb_kwargs))
        return replace(self, **changes)

    # -- serialisation (checkpointing) -------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        cb = self.callback
        if cb is not None and not isinstance(cb, str):
            cb = getattr(cb, "__name__", None)
        data: Any = self.data
        if isinstance(data, bytes):
            data = {"__b64__": base64.b64encode(data).decode("ascii")}
        meta = {k: v for k, v in self.meta.items() if not k.startswith("_")}
        return {
            "url": self.url, "callback": cb, "method": self.method, "headers": self.headers,
            "data": data, "meta": meta, "priority": self.priority,
            "dont_filter": self.dont_filter, "sid": self.sid, "cb_kwargs": self.cb_kwargs,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Request":
        d = dict(d)
        data = d.get("data")
        if isinstance(data, dict) and set(data) == {"__b64__"}:
            d["data"] = base64.b64decode(data["__b64__"])
        return cls(**d)

    def __repr__(self) -> str:
        return "<Request %s %s>" % (self.method, self.url)
