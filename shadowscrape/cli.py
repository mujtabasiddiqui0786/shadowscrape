"""Command line interface for shadowscrape.

Usage examples::

    shadowscrape get https://example.com page.md --css "article" --main-content-only
    shadowscrape stealthy-fetch https://site.com out.html --solve-cloudflare
    shadowscrape extract saved.html out.txt --css "h1::text"
    shadowscrape shell
    shadowscrape mcp [--http --port 8000]
    shadowscrape install

The output file extension selects the format: ``.html`` raw HTML, ``.md`` clean
Markdown, ``.txt`` visible text, ``.json`` a JSON document with metadata.
All fetcher/parser imports are lazy so that ``--help`` stays instant.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "main",
    "build_parser",
    "parse_headers",
    "parse_cookies",
    "format_for_path",
    "extract_content",
    "render_output",
    "write_output",
    "load_fetcher",
    "call_filtered",
]

EXT_FORMATS = {".html": "html", ".htm": "html", ".md": "markdown", ".markdown": "markdown",
               ".txt": "text", ".json": "json"}


# --------------------------------------------------------------------------- helpers
def parse_headers(items: Optional[Sequence[str]]) -> Dict[str, str]:
    """Parse repeated ``"Key: Value"`` strings (or a single JSON object) into a dict."""
    headers: Dict[str, str] = {}
    for item in items or []:
        item = item.strip()
        if item.startswith("{"):
            try:
                headers.update({str(k): str(v) for k, v in json.loads(item).items()})
                continue
            except ValueError:
                raise ValueError("invalid JSON in --headers: %r" % item)
        if ":" not in item:
            raise ValueError("invalid header %r (expected 'Key: Value')" % item)
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def parse_cookies(value: Optional[str]) -> Dict[str, str]:
    """Parse ``"a=b; c=d"`` or a JSON object into a cookie dict."""
    if not value:
        return {}
    value = value.strip()
    if value.startswith("{"):
        try:
            return {str(k): str(v) for k, v in json.loads(value).items()}
        except ValueError:
            raise ValueError("invalid JSON in --cookies")
    cookies: Dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("invalid cookie %r (expected name=value)" % part)
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def format_for_path(path: str) -> str:
    """Return the output format (html|markdown|text|json) implied by a file extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in EXT_FORMATS:
        raise ValueError("unsupported output extension %r (use .html, .md, .txt or .json)" % ext)
    return EXT_FORMATS[ext]


def load_fetcher(name: str) -> Any:
    """Lazily import a fetcher class (``Fetcher``, ``DynamicFetcher``, ``StealthyFetcher``)."""
    import importlib

    module = importlib.import_module("shadowscrape.fetchers")
    return getattr(module, name)


