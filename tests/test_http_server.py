"""Local HTTP fixture shared by the http tests (no network)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: Dict[str, int] = {}
        self.log: List[Dict[str, Any]] = []

    def hit(self, key: str) -> int:
        with self.lock:
            self.hits[key] = self.hits.get(key, 0) + 1
            return self.hits[key]


def make_server():
    state = State()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:  # silence
            pass

        def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8", extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _handle(self) -> None:
            path = self.path.split("?")[0]
            raw = self._body()
            with state.lock:
                state.log.append({"method": self.command, "path": self.path, "headers": dict(self.headers.items()), "body": raw})
            if path == "/":
                self._send(200, b"<html><body><h1 id='t'>Hello</h1><a href='/x'>link</a></body></html>")
            elif path == "/echo":
                data = {"method": self.command, "path": self.path, "headers": dict(self.headers.items()), "body": raw.decode("utf-8", "replace")}
                self._send(200, json.dumps(data).encode(), "application/json")
            elif path == "/setcookie":
                self._send(200, b"ok", extra=[("Set-Cookie", "sid=abc123; Path=/")])
            elif path == "/cookies":
                self._send(200, json.dumps({"cookie": self.headers.get("Cookie", "")}).encode(), "application/json")
            elif path == "/redir":
                self._send(302, b"", extra=[("Location", "/redir2"), ("Set-Cookie", "r=1; Path=/")])
            elif path == "/redir2":
                self._send(301, b"", extra=[("Location", "/echo?final=1")])
            elif path == "/redir303":
                self._send(303, b"", extra=[("Location", "/echo")])
            elif path == "/loop":
                self._send(302, b"", extra=[("Location", "/loop")])
            elif path == "/flaky":
                n = state.hit("flaky")
                if n < 3:
                    self._send(503, b"busy")
                else:
                    self._send(200, b"<p>recovered</p>")
            elif path == "/always503":
                state.hit("always503")
                self._send(503, b"nope", extra=[("Retry-After", "0")])
            elif path == "/latin1":
                self._send(200, "<html><body><p>caf\xe9</p></body></html>".encode("latin-1"), "text/html; charset=iso-8859-1")
            else:
                self._send(404, b"missing")

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, state
