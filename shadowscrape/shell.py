"""Interactive scraping shell with a curl-command converter.

``shadowscrape shell`` drops into IPython (if installed) or a stdlib
``code.InteractiveConsole`` with ``Fetcher``, ``AsyncFetcher``, ``DynamicFetcher``,
``StealthyFetcher``, ``Selector``, ``page`` (last response), ``view(page)`` and
``curl2fetcher(curl_command)`` preloaded.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shlex
import tempfile
import webbrowser
from typing import Any, Dict, Optional

__all__ = ["parse_curl", "curl2fetcher", "build_namespace", "start_shell", "view"]

_DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"}
_IGNORED_NOARG = {"--compressed", "-L", "--location", "-k", "--insecure", "-s", "--silent", "-S",
                  "--show-error", "-v", "--verbose", "-i", "--include", "-g", "--globoff",
                  "--http1.1", "--http2", "-f", "--fail", "-#", "--progress-bar"}
_IGNORED_ARG = {"-o", "--output", "--connect-timeout", "-m", "--max-time", "--retry", "-e", "--referer"}


def _split(cmd: str) -> list:
    """shlex split that tolerates line continuations and bash ``$'...'`` quoting."""
    cmd = re.sub(r"\\\r?\n", " ", cmd.strip())

    def ansi_c(m: "re.Match[str]") -> str:
        body = m.group(1)
        try:
            decoded = bytes(body, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            decoded = body
        return shlex.quote(decoded)

    cmd = re.sub(r"\$'((?:[^'\\]|\\.)*)'", ansi_c, cmd)
    return shlex.split(cmd)


def parse_curl(command: str) -> Dict[str, Any]:
    """Parse a copied ``curl`` command into fetcher keyword arguments.

    Returns a dict with ``method``, ``url``, ``headers``, ``cookies`` and optionally
    ``data`` (str) or ``json`` (parsed) plus ``proxy`` / ``auth``.
    """
    tokens = _split(command)
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]
    method: Optional[str] = None
    url: Optional[str] = None
    headers: Dict[str, str] = {}
    cookies: Dict[str, str] = {}
    data_parts: list = []
    proxy: Optional[str] = None
    auth: Optional[tuple] = None
    head = False
    get_mode = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        val: Optional[str] = None
        if t.startswith("--") and "=" in t:
            t, val = t.split("=", 1)
        elif re.match(r"^-[XHbdAux]", t) and len(t) > 2 and not t.startswith("--"):
            t, val = t[:2], t[2:]

        def arg() -> str:
            nonlocal i, val
            if val is not None:
                return val
            i += 1
            if i >= len(tokens):
                raise ValueError("curl option %s requires an argument" % t)
            return tokens[i]

        if t in ("-X", "--request"):
            method = arg().upper()
        elif t in ("-H", "--header"):
            h = arg()
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
        elif t in ("-b", "--cookie"):
            c = arg()
            if "=" in c:
                for part in c.split(";"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookies[k.strip()] = v.strip()
        elif t in _DATA_FLAGS:
            d = arg()
            if t == "--data-urlencode" and "=" in d:
                k, v = d.split("=", 1)
                from urllib.parse import quote_plus
                d = "%s=%s" % (k, quote_plus(v)) if k else quote_plus(v)
            data_parts.append(d)
        elif t in ("-A", "--user-agent"):
            headers["User-Agent"] = arg()
        elif t in ("-u", "--user"):
            u = arg()
            auth = tuple(u.split(":", 1)) if ":" in u else (u, "")  # type: ignore[assignment]
        elif t in ("-x", "--proxy"):
            proxy = arg()
        elif t in ("-I", "--head"):
            head = True
        elif t in ("-G", "--get"):
            get_mode = True
        elif t == "--url":
            url = arg()
        elif t in _IGNORED_ARG:
            arg()
        elif t in _IGNORED_NOARG:
            pass
        elif t.startswith("-") and len(t) > 1:
            pass  # unknown flag: ignore
        else:
            url = t
        i += 1

    if not url:
        raise ValueError("no URL found in curl command")
    # a Cookie header counts as cookies
    for k in list(headers):
        if k.lower() == "cookie":
            for part in headers.pop(k).split(";"):
                if "=" in part:
                    ck, cv = part.split("=", 1)
                    cookies.setdefault(ck.strip(), cv.strip())

    result: Dict[str, Any] = {"url": url, "headers": headers, "cookies": cookies}
    body = "&".join(data_parts) if data_parts else None
    if get_mode and body:
        url += ("&" if "?" in url else "?") + body
        result["url"] = url
        body = None
    if method is None:
        method = "HEAD" if head else ("POST" if body is not None else "GET")
    result["method"] = method
    if body is not None:
        ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        if "json" in ctype.lower():
            try:
                result["json"] = json.loads(body)
            except ValueError:
                result["data"] = body
        else:
            result["data"] = body
    if proxy:
        result["proxy"] = proxy
    if auth:
        result["auth"] = auth
    return result


def curl2fetcher(curl_command: str, execute: bool = True, _fetcher: Any = None) -> Any:
    """Convert a copied curl command into a Fetcher call.

    With ``execute=True`` (default) the request is sent through ``Fetcher`` and the
    Response returned; with ``execute=False`` the parsed kwargs dict is returned.
    """
    spec = parse_curl(curl_command)
    if not execute:
        return spec
    from shadowscrape.cli import call_filtered, load_fetcher

    fetcher = _fetcher if _fetcher is not None else load_fetcher("Fetcher")
    method = spec.pop("method").lower()
    func = getattr(fetcher, method, None)
    if func is None:
        raise ValueError("Fetcher does not support HTTP method %s" % method.upper())
    if spec.get("auth") is not None:
        import base64
        raw = ("%s:%s" % spec.pop("auth")).encode()
        spec["headers"]["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    spec["headers"] = spec["headers"] or None
    spec["cookies"] = spec["cookies"] or None
    return call_filtered(func, **spec)


def view(page: Any, open_browser: bool = True) -> str:
    """Write ``page`` to a temp HTML file and open it in the default browser; returns the path."""
    if page is None:
        raise ValueError("no page to view; fetch something first")
    html = getattr(page, "text", None) or getattr(page, "html_content", "") or str(page)
    url = getattr(page, "url", "")
    if url and "<base" not in html.lower():
        html = re.sub(r"(<head[^>]*>)", r'\1<base href="%s">' % url, html, count=1, flags=re.I) \
            if re.search(r"<head", html, re.I) else '<base href="%s">' % url + html
    fd, path = tempfile.mkstemp(suffix=".html", prefix="shadowscrape_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(html)
    if open_browser:
        webbrowser.open("file://" + path)
    return path


class _Tracked:
    """Proxy around a fetcher class that stores the last Response as ``page``."""

    def __init__(self, target: Any, ns: Dict[str, Any]) -> None:
        self._target = target
        self._ns = ns

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        @functools.wraps(attr)
        def wrapper(*a: Any, **kw: Any) -> Any:
            result = attr(*a, **kw)
            if hasattr(result, "status") and hasattr(result, "css"):
                self._ns["page"] = result
            return result

        return wrapper

    def __repr__(self) -> str:
        return repr(self._target)


_BANNER = """shadowscrape shell
  Fetcher / AsyncFetcher / DynamicFetcher / StealthyFetcher / Selector
  page            last response (set automatically)
  view(page)      open the page in your browser
  curl2fetcher("curl ...")   run a copied curl command via Fetcher
