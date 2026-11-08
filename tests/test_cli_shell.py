from __future__ import annotations

import pytest

from shadowscrape import cli, shell
from shadowscrape.shell import parse_curl


def test_simple_get():
    r = parse_curl("curl 'https://example.com/a?b=1'")
    assert r["method"] == "GET" and r["url"] == "https://example.com/a?b=1"
    assert r["headers"] == {} and r["cookies"] == {} and "data" not in r


def test_chrome_style_multiline():
    cmd = """curl 'https://api.example.com/x' \\
  -H 'accept: application/json' \\
  -H 'cookie: a=1; b=2' \\
  -H 'user-agent: Mozilla/5.0' \\
  --compressed"""
    r = parse_curl(cmd)
    assert r["headers"] == {"accept": "application/json", "user-agent": "Mozilla/5.0"}
    assert r["cookies"] == {"a": "1", "b": "2"}


def test_post_data_implies_post():
    r = parse_curl("curl https://e.com/login -d 'user=a&pw=b'")
    assert r["method"] == "POST" and r["data"] == "user=a&pw=b"


def test_json_body():
    r = parse_curl("""curl -X PUT https://e.com -H 'Content-Type: application/json' --data-raw '{"a": [1, 2]}'""")
    assert r["method"] == "PUT" and r["json"] == {"a": [1, 2]} and "data" not in r


def test_cookie_flag_user_agent_proxy_auth():
    r = parse_curl("curl -b 'x=1; y=2' -A 'UA/1' -x http://p:8 -u me:secret https://e.com")
    assert r["cookies"] == {"x": "1", "y": "2"}
    assert r["headers"]["User-Agent"] == "UA/1"
    assert r["proxy"] == "http://p:8" and r["auth"] == ("me", "secret")


def test_ansi_c_quoting():
    r = parse_curl("curl 'https://e.com' --data-raw $'a=1\\u0026b=2'")
    assert r["data"] == "a=1&b=2"


def test_get_flag_moves_data_to_query():
    r = parse_curl("curl -G https://e.com/s -d q=1")
    assert r["method"] == "GET" and r["url"] == "https://e.com/s?q=1"


def test_head_and_long_equals_form():
    r = parse_curl("curl -I --header=X-A:1 https://e.com")
    assert r["method"] == "HEAD" and r["headers"] == {"X-A": "1"}


def test_missing_url():
    with pytest.raises(ValueError):
        parse_curl("curl -H 'a: b'")


def test_curl2fetcher_executes():
    seen = {}

    class F:
        @classmethod
        def post(cls, url, *, headers=None, cookies=None, data=None, json=None):
            seen.update(url=url, headers=headers, cookies=cookies, data=data)
            return "resp"

    out = shell.curl2fetcher("curl https://e.com -H 'k: v' -b a=1 -d x=1", _fetcher=F)
    assert out == "resp"
    assert seen == {"url": "https://e.com", "headers": {"k": "v"}, "cookies": {"a": "1"}, "data": "x=1"}


def test_curl2fetcher_no_execute():
    assert shell.curl2fetcher("curl https://e.com", execute=False)["method"] == "GET"


def test_namespace_tracks_page(monkeypatch):
    class R:
        status = 200

        def css(self, s):
            return []

    class F:
        @classmethod
        def get(cls, url):
            return R()

    monkeypatch.setattr(cli, "load_fetcher", lambda name: F)
    ns = shell.build_namespace()
    assert ns["page"] is None
    res = ns["Fetcher"].get("https://e.com")
    assert ns["page"] is res
    assert callable(ns["view"]) and callable(ns["curl2fetcher"])


def test_view_writes_file(monkeypatch):
    opened = []
    monkeypatch.setattr(shell.webbrowser, "open", opened.append)

    class P:
        text = "<html><head></head><body>x</body></html>"
        url = "https://e.com/"

    path = shell.view(P())
    assert opened == ["file://" + path]
    assert '<base href="https://e.com/">' in open(path).read()
    with pytest.raises(ValueError):
        shell.view(None)


def test_shell_code_flag(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_fetcher", lambda name: type(name, (), {}))
    assert shell.start_shell(code="print('ok', 'Selector' in globals())") == 0
    assert capsys.readouterr().out.startswith("ok")
