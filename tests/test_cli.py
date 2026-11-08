from __future__ import annotations

import json

import pytest

from shadowscrape import cli


class FakeEl:
    def __init__(self, text, html):
        self._t, self.html_content = text, html

    def get_all_text(self):
        return self._t

    def to_markdown(self, **kw):
        return "**%s**" % self._t


class FakeResponse:
    status = 200
    url = "https://example.com/"
    headers = {"content-type": "text/html"}
    text = "<html><body><h1>Hi</h1><p>x</p></body></html>"

    def css(self, sel):
        return [FakeEl("Hi", "<h1>Hi</h1>")] if sel == "h1" else []

    def get_all_text(self):
        return "Hi\nx"

    def to_markdown(self, main_content_only=False, **kw):
        return "# Hi (main)" if main_content_only else "# Hi\n\nx"


class FakeFetcher:
    calls = []

    @classmethod
    def get(cls, url, *, headers=None, cookies=None, proxy=None, timeout=None, impersonate=None):
        cls.calls.append(("get", url, headers, cookies, proxy, timeout, impersonate))
        return FakeResponse()

    @classmethod
    def post(cls, url, *, data=None, json=None, headers=None, timeout=None):
        cls.calls.append(("post", url, data, json))
        return FakeResponse()


class FakeBrowser:
    calls = []

    @classmethod
    def fetch(cls, url, *, wait_selector=None, proxy=None, timeout=None, solve_cloudflare=False, **kw):
        cls.calls.append((url, wait_selector, proxy, timeout, solve_cloudflare))
        return FakeResponse()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeFetcher.calls.clear()
    FakeBrowser.calls.clear()
    table = {"Fetcher": FakeFetcher, "DynamicFetcher": FakeBrowser, "StealthyFetcher": FakeBrowser}
    monkeypatch.setattr(cli, "load_fetcher", lambda name: table[name])


def test_parse_headers_and_cookies():
    assert cli.parse_headers(["A: b", "C:d:e"]) == {"A": "b", "C": "d:e"}
    assert cli.parse_headers(['{"X": "1"}']) == {"X": "1"}
    with pytest.raises(ValueError):
        cli.parse_headers(["nocolon"])
    assert cli.parse_cookies("a=1; b=2") == {"a": "1", "b": "2"}
    assert cli.parse_cookies('{"a": 1}') == {"a": "1"}
    assert cli.parse_cookies(None) == {}


@pytest.mark.parametrize("name,fmt", [("a.html", "html"), ("a.MD", "markdown"), ("a.txt", "text"), ("a.json", "json")])
def test_format_for_path(name, fmt):
    assert cli.format_for_path(name) == fmt


def test_bad_extension():
    with pytest.raises(ValueError):
        cli.format_for_path("a.pdf")


def test_get_formats(tmp_path):
    for ext, expect in [("html", "<html>"), ("md", "# Hi"), ("txt", "Hi\nx")]:
        out = tmp_path / ("o." + ext)
        assert cli.main(["get", "https://example.com", str(out)]) == 0
        assert expect in out.read_text()
    out = tmp_path / "o.json"
    cli.main(["get", "https://example.com", str(out)])
    doc = json.loads(out.read_text())
    assert doc["status"] == 200 and doc["content"] == ["Hi\nx"]


def test_get_options_passed(tmp_path):
    out = tmp_path / "o.md"
    cli.main(["get", "https://e.com", str(out), "-H", "A: b", "--cookies", "k=v", "--proxy", "http://p:1",
              "--timeout", "5", "--impersonate", "firefox"])
    assert FakeFetcher.calls[0] == ("get", "https://e.com", {"A": "b"}, {"k": "v"}, "http://p:1", 5.0, "firefox")


def test_css_and_main_content(tmp_path):
    out = tmp_path / "o.html"
    cli.main(["get", "https://e.com", str(out), "--css", "h1"])
    assert out.read_text() == "<h1>Hi</h1>"
    out = tmp_path / "o.md"
    cli.main(["get", "https://e.com", str(out), "--main-content-only"])
    assert out.read_text() == "# Hi (main)"


def test_post_body(tmp_path):
    cli.main(["post", "https://e.com", str(tmp_path / "o.txt"), "--json", '{"a": 1}'])
    assert FakeFetcher.calls[0] == ("post", "https://e.com", None, {"a": 1})


def test_browser_commands(tmp_path):
    out = str(tmp_path / "o.md")
    cli.main(["fetch", "https://e.com", out, "--wait-selector", "#x", "--timeout", "10"])
    assert FakeBrowser.calls[0] == ("https://e.com", "#x", None, 10000.0, False)
    cli.main(["stealthy-fetch", "https://e.com", out, "--solve-cloudflare"])
    assert FakeBrowser.calls[1][4] is True


def test_bad_extension_fails_before_fetch(tmp_path, capsys):
    assert cli.main(["get", "https://e.com", str(tmp_path / "o.pdf")]) == 1
    assert not FakeFetcher.calls
    assert "unsupported" in capsys.readouterr().err


def test_http_error_exit_code(tmp_path, monkeypatch):
    class R(FakeResponse):
        status = 404
    monkeypatch.setattr(FakeFetcher, "get", classmethod(lambda cls, url, **kw: R()))
    assert cli.main(["get", "https://e.com", str(tmp_path / "o.txt")]) == 2


def test_fetch_exception_returns_1(tmp_path, monkeypatch, capsys):
    def boom(cls, url, **kw):
        raise RuntimeError("net down")
    monkeypatch.setattr(FakeFetcher, "get", classmethod(boom))
    assert cli.main(["get", "https://e.com", str(tmp_path / "o.txt")]) == 1
    assert "net down" in capsys.readouterr().err


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_call_filtered_drops_unsupported(capsys):
    def f(a, b=None):
        return (a, b)
    assert cli.call_filtered(f, a=1, b=2, zzz=3, none=None) == (1, 2)
    assert "zzz" in capsys.readouterr().err


def test_install_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: seen.setdefault("cmd", cmd) and 0)
    assert cli.main(["install", "--with-deps"]) == 0
    assert seen["cmd"][-3:] == ["install", "chromium", "--with-deps"]


def _have_parser():
    try:
        from shadowscrape.parser import Selector  # noqa: F401
        Selector("<p>x</p>").to_markdown()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _have_parser(), reason="real parser not importable yet")
def test_extract_integration(tmp_path):
    src = tmp_path / "p.html"
    src.write_text("<html><body><nav>menu</nav><h1>Title</h1><p>Body</p></body></html>")
    out = tmp_path / "o.txt"
    assert cli.main(["extract", str(src), str(out), "--css", "h1"]) == 0
    assert "Title" in out.read_text()