"""


def build_namespace() -> Dict[str, Any]:
    """Create the shell namespace; unavailable optional classes are simply omitted."""
    ns: Dict[str, Any] = {"page": None, "curl2fetcher": None, "view": None}
    from shadowscrape.cli import load_fetcher

    for name in ("Fetcher", "AsyncFetcher", "DynamicFetcher", "StealthyFetcher"):
        try:
            cls = load_fetcher(name)
        except Exception as exc:  # missing playwright etc.
            ns[name] = None
            ns.setdefault("_import_errors", {})[name] = repr(exc)
            continue
        ns[name] = _Tracked(cls, ns) if not name.startswith("Async") else cls
    try:
        from shadowscrape.parser import Selector
        ns["Selector"] = Selector
    except Exception as exc:
        ns["Selector"] = None
        ns.setdefault("_import_errors", {})["Selector"] = repr(exc)

    def _curl(cmd: str, execute: bool = True) -> Any:
        res = curl2fetcher(cmd, execute=execute, _fetcher=ns.get("Fetcher"))
        if execute and hasattr(res, "status"):
            ns["page"] = res
        return res

    def _view(p: Any = None) -> str:
        return view(p if p is not None else ns.get("page"))

    ns["curl2fetcher"] = _curl
    ns["view"] = _view
    return ns


def start_shell(code: Optional[str] = None) -> int:  # noqa: A002 - shadows module only locally
    """Start the interactive shell, or exec ``code`` in the namespace and return."""
    ns = build_namespace()
    if code is not None:
        exec(compile(code, "<shadowscrape-shell>", "exec"), ns)  # noqa: S102
        return 0
    try:
        from IPython import start_ipython  # type: ignore
        from traitlets.config import Config  # type: ignore

        cfg = Config()
        cfg.TerminalInteractiveShell.banner1 = _BANNER
        start_ipython(argv=[], user_ns=ns, config=cfg)
    except ImportError:
        import code as _code

        _code.InteractiveConsole(ns).interact(banner=_BANNER, exitmsg="")
    return 0
