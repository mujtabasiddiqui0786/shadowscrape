"""MCP (Model Context Protocol) server exposing shadowscrape fetchers as tools.

The official ``mcp`` SDK requires Python >= 3.10, so this module ships a small,
spec-conformant JSON-RPC 2.0 implementation instead: newline-delimited JSON over
stdio (and an optional minimal HTTP POST transport). It supports ``initialize``,
``notifications/initialized``, ``ping``, ``tools/list`` and ``tools/call``.

Tools: ``get``, ``bulk_get``, ``fetch``, ``bulk_fetch``, ``stealthy_fetch``,
``bulk_stealthy_fetch``. Use ``css_selector`` to pre-filter pages server side and
save tokens.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

__all__ = ["MCPServer", "TOOLS", "run_stdio", "run_http", "handle_message", "PROTOCOL_VERSIONS"]

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "shadowscrape", "version": "0.1.0"}

# --------------------------------------------------------------------------- schemas
_EXTRACTION = {
    "extraction_type": {
        "type": "string", "enum": ["markdown", "html", "text"], "default": "markdown",
        "description": "Output format. markdown is the most token-efficient for reading; html for structure; text for plain text.",
    },
    "css_selector": {
        "type": "string",
        "description": "CSS selector to pre-filter the page BEFORE returning content (e.g. 'article', '.price::text', 'a::attr(href)'). Strongly recommended: it cuts tokens dramatically. One result string per match.",
    },
    "main_content_only": {
        "type": "boolean", "default": True,
        "description": "Strip navigation, headers, footers and sidebars, keeping the main content. Ignored when css_selector is set.",
    },
}
_PROXY = {"proxy": {"type": "string", "description": "Proxy URL, e.g. http://user:pass@host:8080 or socks5://host:1080."}}
_TIMEOUT = {"timeout": {"type": "number", "default": 30, "description": "Timeout in seconds."}}
_HTTP_PROPS: Dict[str, Any] = {
    "impersonate": {"type": "string", "description": "Browser TLS/HTTP2 fingerprint to impersonate, e.g. 'chrome', 'firefox', 'safari'."},
    "headers": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Extra request headers."},
    "cookies": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Request cookies."},
    "params": {"type": "object", "description": "Query string parameters."},
    "follow_redirects": {"type": "boolean", "default": True},
    "retries": {"type": "integer", "default": 3, "description": "Retry attempts on failure."},
    **_PROXY, **_TIMEOUT,
}
_BROWSER_PROPS: Dict[str, Any] = {
    "wait_selector": {"type": "string", "description": "CSS selector to wait for before capturing the page (use for JS-rendered content)."},
    "wait": {"type": "number", "default": 0, "description": "Extra seconds to wait after load."},
    "network_idle": {"type": "boolean", "default": False, "description": "Wait until the network is idle."},
    "headless": {"type": "boolean", "default": True},
    "disable_resources": {"type": "boolean", "default": False, "description": "Block images/fonts/media to speed up loading."},
    **_PROXY, **_TIMEOUT,
}
_STEALTH_PROPS: Dict[str, Any] = {
    **_BROWSER_PROPS,
    "solve_cloudflare": {"type": "boolean", "default": False, "description": "Detect and solve Cloudflare Turnstile / interstitial challenges using real browser behaviour."},
    "humanize": {"type": "boolean", "default": True, "description": "Human-like mouse movement and typing."},
}
_URL = {"url": {"type": "string", "description": "The URL to fetch (http/https)."}}
_URLS = {"urls": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "URLs to fetch."}}


def _tool(name: str, description: str, props: Dict[str, Any], required: List[str], kind: str, bulk: bool) -> Dict[str, Any]:
    return {"name": name, "description": description, "kind": kind, "bulk": bulk,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


_DESC_NOTE = " Returns JSON: {url, status, content: [strings]}. Use css_selector to return only what you need."
TOOLS: List[Dict[str, Any]] = [
    _tool("get", "Fast HTTP GET with browser TLS/HTTP2 impersonation. Best default for static pages, APIs and light bot protection. No JavaScript execution." + _DESC_NOTE,
          {**_URL, **_EXTRACTION, **_HTTP_PROPS}, ["url"], "http", False),
    _tool("bulk_get", "Fast HTTP GET for several URLs concurrently (same options as get). Returns a JSON list, one entry per URL in order." + _DESC_NOTE,
          {**_URLS, **_EXTRACTION, **_HTTP_PROPS}, ["urls"], "http", True),
    _tool("fetch", "Open the URL in a real Chromium browser (Playwright) and return the rendered page. Use when content needs JavaScript. Slower than get." + _DESC_NOTE,
          {**_URL, **_EXTRACTION, **_BROWSER_PROPS}, ["url"], "dynamic", False),
    _tool("bulk_fetch", "Browser-render several URLs (sequentially, same options as fetch). Returns a JSON list, one entry per URL." + _DESC_NOTE,
          {**_URLS, **_EXTRACTION, **_BROWSER_PROPS}, ["urls"], "dynamic", True),
    _tool("stealthy_fetch", "Stealth browser fetch with anti-detection evasions, fingerprint consistency and optional Cloudflare solving. Use for protected sites where get/fetch are blocked. Slowest, most capable." + _DESC_NOTE,
          {**_URL, **_EXTRACTION, **_STEALTH_PROPS}, ["url"], "stealthy", False),
    _tool("bulk_stealthy_fetch", "Stealth browser fetch for several URLs (sequentially, same options as stealthy_fetch). Returns a JSON list, one entry per URL." + _DESC_NOTE,
          {**_URLS, **_EXTRACTION, **_STEALTH_PROPS}, ["urls"], "stealthy", True),
]
_TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------- execution
def _fetch_one(kind: str, url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    from shadowscrape import cli

    css = opts.get("css_selector") or None
    etype = opts.get("extraction_type") or "markdown"
    mco = opts.get("main_content_only", True)
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://: %r" % url)
    fetch_opts = {k: v for k, v in opts.items()
                  if k not in ("css_selector", "main_content_only", "extraction_type", "url", "urls")}
    if kind == "http":
        func = cli.load_fetcher("Fetcher").get
    else:
        cls = cli.load_fetcher("DynamicFetcher" if kind == "dynamic" else "StealthyFetcher")
        func = cls.fetch
        if "timeout" in fetch_opts and fetch_opts["timeout"] is not None:
            fetch_opts["timeout"] = fetch_opts["timeout"] * 1000  # browsers use milliseconds
    response = cli.call_filtered(func, url=url, **fetch_opts)
    content = cli.extract_content(response, css, bool(mco), etype)
    return {"url": getattr(response, "url", url) or url, "status": getattr(response, "status", None), "content": content}


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Run a tool and return a JSON-serialisable result (raises on bad input)."""
    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    schema = tool["inputSchema"]
    for req in schema["required"]:
        if req not in arguments:
            raise ValueError("missing required argument: %s" % req)
    unknown = set(arguments) - set(schema["properties"])
    if unknown:
        raise ValueError("unknown argument(s): %s" % ", ".join(sorted(unknown)))
    if not tool["bulk"]:
        return _fetch_one(tool["kind"], arguments["url"], arguments)
    urls = arguments["urls"]
    if not isinstance(urls, list) or not urls:
        raise ValueError("urls must be a non-empty list")

    def one(u: str) -> Dict[str, Any]:
        try:
            return _fetch_one(tool["kind"], u, arguments)
        except Exception as exc:  # one failure must not sink the batch
            return {"url": u, "status": None, "content": [], "error": "%s: %s" % (type(exc).__name__, exc)}

    if tool["kind"] == "http":
        with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
            return list(pool.map(one, urls))
    return [one(u) for u in urls]  # browsers: sequential (sync Playwright is not thread-safe)