def call_filtered(func: Callable[..., Any], **kwargs: Any) -> Any:
    """Call ``func`` passing only the keyword arguments it accepts (None values dropped).

    Fetcher implementations are developed independently, so unsupported options are
    dropped with a warning on stderr rather than raising ``TypeError``.
    """
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return func(**kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return func(**kwargs)
    accepted = {k: v for k, v in kwargs.items() if k in params}
    for k in kwargs:
        if k not in accepted:
            print("warning: option %r is not supported by this fetcher; ignored" % k, file=sys.stderr)
    return func(**accepted)


def _text_of(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    for attr in ("get_all_text",):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn()
    return str(getattr(obj, "text", obj))


def _html_of(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    return str(getattr(obj, "html_content", None) or getattr(obj, "text", "") or obj)


def _markdown_of(obj: Any, main_content_only: bool) -> str:
    if isinstance(obj, str):
        return obj
    fn = getattr(obj, "to_markdown", None)
    if callable(fn):
        try:
            return fn(main_content_only=main_content_only)
        except TypeError:
            return fn()
    return _text_of(obj)


def extract_content(response: Any, css: Optional[str] = None, main_content_only: bool = False,
                    extraction_type: str = "markdown") -> List[str]:
    """Extract content from a Response/Selector as a list of strings.

    With ``css`` one string is returned per matched element (``::text``/``::attr()``
    results are returned verbatim); otherwise a single-element list with the whole page.
    ``extraction_type`` is ``markdown``, ``html`` or ``text``.
    """
    if extraction_type not in ("markdown", "html", "text"):
        raise ValueError("extraction_type must be markdown, html or text")
    if css:
        out: List[str] = []
        for el in response.css(css):
            if extraction_type == "html":
                out.append(_html_of(el))
            elif extraction_type == "text":
                out.append(_text_of(el))
            else:
                out.append(_markdown_of(el, False))
        return out
    if extraction_type == "html":
        body = getattr(response, "text", None)
        return [body if isinstance(body, str) and body else _html_of(response)]
    if extraction_type == "text":
        return [_text_of(response)]
    return [_markdown_of(response, main_content_only)]


def render_output(response: Any, fmt: str, css: Optional[str] = None,
                  main_content_only: bool = False) -> str:
    """Render a response to the string written for output format ``fmt``."""
    if fmt == "json":
        content = extract_content(response, css, main_content_only, "text")
        doc = {
            "url": getattr(response, "url", ""),
            "status": getattr(response, "status", None),
            "headers": dict(getattr(response, "headers", {}) or {}),
            "content": content,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2)
    etype = {"html": "html", "markdown": "markdown", "text": "text"}[fmt]
    return "\n\n".join(extract_content(response, css, main_content_only, etype))


def write_output(response: Any, path: str, css: Optional[str] = None,
                 main_content_only: bool = False) -> str:
    """Write ``response`` to ``path`` in the format implied by its extension."""
    fmt = format_for_path(path)
    data = render_output(response, fmt, css, main_content_only)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)
    return fmt


# --------------------------------------------------------------------------- parser
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("url", help="URL to fetch")
    p.add_argument("outfile", help="output file; extension selects format (.html .md .txt .json)")
    p.add_argument("--css", "--css-selector", dest="css", help="only keep elements matching this CSS selector")
    p.add_argument("--main-content-only", action="store_true", help="strip nav/footer/ads (markdown output)")
    p.add_argument("--proxy", help="proxy URL, e.g. http://user:pass@host:8080")
    p.add_argument("--headers", "-H", action="append", metavar="'Key: Value'", help="request header (repeatable) or JSON object")
    p.add_argument("--cookies", help="cookies as 'a=b; c=d' or JSON object")
    p.add_argument("--timeout", type=float, default=30.0, help="timeout in seconds (default 30)")


def build_parser() -> argparse.ArgumentParser:
    """Build the top level argparse parser."""
    parser = argparse.ArgumentParser(prog="shadowscrape", description="shadowscrape: stealthy web scraping toolkit")
    sub = parser.add_subparsers(dest="command")

    for name in ("get", "post"):
        p = sub.add_parser(name, help="HTTP %s with TLS impersonation" % name.upper())
        _add_common(p)
        p.add_argument("--impersonate", default=None, help="browser to impersonate (e.g. chrome, firefox)")
        p.add_argument("--no-follow-redirects", action="store_true")
        p.add_argument("--params", action="append", metavar="k=v", help="query parameter (repeatable)")
        if name == "post":
            p.add_argument("--data", "-d", help="form body, as k=v&k2=v2 or raw string")
            p.add_argument("--json", dest="json_body", help="JSON request body")

    p = sub.add_parser("fetch", help="fetch with a real (Playwright) browser")
    _add_common(p)
    p.add_argument("--wait-selector", help="wait for this CSS selector before capturing")
    p.add_argument("--wait", type=float, default=None, help="extra seconds to wait after load")
    p.add_argument("--network-idle", action="store_true")
    p.add_argument("--no-headless", action="store_true", help="show the browser window")

    p = sub.add_parser("stealthy-fetch", help="fetch with the stealth browser (anti-bot evasions)")
    _add_common(p)
    p.add_argument("--wait-selector", help="wait for this CSS selector before capturing")
    p.add_argument("--wait", type=float, default=None)
    p.add_argument("--network-idle", action="store_true")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("--solve-cloudflare", action="store_true", help="solve Cloudflare Turnstile/interstitial")

    p = sub.add_parser("extract", help="extract content from a local HTML file (or '-' for stdin)")
    p.add_argument("source", help="HTML file or '-'")
    p.add_argument("outfile", nargs="?", default="-", help="output file or '-' for stdout (stdout uses markdown)")
    p.add_argument("--css", "--css-selector", dest="css")
    p.add_argument("--main-content-only", action="store_true")
    p.add_argument("--format", choices=["html", "markdown", "text", "json"], help="override format (default from extension)")

    p = sub.add_parser("shell", help="interactive scraping shell")
    p.add_argument("-c", "--code", help="run this code in the shell namespace and exit")

    p = sub.add_parser("mcp", help="run the MCP server (stdio by default)")
    p.add_argument("--http", action="store_true", help="serve MCP over HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("install", help="install browser dependencies (playwright chromium)")
    p.add_argument("--with-deps", action="store_true", help="also install OS dependencies")
    p.add_argument("--force", action="store_true")
    return parser


# --------------------------------------------------------------------------- commands
def _parse_kv(items: Optional[Sequence[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError("expected k=v, got %r" % it)
        k, v = it.split("=", 1)
        out[k] = v
    return out


def _fetch_response(args: argparse.Namespace) -> Any:
    cmd = args.command
    headers = parse_headers(args.headers) or None
    cookies = parse_cookies(args.cookies) or None
    if cmd in ("get", "post"):
        fetcher = load_fetcher("Fetcher")
        func = getattr(fetcher, cmd)
        kw: Dict[str, Any] = dict(headers=headers, cookies=cookies, proxy=args.proxy, timeout=args.timeout,
                                  impersonate=args.impersonate,
                                  follow_redirects=False if args.no_follow_redirects else None,
                                  params=_parse_kv(args.params) or None)
        if cmd == "post":
            kw["data"] = args.data
            if args.json_body:
                kw["json"] = json.loads(args.json_body)
        return call_filtered(func, url=args.url, **kw)
    cls = load_fetcher("DynamicFetcher" if cmd == "fetch" else "StealthyFetcher")
    kw = dict(headers=headers, cookies=cookies, proxy=args.proxy, timeout=args.timeout * 1000,
              wait_selector=args.wait_selector, wait=args.wait,
              network_idle=True if args.network_idle else None,
              headless=False if args.no_headless else None)
    if cmd == "stealthy-fetch" and args.solve_cloudflare:
        kw["solve_cloudflare"] = True
    return call_filtered(cls.fetch, url=args.url, **kw)


def _cmd_fetch(args: argparse.Namespace) -> int:
    format_for_path(args.outfile)  # validate before doing network work
    response = _fetch_response(args)
    status = getattr(response, "status", None)
    fmt = write_output(response, args.outfile, args.css, args.main_content_only)
    print("[%s] %s -> %s (%s)" % (status, args.url, args.outfile, fmt))
    return 0 if status is None or int(status) < 400 else 2


def _cmd_extract(args: argparse.Namespace) -> int:
    from shadowscrape.parser import Selector

    if args.source == "-":
        html = sys.stdin.read()
    else:
        with open(args.source, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    page = Selector(html)
    if args.outfile == "-":
        fmt = args.format or "markdown"
        sys.stdout.write(render_output(page, fmt, args.css, args.main_content_only) + "\n")
    else:
        fmt = args.format or format_for_path(args.outfile)
        data = render_output(page, fmt, args.css, args.main_content_only)
        with open(args.outfile, "w", encoding="utf-8") as fh:
            fh.write(data)
        print("%s -> %s (%s)" % (args.source, args.outfile, fmt))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if args.with_deps:
        cmd.append("--with-deps")
    if args.force:
        cmd.append("--force")
    print("running: " + " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command in ("get", "post", "fetch", "stealthy-fetch"):
            return _cmd_fetch(args)
        if args.command == "extract":
            return _cmd_extract(args)
        if args.command == "install":
            return _cmd_install(args)
        if args.command == "shell":
            from shadowscrape.shell import start_shell

            return start_shell(code=args.code) or 0
        if args.command == "mcp":
            from shadowscrape.mcp_server import run_http, run_stdio

            if args.http:
                run_http(args.host, args.port)
            else:
                run_stdio()
            return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # network/browser failures
        print("error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
