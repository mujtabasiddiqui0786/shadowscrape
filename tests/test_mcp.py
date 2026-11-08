from __future__ import annotations

import io
import json

import pytest

from shadowscrape import cli, mcp_server
from shadowscrape.mcp_server import MCPServer, TOOLS


class El:
    def __init__(self, t):
        self.t = t
        self.html_content = "<b>%s</b>" % t

    def get_all_text(self):
        return self.t

    def to_markdown(self, **kw):
        return "**%s**" % self.t


class Resp:
    def __init__(self, url):
        self.url = url
        self.status = 200
        self.text = "<html>%s</html>" % url

    def css(self, sel):
        return [El("a"), El("b")]

    def get_all_text(self):
        return "text " + self.url

    def to_markdown(self, main_content_only=False, **kw):
        return "md %s %s" % (self.url, main_content_only)


class HttpF:
    @classmethod
    def get(cls, url, *, proxy=None, timeout=None, headers=None, impersonate=None):
        if "bad" in url:
            raise RuntimeError("boom")
        return Resp(url)


class Browser:
    seen = []

    @classmethod
    def fetch(cls, url, *, wait_selector=None, solve_cloudflare=False, timeout=None, proxy=None):
        cls.seen.append((wait_selector, solve_cloudflare, timeout))
        return Resp(url)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    Browser.seen.clear()
    table = {"Fetcher": HttpF, "DynamicFetcher": Browser, "StealthyFetcher": Browser}
    monkeypatch.setattr(cli, "load_fetcher", lambda n: table[n])


def rpc(server, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def call(server, name, args):
    r = rpc(server, "tools/call", {"name": name, "arguments": args})
    return r["result"], (json.loads(r["result"]["content"][0]["text"]) if not r["result"]["isError"] else r["result"]["content"][0]["text"])


def test_initialize_and_lifecycle():
    s = MCPServer()
    r = rpc(s, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in r["result"]["capabilities"] and r["result"]["serverInfo"]["name"] == "shadowscrape"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.initialized
    assert rpc(s, "initialize", {"protocolVersion": "1999"})["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]


def test_ping_and_unknown_method():
    s = MCPServer()
    assert rpc(s, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert rpc(s, "nope")["error"]["code"] == -32601


def test_tools_list():
    r = rpc(MCPServer(), "tools/list")["result"]["tools"]
    assert [t["name"] for t in r] == ["get", "bulk_get", "fetch", "bulk_fetch", "stealthy_fetch", "bulk_stealthy_fetch"]
    for t in r:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        props = t["inputSchema"]["properties"]
        assert {"css_selector", "main_content_only", "extraction_type", "proxy"} <= set(props)
        assert props["extraction_type"]["enum"] == ["markdown", "html", "text"]
    by = {t["name"]: t for t in r}
    assert "solve_cloudflare" in by["stealthy_fetch"]["inputSchema"]["properties"]
    assert "wait_selector" in by["fetch"]["inputSchema"]["properties"]
    assert by["bulk_get"]["inputSchema"]["required"] == ["urls"]


def test_get_default_markdown_main_content():
    res, data = call(MCPServer(), "get", {"url": "https://e.com"})
    assert res["isError"] is False
    assert data == {"url": "https://e.com", "status": 200, "content": ["md https://e.com True"]}


def test_get_css_html_text():
    s = MCPServer()
    assert call(s, "get", {"url": "https://e.com", "css_selector": "p", "extraction_type": "html"})[1]["content"] == ["<b>a</b>", "<b>b</b>"]
    assert call(s, "get", {"url": "https://e.com", "css_selector": "p", "extraction_type": "text"})[1]["content"] == ["a", "b"]
    assert call(s, "get", {"url": "https://e.com", "extraction_type": "text"})[1]["content"] == ["text https://e.com"]
    assert call(s, "get", {"url": "https://e.com", "extraction_type": "html"})[1]["content"] == ["<html>https://e.com</html>"]


def test_bulk_get_partial_failure_keeps_order():
    _, data = call(MCPServer(), "bulk_get", {"urls": ["https://a.com", "https://bad.com", "https://c.com"]})
    assert [d["url"] for d in data] == ["https://a.com", "https://bad.com", "https://c.com"]
    assert "boom" in data[1]["error"] and data[1]["status"] is None
    assert data[2]["status"] == 200


def test_browser_tools_options():
    s = MCPServer()
    call(s, "fetch", {"url": "https://e.com", "wait_selector": "#a", "timeout": 5})
    call(s, "stealthy_fetch", {"url": "https://e.com", "solve_cloudflare": True})
    _, data = call(s, "bulk_stealthy_fetch", {"urls": ["https://a.com", "https://b.com"]})
    assert Browser.seen[0] == ("#a", False, 5000)
    assert Browser.seen[1][1] is True
    assert len(data) == 2 and len(Browser.seen) == 4


def test_tool_errors_are_results_not_protocol_errors():
    s = MCPServer()
    res, text = call(s, "get", {})
    assert res["isError"] and "url" in text
    res, text = call(s, "get", {"url": "ftp://x"})
    assert res["isError"] and "http" in text
    res, text = call(s, "get", {"url": "https://bad.com"})
    assert res["isError"] and "boom" in text
    res, text = call(s, "get", {"url": "https://e.com", "bogus": 1})
    assert res["isError"] and "bogus" in text


def test_unknown_tool_is_invalid_params():
    assert rpc(MCPServer(), "tools/call", {"name": "zzz"})["error"]["code"] == -32602


def test_invalid_request_and_parse_error():
    s = MCPServer()
    assert s.handle({"id": 3, "method": "ping"})["error"]["code"] == -32600
    assert json.loads(s.handle_line("{not json"))["error"]["code"] == -32700
    assert s.handle_line("   ") is None
    assert s.handle({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]})["error"]["code"] == -32602


def test_batch():
    out = MCPServer().handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                              {"jsonrpc": "2.0", "method": "notifications/initialized"},
                              {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
    assert [o["id"] for o in out] == [1, 2]


def test_stdio_round_trip():
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get", "arguments": {"url": "https://e.com", "css_selector": "a", "extraction_type": "text"}}},
    ]
    stdin = io.StringIO("\n".join(json.dumps(l) for l in lines) + "\n")
    stdout = io.StringIO()
    mcp_server.run_stdio(stdin, stdout)
    out = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert [o["id"] for o in out] == [1, 2, 3]
    assert json.loads(out[2]["result"]["content"][0]["text"])["content"] == ["a", "b"]


def test_subprocess_stdio():
    import subprocess
    import sys
    p = subprocess.run([sys.executable, "-m", "shadowscrape.mcp_server"],
                       input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n', capture_output=True, text=True, timeout=30)
    assert len(json.loads(p.stdout)["result"]["tools"]) == len(TOOLS)