# --------------------------------------------------------------------------- JSON-RPC
class MCPServer:
    """Stateless-ish MCP request handler. ``handle`` maps a decoded message to a response."""

    def __init__(self, caller: Callable[[str, Dict[str, Any]], Any] = call_tool) -> None:
        self.caller = caller
        self.initialized = False

    @staticmethod
    def _error(id_: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}

    def handle(self, msg: Any) -> Optional[Any]:
        """Handle one decoded JSON-RPC message (or batch); None means no response."""
        if isinstance(msg, list):
            if not msg:
                return self._error(None, -32600, "Invalid Request: empty batch")
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            return out or None
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return None  # a response to something we never sent; ignore
            return self._error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method = msg["method"]
        is_notification = "id" not in msg
        id_ = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else self._error(id_, -32602, "Invalid params")
        if is_notification:
            if method == "notifications/initialized":
                self.initialized = True
            return None
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result: Dict[str, Any] = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "Web scraping tools. Prefer get; escalate to fetch (JS) then stealthy_fetch (anti-bot). Always pass css_selector when you know what you need.",
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]}
            elif method == "tools/call":
                name = params.get("name")
                if name not in _TOOL_BY_NAME:
                    return self._error(id_, -32602, "Unknown tool: %s" % name)
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    return self._error(id_, -32602, "arguments must be an object")
                try:
                    data = self.caller(name, args)
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
                except ValueError as exc:
                    result = {"content": [{"type": "text", "text": "Invalid arguments: %s" % exc}], "isError": True}
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}], "isError": True}
            else:
                return self._error(id_, -32601, "Method not found: %s" % method)
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(id_, -32603, "Internal error: %s" % exc)
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    def handle_line(self, line: str) -> Optional[str]:
        """Decode a JSON line, handle it, and return the encoded response (or None)."""
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            return json.dumps(self._error(None, -32700, "Parse error"))
        resp = self.handle(msg)
        return None if resp is None else json.dumps(resp, ensure_ascii=False)


def handle_message(msg: Any) -> Optional[Any]:
    """Convenience wrapper using a fresh :class:`MCPServer`."""
    return MCPServer().handle(msg)


def run_stdio(stdin: Any = None, stdout: Any = None) -> None:
    """Serve newline-delimited JSON-RPC on stdin/stdout until EOF. Logs go to stderr."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = MCPServer()
    for line in stdin:
        out = server.handle_line(line)
        if out is not None:
            stdout.write(out + "\n")
            stdout.flush()


def run_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve MCP over plain HTTP: POST /mcp with a JSON-RPC body returns a JSON response."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    server = MCPServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            out = server.handle_line(self.rfile.read(length).decode("utf-8", "replace"))
            if out is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = out.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            print(fmt % args, file=sys.stderr)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print("shadowscrape MCP listening on http://%s:%d/mcp" % (host, port), file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":  # pragma: no cover
    run_stdio()
