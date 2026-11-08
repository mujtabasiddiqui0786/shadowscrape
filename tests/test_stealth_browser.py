"""Browser-level tests against a local http.server (no network).

Everything here needs an installed chromium (``python -m playwright install chromium``) and is
skipped otherwise.  What is verified: property-level consistency of the fingerprint surfaces, JS
evasion behaviour, worker/iframe propagation, fetcher semantics (XHR capture, sessions, persistence).
What is NOT verified: behaviour against real anti-bot vendors (see benchmarks/ for that).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import types
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from shadowscrape.stealth import build_profile
from shadowscrape.stealth.profiles import detect_host_os


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return os.path.exists(p.chromium.executable_path)
    except Exception:
        return False


pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not _chromium_available(), reason="playwright chromium not installed")]

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082")

PROBE_JS = r"""
(async () => {
const out = {};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const hash = (s) => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return h; };
const tryf = async (f) => { try { return await f(); } catch (e) { return 'ERR:' + e; } };
out.webdriver = navigator.webdriver;
out.ua = navigator.userAgent; out.platform = navigator.platform; out.vendor = navigator.vendor;
out.plugins = navigator.plugins.length; out.mimeTypes = navigator.mimeTypes.length;
out.pluginInstance = await tryf(() => navigator.plugins[0] instanceof Plugin && navigator.plugins.item(0) === navigator.plugins[0] && navigator.plugins.namedItem(navigator.plugins[0].name) === navigator.plugins[0]);
out.chrome = { has: !!window.chrome, loadTimes: typeof (window.chrome || {}).loadTimes, csi: typeof (window.chrome || {}).csi, app: typeof (window.chrome || {}).app };
out.langs = navigator.languages; out.lang = navigator.language;
out.hc = navigator.hardwareConcurrency; out.dm = navigator.deviceMemory;
out.uad = navigator.userAgentData ? { brands: navigator.userAgentData.brands, mobile: navigator.userAgentData.mobile, platform: navigator.userAgentData.platform } : null;
out.he = navigator.userAgentData ? await navigator.userAgentData.getHighEntropyValues(['platformVersion', 'architecture', 'bitness', 'model', 'fullVersionList', 'uaFullVersion', 'wow64']) : null;
out.gl = await tryf(() => { const gl = document.createElement('canvas').getContext('webgl'); const e = gl.getExtension('WEBGL_debug_renderer_info');
  return { vendor: gl.getParameter(e.UNMASKED_VENDOR_WEBGL), renderer: gl.getParameter(e.UNMASKED_RENDERER_WEBGL), maxTex: gl.getParameter(gl.MAX_TEXTURE_SIZE) }; });
out.gl2 = await tryf(() => { const gl = document.createElement('canvas').getContext('webgl2'); const e = gl.getExtension('WEBGL_debug_renderer_info'); return gl.getParameter(e.UNMASKED_RENDERER_WEBGL); });
out.screen = { w: screen.width, h: screen.height, aw: screen.availWidth, ah: screen.availHeight, dpr: devicePixelRatio, cd: screen.colorDepth };
out.win = { iw: innerWidth, ih: innerHeight, ow: outerWidth, oh: outerHeight };
const nat = (fn, name) => { const s = Function.prototype.toString.call(fn); return { ok: s === 'function ' + name + '() { [native code] }' || s === 'function () { [native code] }', s, hasProto: Object.prototype.hasOwnProperty.call(fn, 'prototype') }; };
const g = (o, p) => Object.getOwnPropertyDescriptor(o, p).get;
out.native = {
  webdriver: nat(g(Navigator.prototype, 'webdriver'), 'get webdriver'),
  hc: nat(g(Navigator.prototype, 'hardwareConcurrency'), 'get hardwareConcurrency'),
  languages: nat(g(Navigator.prototype, 'languages'), 'get languages'),
  userAgent: nat(g(Navigator.prototype, 'userAgent'), 'get userAgent'),
  getParameter: nat(WebGLRenderingContext.prototype.getParameter, 'getParameter'),
  getParameter2: nat(WebGL2RenderingContext.prototype.getParameter, 'getParameter'),
  toDataURL: nat(HTMLCanvasElement.prototype.toDataURL, 'toDataURL'),
  getImageData: nat(CanvasRenderingContext2D.prototype.getImageData, 'getImageData'),
  canPlayType: nat(HTMLMediaElement.prototype.canPlayType, 'canPlayType'),
  query: nat(Permissions.prototype.query, 'query'),
  plugins: nat(g(Navigator.prototype, 'plugins'), 'get plugins'),
  toString: nat(Function.prototype.toString, 'toString'),
  toStringOfToString: nat(Function.prototype.toString.toString, 'toString'),
  getDesc: nat(Object.getOwnPropertyDescriptor, 'getOwnPropertyDescriptor'),
  outerHeight: nat(Object.getOwnPropertyDescriptor(window, 'outerHeight').get, 'get outerHeight'),
};
out.nativeLen = { getParameter: WebGLRenderingContext.prototype.getParameter.length, toDataURL: HTMLCanvasElement.prototype.toDataURL.length };
out.iframe = await new Promise((resolve) => {
  const f = document.createElement('iframe'); f.srcdoc = '<p>x</p>';
  const grab = (cw) => ({ wd: cw.navigator.webdriver, ua: cw.navigator.userAgent, plat: cw.navigator.platform, plugins: cw.navigator.plugins.length, chrome: typeof cw.chrome, langs: cw.navigator.languages.join(), hc: cw.navigator.hardwareConcurrency });
  f.onload = () => resolve({ srcdoc: grab(f.contentWindow) });
  document.body.appendChild(f);
});
out.iframeBlank = (() => { const f = document.createElement('iframe'); document.body.appendChild(f); const cw = f.contentWindow;
  return { wd: cw.navigator.webdriver, ua: cw.navigator.userAgent, plat: cw.navigator.platform, plugins: cw.navigator.plugins.length, chrome: typeof cw.chrome }; })();
out.worker = await new Promise((resolve) => {
  const src = `
   let gl = null; try { const c = new OffscreenCanvas(1, 1).getContext('webgl'); const e = c.getExtension('WEBGL_debug_renderer_info'); gl = c.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (e) { gl = 'ERR:' + e; }
   postMessage({ gl, ua: navigator.userAgent, plat: navigator.platform, hc: navigator.hardwareConcurrency, langs: navigator.languages.join(), uadPlat: navigator.userAgentData && navigator.userAgentData.platform,
     uadBrands: navigator.userAgentData && navigator.userAgentData.brands, nat: Function.prototype.toString.call(Object.getOwnPropertyDescriptor(WorkerNavigator.prototype, 'hardwareConcurrency').get) });`;
  const w = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
  w.onmessage = (e) => resolve(e.data); w.onerror = (e) => resolve({ err: String(e.message) });
});
const draw = () => { const c = document.createElement('canvas'); c.width = 220; c.height = 60; const x = c.getContext('2d');
  x.fillStyle = '#f60'; x.fillRect(10, 10, 100, 30); x.fillStyle = '#069'; x.font = '16px Arial'; x.fillText('shadowscrape ☃ canvas', 4, 40); return c; };
out.canvas = { a: hash(draw().toDataURL()), b: hash(draw().toDataURL()), gid: hash(Array.from(draw().getContext('2d').getImageData(0, 0, 220, 60).data).join()), gid2: hash(Array.from(draw().getContext('2d').getImageData(0, 0, 220, 60).data).join()),
  blank: draw().getContext('2d') && (() => { const c = document.createElement('canvas'); c.width = 10; c.height = 10; return c.toDataURL() === document.createElement('canvas').toDataURL() ? 'n/a' : (() => { const d = c.getContext('2d').getImageData(0,0,10,10).data; return Array.from(d).every(v => v === 0); })(); })() };
out.audio = await tryf(async () => { const run = async () => { const ctx = new OfflineAudioContext(1, 4410, 44100); const o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = 1000; o.connect(ctx.destination); o.start(); const b = await ctx.startRendering(); return hash(Array.from(b.getChannelData(0)).slice(0, 2000).join()); }; return { a: await run(), b: await run() }; });
out.perm = await tryf(async () => ({ notif: Notification.permission, query: (await navigator.permissions.query({ name: 'notifications' })).state }));
out.conn = navigator.connection ? { rtt: navigator.connection.rtt, down: navigator.connection.downlink, eff: navigator.connection.effectiveType } : null;
out.codec = { mp4: document.createElement('video').canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"'), ms: window.MediaSource ? MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"') : null };
out.battery = await tryf(async () => { const b = await navigator.getBattery(); return { level: b.level, charging: b.charging }; });
out.media = await tryf(async () => (await navigator.mediaDevices.enumerateDevices()).length);
out.rtc = await tryf(() => new RTCPeerConnection().getConfiguration().iceTransportPolicy);
let detected = false; const e = new Error(); Object.defineProperty(e, 'stack', { get() { detected = true; return ''; } }); console.debug(e); await sleep(150);
out.cdpDetected = detected;
out.hdr = await (await fetch('/echo-headers')).json();
document.getElementById('out').textContent = JSON.stringify(out);
document.title = 'done';
})();
"""


class _Handler(BaseHTTPRequestHandler):
    server: "LocalServer"

    def log_message(self, *a: Any) -> None:
        pass

    def _send(self, body: Any, ctype: str = "text/html; charset=utf-8", status: int = 200, headers: Dict[str, str] = None) -> None:
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        self.server.log.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if path == "/probe.html":
            self._send("<html><head><title>probe</title></head><body><pre id=out></pre><script>%s</script></body></html>" % PROBE_JS,
                       headers={"Accept-CH": "Sec-CH-UA-Platform-Version, Sec-CH-UA-Arch, Sec-CH-UA-Bitness, Sec-CH-UA-Full-Version-List, Sec-CH-UA-Model"})
        elif path == "/w.js":
            self._send("postMessage({ua:navigator.userAgent,plat:navigator.platform,langs:navigator.languages.join(),"
                       "uadPlat:navigator.userAgentData&&navigator.userAgentData.platform,"
                       "uadBrands:navigator.userAgentData&&navigator.userAgentData.brands,"
                       "gl:(()=>{try{const c=new OffscreenCanvas(1,1).getContext('webgl');return c.getParameter(c.getExtension('WEBGL_debug_renderer_info').UNMASKED_RENDERER_WEBGL)}catch(e){return 'ERR'}})()})",
                       "text/javascript")
        elif path == "/worker.html":
            self._send("<html><title>w</title><body><pre id=out></pre><script>const w=new Worker('/w.js');w.onmessage=e=>{"
                       "document.getElementById('out').textContent=JSON.stringify(e.data);document.title='done'}</script></body></html>")
        elif path == "/echo-headers":
            self._send(json.dumps({k.lower(): v for k, v in self.headers.items()}), "application/json")
        elif path == "/api/items":
            self._send(json.dumps({"items": [1, 2, 3]}), "application/json")
        elif path == "/api/other":
            self._send(json.dumps({"other": True}), "application/json")
        elif path == "/xhr.html":
            self._send("<html><body><h1 id=t>xhr</h1><script>fetch('/api/items?x=1').then(r=>r.json());"
                       "const x=new XMLHttpRequest();x.open('GET','/api/other');x.send();</script></body></html>")
        elif path == "/res.html":
            self._send("<html><head><link rel=stylesheet href=/s.css></head><body><h1>res</h1><img src=/img.png></body></html>")
        elif path == "/img.png":
            self._send(PNG, "image/png")
        elif path == "/s.css":
            self._send("h1{color:red}", "text/css")
        elif path == "/setcookie":
            self._send("<html><body><script>localStorage.setItem('k','persisted')</script>set</body></html>",
                       headers={"Set-Cookie": "sid=abc123; Max-Age=3600; Path=/"})
        elif path == "/cookie-echo":
            self._send("<html><body><p id=c>%s</p><p id=ls></p><script>document.getElementById('ls').textContent=localStorage.getItem('k')</script></body></html>"
                       % self.headers.get("Cookie", ""))
        elif path == "/action.html":
            self._send("<html><body><button id=b onclick=\"document.body.insertAdjacentHTML('beforeend','<div id=added>clicked</div>')\">go</button></body></html>")
        elif path == "/redirect":
            self._send("moved", status=302, headers={"Location": "/index.html"})
        elif path == "/missing":
            self._send("<html><body>nope</body></html>", status=404)
        elif path == "/cf":
            self._send("<html><head><title>Just a moment...</title></head><body><div id=m>Checking your browser</div>"
                       "<iframe id=f src='/turnstile-frame' style='position:absolute;left:100px;top:200px;width:300px;height:65px;border:0'></iframe>"
                       "<script>window.addEventListener('message',e=>{if(e.data==='ok'){setTimeout(()=>{document.cookie='cf_clearance=solved; path=/';"
                       "document.title='Welcome';document.body.innerHTML='<h1 id=ok>cleared</h1>';},400)}});</script></body></html>")
        elif path == "/turnstile-frame":
            self._send("<html><body style='margin:0;background:#fafafa'><div id=cb style='position:absolute;left:10px;top:12px;width:40px;height:40px;background:#ddd' "
                       "onclick=\"parent.postMessage('ok','*')\"></div></body></html>")
        else:
            self._send("<html><head><title>index</title></head><body><h1>hello</h1></body></html>")


class LocalServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.log: List[Any] = []

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.server_address[1]


@pytest.fixture(scope="module")
def srv():
    s = LocalServer()
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()


def _run_probe(session: Any, base: str) -> Dict[str, Any]:
    r = session.fetch(base + "/probe.html", wait_selector="title", humanize=False, solve_cloudflare=False,
                      page_action=lambda page: page.wait_for_function("document.title === 'done'", timeout=15000))
    text = r.css_first("#out").get_all_text() if hasattr(r, "css_first") else ""
    return json.loads(text)


# --------------------------------------------------------------------- sessions
@pytest.fixture(scope="module", params=["host", "windows"])
def stealth_probe(request, srv):  # returns (profile, probe, request_headers, info)
    from shadowscrape.fetchers.browser import StealthySession

    os_name = detect_host_os() if request.param == "host" else "windows"
    prof = build_profile(os_name, seed=4242)
    srv.log.clear()
    # NB: only one sync-playwright instance can be alive per thread, so close the session before yielding
    with StealthySession(fingerprint_profile=prof, headless=True) as s:
        probe = _run_probe(s, srv.base)
        hdrs = [h for p, h in srv.log if p == "/probe.html"][0]
        info = types.SimpleNamespace(profile=s.profile, backend_used=s.backend_used)
    return prof, probe, hdrs, info


@pytest.fixture(scope="module")
def plain_probe(srv):
    from shadowscrape.fetchers.browser import DynamicSession

    with DynamicSession(headless=True) as s:
        return _run_probe(s, srv.base)


# ------------------------------------------------------------------------ tests
def test_stealth_navigator_surfaces_match_profile(stealth_probe):
    prof, p, _, s = stealth_probe
    assert p["webdriver"] is False
    assert p["ua"] == prof.user_agent and "Headless" not in p["ua"]
    assert p["platform"] == prof.platform and p["vendor"] == "Google Inc."
    assert p["langs"] == prof.languages and p["lang"] == prof.locale
    assert p["hc"] == prof.hardware_concurrency and p["dm"] == prof.device_memory
    assert p["plugins"] >= 1 and p["mimeTypes"] >= 1 and p["pluginInstance"] is True
    assert p["chrome"] == {"has": True, "loadTimes": "function", "csi": "function", "app": "object"}
    assert p["screen"]["w"] == prof.screen["width"] and p["screen"]["h"] == prof.screen["height"]
    assert p["screen"]["ah"] == prof.screen["availHeight"] and p["screen"]["dpr"] == prof.device_scale_factor
    assert p["win"]["iw"] == prof.viewport["width"] and p["win"]["ih"] == prof.viewport["height"]
    assert p["win"]["oh"] - p["win"]["ih"] == prof.ui_height  # real windows have browser chrome around the viewport
    assert s.profile.chrome_major == prof.chrome_major or s.profile.chrome_major > 0


def test_ua_client_hints_consistent_everywhere(stealth_probe):
    prof, p, hdrs, s = stealth_probe
    final = s.profile  # re-targeted to the real engine version
    md = final.ua_metadata
    assert {(b["brand"], b["version"]) for b in p["uad"]["brands"]} == {(b["brand"], b["version"]) for b in md["brands"]}
    assert p["uad"]["platform"] == md["platform"] and p["uad"]["mobile"] is False
    he = p["he"]
    assert he["platformVersion"] == md["platformVersion"] and he["architecture"] == md["architecture"]
    assert he["bitness"] == "64" and he["model"] == "" and he["wow64"] is False
    assert {(b["brand"], b["version"]) for b in he["fullVersionList"]} == {(b["brand"], b["version"]) for b in md["fullVersionList"]}
    assert he["uaFullVersion"] == md["fullVersion"]
    # the UA string, JS UA-CH and the HTTP request headers all tell the same story
    assert hdrs["user-agent"] == final.user_agent == p["ua"]
    assert hdrs["sec-ch-ua"] == final.sec_ch_ua()
    assert hdrs["sec-ch-ua-platform"] == '"%s"' % md["platform"] and hdrs["sec-ch-ua-mobile"] == "?0"
    assert hdrs["accept-language"].split(",")[0] == final.locale
    assert "HeadlessChrome" not in json.dumps(hdrs)
    echo = p["hdr"]  # subresource after Accept-CH: high-entropy hint headers
    assert echo.get("sec-ch-ua-platform-version") == '"%s"' % md["platformVersion"]
    assert echo.get("sec-ch-ua-arch") == '"%s"' % md["architecture"]


def test_webgl_matches_profile_in_page_and_worker(stealth_probe):
    prof, p, _, _ = stealth_probe
    assert p["gl"]["vendor"] == prof.webgl["vendor"] and p["gl"]["renderer"] == prof.webgl["renderer"]
    assert p["gl2"] == prof.webgl["renderer"]
    assert "SwiftShader" not in json.dumps(p["gl"]) and p["gl"]["maxTex"] == 16384
    w = p["worker"]
    assert "err" not in w
    assert w["gl"] == prof.webgl["renderer"], "WebGL in a Worker must agree with the main thread"
    assert w["ua"] == p["ua"] and w["plat"] == p["platform"] and w["langs"] == ",".join(prof.languages)
    assert w["hc"] == prof.hardware_concurrency
    assert w["uadPlat"] == p["uad"]["platform"] and w["uadBrands"] == p["uad"]["brands"]


def test_patched_functions_look_native(stealth_probe):
    _, p, _, _ = stealth_probe
    bad = {k: v for k, v in p["native"].items() if not v["ok"] or v["hasProto"]}
    assert not bad, bad
    assert p["nativeLen"] == {"getParameter": 1, "toDataURL": 0}  # arity preserved
    assert p["worker"]["nat"] in ("function get hardwareConcurrency() { [native code] }", "function () { [native code] }")


def test_iframes_do_not_leak(stealth_probe):
    prof, p, _, _ = stealth_probe
    for key in ("srcdoc", "iframe"):
        f = p["iframe"]["srcdoc"] if key == "srcdoc" else p["iframeBlank"]
        assert f["wd"] is False, key
        assert f["ua"] == prof.user_agent and f["plat"] == prof.platform
        assert f["plugins"] >= 1 and f["chrome"] == "object"
    assert p["iframe"]["srcdoc"]["langs"] == ",".join(prof.languages)


def test_canvas_audio_noise_is_deterministic_within_session(stealth_probe):
    _, p, _, _ = stealth_probe
    c = p["canvas"]
    assert c["a"] == c["b"] and c["gid"] == c["gid2"]
    assert isinstance(p["audio"], dict) and p["audio"]["a"] == p["audio"]["b"]


def test_misc_surfaces(stealth_probe):
    prof, p, _, _ = stealth_probe
    assert p["perm"] == {"notif": "default", "query": "prompt"}  # classic headless inconsistency fixed
    assert p["conn"]["rtt"] == prof.connection["rtt"] and p["conn"]["eff"] == "4g"
    assert p["codec"] == {"mp4": "probably", "ms": True}
    assert p["battery"]["level"] == 1 and p["battery"]["charging"] is True
    assert p["media"] == 3
    assert p["rtc"] == "relay"  # WebRTC cannot gather host/srflx candidates


def test_runtime_enable_not_detectable_on_stealth_backend(stealth_probe):
    _, p, _, s = stealth_probe
    assert s.backend_used in ("patchright", "playwright")
    assert p["cdpDetected"] is False


def test_http_served_worker_inherits_ua_and_client_hints(srv):
    """Same-origin http workers: UA/UA-CH come from the CDP override; WebGL patch there is best-effort (racy)."""
    from shadowscrape.fetchers.browser import StealthySession

    prof = build_profile(detect_host_os(), seed=11)
    with StealthySession(fingerprint_profile=prof) as s:
        r = s.fetch(srv.base + "/worker.html", humanize=False, solve_cloudflare=False,
                    page_action=lambda page: page.wait_for_function("document.title === 'done'", timeout=15000))
        w = json.loads(r.css_first("#out").get_all_text())
        final = s.profile
    assert w["ua"] == final.user_agent and w["plat"] == final.platform and w["langs"] == ",".join(final.languages)
    assert w["uadPlat"] == final.ua_metadata["platform"]
    assert {b["brand"] for b in w["uadBrands"]} == {b["brand"] for b in final.ua_metadata["brands"]}
    print("http worker webgl patched:", w["gl"] == final.webgl["renderer"])


def test_canvas_differs_across_seeds(srv):
    from shadowscrape.fetchers.browser import StealthySession

    hashes = []
    for seed in (1, 2):
        with StealthySession(fingerprint_profile=build_profile(detect_host_os(), seed=seed)) as s:
            hashes.append(_run_probe(s, srv.base)["canvas"]["a"])
    assert hashes[0] != hashes[1]


def test_plain_playwright_baseline_is_detectable_and_stealth_is_not(plain_probe, stealth_probe):
    """The comparison the benchmark generalises: count local detector failures."""
    _, s, _, sess = stealth_probe

    def fails(p: Dict[str, Any]) -> List[str]:
        out = []
        if p["webdriver"] is not False: out.append("webdriver")
        if "Headless" in p["ua"]: out.append("headless-ua")
        if p["plugins"] == 0: out.append("no-plugins")
        if not p["chrome"]["has"]: out.append("no-window.chrome")
        if p["uad"] is None or any("Headless" in b["brand"] for b in p["uad"]["brands"]): out.append("uach-headless")
        if p["win"]["ow"] == p["win"]["iw"] and p["win"]["oh"] == p["win"]["ih"]: out.append("outer==inner")
        if p["perm"] != {"notif": "default", "query": "prompt"}: out.append("permissions-inconsistent")
        if p["conn"] and p["conn"]["rtt"] == 0: out.append("rtt0")
        if "SwiftShader" in json.dumps(p["gl"]): out.append("swiftshader")
        if p["media"] == 0: out.append("no-media-devices")
        if p["worker"].get("gl") != p["gl"]["renderer"]: out.append("worker-webgl-mismatch")
        if "uadBrands" in p["worker"] and p["worker"]["uadBrands"] and any("Headless" in b["brand"] for b in p["worker"]["uadBrands"]): out.append("worker-uach-headless")
        return out

    plain_f, stealth_f = fails(plain_probe), fails(s)
    print("plain playwright failures:", plain_f)
    print("stealth failures:", stealth_f)
    assert len(plain_f) >= 4
    assert stealth_f == []


# ------------------------------------------------------------------- fetcher API
def test_fetch_response_xhr_capture_and_history(srv):
    from shadowscrape.fetchers.browser import StealthyFetcher

    r = StealthyFetcher.fetch(srv.base + "/xhr.html", capture_xhr=r"/api/items", humanize=False, network_idle=True)
    assert r.status == 200 and "xhr" in r.text
    assert len(r.captured_xhr) == 1
    c = r.captured_xhr[0]
    assert c["url"].endswith("/api/items?x=1") and c["method"] == "GET" and c["status"] == 200
    assert json.loads(c["body"]) == {"items": [1, 2, 3]}
    assert r.css_first("#t").text == "xhr"
    r2 = StealthyFetcher.fetch(srv.base + "/xhr.html", capture_xhr=re.compile(r"/api/"), humanize=False, network_idle=True)
    assert sorted(x["url"].rsplit("/", 1)[1].split("?")[0] for x in r2.captured_xhr) == ["items", "other"]
    r3 = StealthyFetcher.fetch(srv.base + "/redirect", humanize=False)
    assert r3.status == 200 and r3.url.endswith("/index.html") and r3.history and r3.history[0].status == 302
    r4 = StealthyFetcher.fetch(srv.base + "/missing", humanize=False)
    assert r4.status == 404


def test_disable_resources_and_wait_and_action(srv):
    from shadowscrape.fetchers.browser import StealthySession

    with StealthySession() as s:
        srv.log.clear()
        s.fetch(srv.base + "/res.html", humanize=False, disable_resources=True)
        paths = [p for p, _ in srv.log]
        assert "/img.png" not in paths and "/s.css" not in paths
        srv.log.clear()
        s.fetch(srv.base + "/res.html", humanize=False, disable_resources=False)
        assert "/img.png" in [p for p, _ in srv.log]
        r = s.fetch(srv.base + "/action.html", humanize=False, wait_selector="#added",
                    page_action=lambda page: page.click("#b"))
        assert r.css_first("#added").text == "clicked"
        r = s.fetch(srv.base + "/action.html", humanize=True, solve_cloudflare=False, wait=50)  # humanize path runs without error
        assert r.status == 200


def test_session_reuses_context_and_cookies(srv):
    from shadowscrape.fetchers.browser import StealthySession

    with StealthySession() as s:
        ctx1 = s._ctx
        s.fetch(srv.base + "/setcookie", humanize=False)
        r = s.fetch(srv.base + "/cookie-echo", humanize=False)
        assert s._ctx is ctx1
        assert "sid=abc123" in r.css_first("#c").text and r.css_first("#ls").text == "persisted"
        assert r.cookies.get("sid") == "abc123"
        # changing a launch-level option restarts the browser (fresh cookies)
        r = s.fetch(srv.base + "/cookie-echo", humanize=False, block_webrtc=False)
        assert s._ctx is not ctx1 and "sid=abc123" not in r.css_first("#c").text


def test_persistent_user_data_dir_survives_restart(srv):
    from shadowscrape.fetchers.browser import StealthySession

    d = tempfile.mkdtemp(prefix="ss-persist-")
    with StealthySession(user_data_dir=d) as s:
        s.fetch(srv.base + "/setcookie", humanize=False)
    with StealthySession(user_data_dir=d) as s:
        r = s.fetch(srv.base + "/cookie-echo", humanize=False)
    assert "sid=abc123" in r.css_first("#c").text and r.css_first("#ls").text == "persisted"


def test_async_stealthy_session(srv):
    from shadowscrape.fetchers.browser import AsyncStealthySession

    async def go():
        async with AsyncStealthySession(max_pages=3) as s:
            async def act(page):
                await page.click("#b")

            r, x, *_ = await asyncio.gather(
                s.fetch(srv.base + "/action.html", humanize=False, wait_selector="#added", page_action=act),
                s.fetch(srv.base + "/xhr.html", humanize=False, capture_xhr="/api/items", network_idle=True),
                s.fetch(srv.base + "/index.html", humanize=False))
            return r, x, s

    r, x, s = asyncio.run(go())
    assert r.css_first("#added").text == "clicked"
    assert json.loads(x.captured_xhr[0]["body"]) == {"items": [1, 2, 3]}
    assert s.profile is not None


def test_async_stealth_matches_sync_surfaces(srv):
    """Worker/UA-CH propagation also works on the async API path."""
    from shadowscrape.fetchers.browser import AsyncStealthySession

    prof = build_profile(detect_host_os(), seed=99)

    async def go():
        async with AsyncStealthySession(fingerprint_profile=prof) as s:
            r = await s.fetch(srv.base + "/probe.html", humanize=False, solve_cloudflare=False,
                              page_action=lambda page: page.wait_for_function("document.title === 'done'", timeout=15000))
            return json.loads(r.css_first("#out").get_all_text()), s.profile

    p, final = asyncio.run(go())
    assert p["webdriver"] is False and p["ua"] == final.user_agent
    assert p["worker"]["gl"] == prof.webgl["renderer"] and p["worker"]["uadPlat"] == final.ua_metadata["platform"]
    assert p["iframeBlank"]["wd"] is False


def test_dynamic_fetcher_plain_playwright(srv):
    from shadowscrape.fetchers.browser import DynamicFetcher

    r = DynamicFetcher.fetch(srv.base + "/xhr.html", capture_xhr="/api/items", network_idle=True, wait_selector="#t")
    assert r.status == 200 and len(r.captured_xhr) == 1
    r = asyncio.run(DynamicFetcher.async_fetch(srv.base + "/index.html", disable_resources=True))
    assert "hello" in r.text


def test_timeout_seconds_shorthand():
    from shadowscrape.fetchers.browser import _ms

    assert _ms(30) == 30000 and _ms(30000) == 30000 and _ms(None) == 30000


def test_cloudflare_style_interaction_mechanics(srv):
    """Mechanics only: detect interstitial -> humanized click on checkbox frame -> clearance observed.

    The 'challenge' is a local mock; this proves the geometry/behaviour/wait loop, NOT bypass of
    real Cloudflare.
    """
    from shadowscrape.fetchers.browser import StealthySession

    with StealthySession() as s:
        r = s.fetch(srv.base + "/cf", humanize=False, solve_cloudflare=True, timeout=40000)
        res = s.last_challenge
    assert res["detected"] and res["kind"] == "interstitial"
    assert res["solved"] is True and res["method"] == "click" and res["attempts"] >= 1
    assert r.css_first("#ok").text == "cleared" and r.cookies.get("cf_clearance") == "solved"


def test_blocked_page_reports_unsolvable(srv):
    from shadowscrape.stealth.turnstile import solve_sync
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content("<html><title>Attention Required! | Cloudflare</title><h1>Sorry, you have been blocked</h1>")
        res = solve_sync(pg, timeout_ms=3000)
        b.close()
    assert res["kind"] == "blocked" and res["solved"] is False


def test_fallback_backend_plain_playwright(srv):
    """Without patchright the same evasions apply; Runtime.enable stays (documented limit) but the console guard hides the common probe."""
    from shadowscrape.fetchers.browser import StealthySession

    prof = build_profile(detect_host_os(), seed=77)
    with StealthySession(fingerprint_profile=prof, backend="playwright") as s:
        p = _run_probe(s, srv.base)
        assert s.backend_used == "playwright"
        final = s.profile
    assert p["webdriver"] is False and p["ua"] == final.user_agent and p["plugins"] >= 1
    assert p["gl"]["renderer"] == prof.webgl["renderer"] and p["worker"]["gl"] == prof.webgl["renderer"]
    assert all(v["ok"] for v in p["native"].values())
    assert p["iframeBlank"]["wd"] is False
    assert p["cdpDetected"] is False  # console-serialisation guard (partial mitigation only)


def test_missing_chromium_gives_actionable_error(monkeypatch):
    from shadowscrape.fetchers import browser as B

    err = B._wrap_launch_error(Exception("BrowserType.launch: Executable doesn't exist at /x"))
    assert isinstance(err, RuntimeError) and "playwright install chromium" in str(err)
