"""Small helpers shared by spider templates (independent of the parser module)."""
from __future__ import annotations

import inspect
import re
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from urllib.parse import urldefrag, urljoin, urlsplit

import lxml.html
from cssselect import HTMLTranslator

from .request import Request

_PSEUDO = re.compile(r"^(?P<sel>.*?)\s*::(?P<kind>text|attr\((?P<attr>[^)]+)\))\s*$", re.S)
_WS = re.compile(r"\s+")
_translator = HTMLTranslator()


async def aiter_results(out: Any) -> AsyncIterator[Any]:
    """Normalise whatever a callback returned into an async stream of items/requests."""
    if inspect.isawaitable(out):
        out = await out
    if out is None:
        return
    if hasattr(out, "__aiter__"):
        async for x in out:
            yield x
    elif isinstance(out, (dict, Request, str, bytes)):
        yield out
    elif hasattr(out, "__iter__"):
        for x in out:
            yield x
    else:
        yield out


def get_doc(response: Any) -> Any:
    """Parse (and cache) a response body into an lxml HTML document."""
    doc = getattr(response, "_ss_doc", None)
    if doc is not None:
        return doc
    body = getattr(response, "body", None)
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not body:
        text = getattr(response, "text", "") or ""
        body = text.encode("utf-8")
    if not body.strip():
        doc = lxml.html.fromstring("<html></html>")
    else:
        doc = lxml.html.document_fromstring(body)
    try:
        response._ss_doc = doc
    except Exception:
        pass
    return doc


def base_url_of(response: Any, doc: Any = None) -> str:
    url = getattr(response, "url", "") or ""
    doc = doc if doc is not None else get_doc(response)
    try:
        hrefs = doc.xpath("//base/@href")
    except Exception:
        hrefs = []
    return urljoin(url, hrefs[0]) if hrefs else url


@lru_cache(maxsize=512)
def _xpath_for(css: str) -> str:
    return _translator.css_to_xpath(css)


def _text(el: Any) -> str:
    if isinstance(el, str):
        return _WS.sub(" ", el).strip()
    return _WS.sub(" ", el.text_content()).strip()


def select(root: Any, spec: str, base_url: Optional[str] = None) -> List[str]:
    """Return string values for a CSS/XPath ``spec`` evaluated under ``root``.

    CSS supports ``::text`` and ``::attr(name)``; a bare CSS selector yields the
    element's normalised text.  ``href``/``src``/``action`` attributes are made
    absolute when ``base_url`` is given.  XPath is used when ``spec`` starts with
    ``/``, ``./`` or ``xpath:``.
    """
    attr: Optional[str] = None
    if spec.startswith("xpath:"):
        nodes = root.xpath(spec[6:])
    elif spec.startswith(("/", "./", "(")):
        nodes = root.xpath(spec)
    else:
        m = _PSEUDO.match(spec)
        css = m.group("sel") if m else spec
        if m and m.group("attr"):
            attr = m.group("attr").strip()
        nodes = [root] if not css.strip() else root.xpath(_xpath_for(css))
    out: List[str] = []
    for n in nodes:
        if attr is not None:
            if hasattr(n, "get") and n.get(attr) is not None:
                v = n.get(attr).strip()
                if base_url and attr in ("href", "src", "action") and v:
                    v = urljoin(base_url, v)
                out.append(v)
        elif isinstance(n, str):
            out.append(_text(n))
        else:
            out.append(_text(n))
    return out


FieldSpec = Union[str, tuple, Dict[str, Any]]


def extract_fields(root: Any, fields: Dict[str, FieldSpec], base_url: Optional[str] = None) -> Dict[str, Any]:
    """Apply a ``{name: spec}`` map.  ``spec`` is a selector string, ``(selector, "all")``
    or ``{"css"|"xpath": ..., "all": bool, "default": ...}``."""
    item: Dict[str, Any] = {}
    for name, spec in fields.items():
        want_all, default = False, None
        if isinstance(spec, tuple):
            sel, want_all = spec[0], (len(spec) > 1 and spec[1] in ("all", True))
        elif isinstance(spec, dict):
            sel = spec.get("css") or ("xpath:" + spec["xpath"] if spec.get("xpath") else "")
            want_all, default = bool(spec.get("all")), spec.get("default")
        else:
            sel = spec
        vals = [v for v in select(root, sel, base_url) if v != ""] if sel else []
        if want_all:
            item[name] = vals if vals else (default if default is not None else [])
        else:
            item[name] = vals[0] if vals else default
    return item


def clean_link(url: str) -> Optional[str]:
    """Strip fragment; return None for non-http(s) URLs."""
    url, _ = urldefrag(url.strip())
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return None
    return url


def host_matches(host: str, domains: List[str]) -> bool:
    host = (host or "").lower().split(":")[0]
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)
