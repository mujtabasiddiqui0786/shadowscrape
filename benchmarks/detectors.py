"""Reproducible detector benchmark: plain Playwright vs shadowscrape Stealthy.

Usage::

    python -m benchmarks.detectors                       # all engines x all targets, 1 run each
    python -m benchmarks.detectors --repeat 5 --engines playwright stealthy
    python -m benchmarks.detectors --targets local-probe # fully offline sanity target
    python -m benchmarks.detectors --list

Outputs ``benchmarks/results/<UTC timestamp>.json`` + ``.md`` and copies them to ``latest.json`` / ``latest.md``.

Honesty notes (also in the report): verdict parsers are *heuristics over third-party pages that change*;
a ``pass`` means "this page did not flag us", not "we beat a commercial anti-bot".  Without network all
public targets are recorded as ``skipped`` and only ``local-probe`` runs.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import platform
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")


# ------------------------------------------------------------------ verdict parsers (pure; unit-tested)
def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_sannysoft(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    """bot.sannysoft.com: count table cells with class passed/failed/warn."""
    cells = re.findall(r'class="([^"]*)"', html)
    cnt = {k: sum(1 for c in cells if re.search(r"\b%s\b" % k, c)) for k in ("passed", "failed", "warn")}
    failed_rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        if re.search(r'class="[^"]*\bfailed\b', row):
            failed_rows.append(_strip_tags(row)[:120])
    if not any(cnt.values()):
        return "unknown", {"note": "no result cells found (page changed or blocked)"}
    return ("pass" if cnt["failed"] == 0 else "fail"), {**cnt, "failed_rows": failed_rows}


def parse_areyouheadless(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    t = text.lower()
    if "you are not chrome headless" in t:
        return "pass", {"text": "You are not Chrome headless"}
    if "you are chrome headless" in t:
        return "fail", {"text": "You are Chrome headless"}
    return "unknown", {"snippet": text[:160]}


def parse_browserscan(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    m = re.search(r"Test Results:?\s*([A-Za-z]+)", text)
    if m:
        v = m.group(1).lower()
        return ("pass" if v == "normal" else "fail"), {"test_results": m.group(1)}
    return "unknown", {"snippet": text[:160]}


def parse_creepjs(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    text = _strip_tags(html)  # engine text extraction differs (char-level spans); use one normalisation
    det: Dict[str, Any] = {}
    m = re.search(r"trust score:?\s*([\d.]+)\s*%", text, re.I)
    if m:
        det["trust_score"] = float(m.group(1))
    for key, pat in (("headless_pct", r"(\d+)%\s*headless"), ("like_headless_pct", r"(\d+)%\s*like headless"),
                     ("stealth_pct", r"(\d+)%\s*stealth")):
        mm = re.search(pat, text, re.I)
        if mm:
            det[key] = int(mm.group(1))
    for key, pat in (("webdriver_is_on", r"webDriverIsOn:\s*(true|false)"), ("to_string_proxy", r"hasToStringProxy:\s*(true|false)"),
                     ("bad_webgl", r"hasBadWebGL:\s*(true|false)")):
        mm = re.search(pat, text)
        if mm:
            det[key] = mm.group(1) == "true"
    if not det:
        return "unknown", {"snippet": text[:160]}
    # like_headless_pct is informational: it includes features absent on *some real* desktop Chromes and the
    # ActiveText-colour tell of new-headless; headless/stealth ratings and the lie flags decide the verdict.
    bad = any(det.get(k, 0) > 0 for k in ("headless_pct", "stealth_pct")) or any(
        det.get(k) for k in ("webdriver_is_on", "to_string_proxy", "bad_webgl"))
    return ("fail" if bad else "pass"), det


def parse_tls(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    try:
        raw = text if text.strip().startswith("{") else re.search(r"\{.*\}", text, re.S).group(0)  # type: ignore[union-attr]
        d = json.loads(raw)
    except Exception:
        return "unknown", {"snippet": text[:160]}
    tls = d.get("tls", {}) or {}
    h2 = d.get("http2", {}) or {}
    return "info", {"http_version": d.get("http_version"), "ja3_hash": tls.get("ja3_hash"), "ja4": tls.get("ja4"),
                    "akamai_hash": h2.get("akamai_fingerprint_hash"), "user_agent": d.get("user_agent")}


def parse_nowsecure(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    t = (text + " " + html[:2000]).lower()
    if any(m in t for m in ("just a moment", "checking your browser", "verify you are human", "cf-chl")):
        return "fail", {"note": "Cloudflare challenge page served"}
    if "nowsecure" in t:
        return "pass", {"note": "target content rendered, no challenge markers"}
    return "unknown", {"snippet": text[:160]}


def parse_local(html: str, text: str) -> Tuple[str, Dict[str, Any]]:
    m = re.search(r'<pre id="out">(.*?)</pre>', html, re.S)
    if not m:
        return "unknown", {"note": "probe produced no output"}
    import html as _h

    d = json.loads(_h.unescape(m.group(1)))
    return ("pass" if not d["fails"] else "fail"), {"fails": d["fails"], "checks": d["n"]}


LOCAL_PROBE_HTML = """<!doctype html><html><head><title>probe</title></head><body><pre id="out"></pre><script>
(async()=>{const f=[];let n=0;const chk=(c,name)=>{n++;if(!c)f.push(name)};
chk(navigator.webdriver===false,'navigator.webdriver');chk(!/Headless/.test(navigator.userAgent),'headless-ua');
chk(navigator.plugins.length>0,'plugins');chk(!!window.chrome&&!!chrome.app,'window.chrome');
chk(navigator.userAgentData&&!navigator.userAgentData.brands.some(b=>/Headless/.test(b.brand)),'uach-headless');
chk(outerHeight-innerHeight>20,'outer==inner');
try{const q=(await navigator.permissions.query({name:'notifications'})).state;chk(!(Notification.permission==='denied'&&q==='prompt'),'permissions')}catch(e){}
chk(!navigator.connection||navigator.connection.rtt>0,'rtt0');
const gl=document.createElement('canvas').getContext('webgl');if(gl){const e=gl.getExtension('WEBGL_debug_renderer_info');chk(!/SwiftShader|llvmpipe/i.test(gl.getParameter(e.UNMASKED_RENDERER_WEBGL)),'swiftshader')}
chk((await navigator.mediaDevices.enumerateDevices()).length>0,'media-devices');
const w=await new Promise(r=>{const wk=new Worker(URL.createObjectURL(new Blob(['postMessage({ua:navigator.userAgent,gl:(()=>{try{const c=new OffscreenCanvas(1,1).getContext("webgl");return c.getParameter(c.getExtension("WEBGL_debug_renderer_info").UNMASKED_RENDERER_WEBGL)}catch(e){return ""}})()})'],{type:'text/javascript'})));wk.onmessage=e=>r(e.data)});
chk(w.ua===navigator.userAgent,'worker-ua');
if(gl){const e=gl.getExtension('WEBGL_debug_renderer_info');chk(w.gl===gl.getParameter(e.UNMASKED_RENDERER_WEBGL),'worker-webgl')}
document.getElementById('out').textContent=JSON.stringify({fails:f,n:n});document.title='done'})();
</script></body></html>"""


class Target:
    def __init__(self, name: str, url: str, parser: Callable[[str, str], Tuple[str, Dict[str, Any]]], wait_ms: int,
                 needs_network: bool = True, note: str = "") -> None:
        self.name, self.url, self.parser, self.wait_ms, self.needs_network, self.note = name, url, parser, wait_ms, needs_network, note


TARGETS: List[Target] = [
    Target("local-probe", "@local", parse_local, 500, needs_network=False, note="offline self-test page served from this process"),
    Target("sannysoft", "https://bot.sannysoft.com/", parse_sannysoft, 3000),
    Target("creepjs", "https://abrahamjuliot.github.io/creepjs/", parse_creepjs, 12000),
    Target("browserscan", "https://www.browserscan.net/bot-detection", parse_browserscan, 8000),
    Target("areyouheadless", "https://arh.antoinevastel.com/bots/areyouheadless", parse_areyouheadless, 3000),
    Target("tls-peet", "https://tls.peet.ws/api/all", parse_tls, 500),
    Target("nowsecure", "https://nowsecure.nl/", parse_nowsecure, 8000),
]


# ------------------------------------------------------------------------------------- environment
def have_network(timeout: float = 4.0) -> bool:
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection(host, timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def _version(mod: str) -> Optional[str]:
    try:
        from importlib import metadata

        return metadata.version(mod)
    except Exception:
        return None


def environment() -> Dict[str, Any]:
    return {"python": platform.python_version(), "os": "%s %s %s" % (platform.system(), platform.release(), platform.machine()),
            "playwright": _version("playwright"), "patchright": _version("patchright"),
            "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}


class _LocalServer:
    def __init__(self) -> None:
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                body = LOCAL_PROBE_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


# --------------------------------------------------------------------------------------- engines
Fetched = Dict[str, Any]  # {html, text, status, final_url}


def _wait_for_local(page: Any) -> None:
    page.wait_for_function("document.title === 'done'", timeout=15000)


def engine_playwright(url: str, wait_ms: int, headless: bool, timeout_ms: int, seed: Optional[int]) -> Fetched:
    """Plain Playwright Chromium, default launch options, no patches (the baseline)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        try:
            pg = b.new_page()
            resp = pg.goto(url, wait_until="load", timeout=timeout_ms)
            if url.startswith("http://127.0.0.1"):
                _wait_for_local(pg)
            pg.wait_for_timeout(wait_ms)
            return {"html": pg.content(), "text": pg.inner_text("body"), "status": resp.status if resp else None, "final_url": pg.url}
        finally:
            b.close()


def engine_stealthy(url: str, wait_ms: int, headless: bool, timeout_ms: int, seed: Optional[int]) -> Fetched:
    """shadowscrape StealthySession (profile = host OS, seeded for reproducibility)."""
    from shadowscrape.fetchers.browser import StealthySession
    from shadowscrape.stealth.profiles import host_profile

    local = url.startswith("http://127.0.0.1")
    with StealthySession(headless=headless, fingerprint_profile=host_profile(seed=seed)) as s:
        r = s.fetch(url, wait=wait_ms, timeout=timeout_ms, solve_cloudflare=True, humanize=not local,
                    page_action=(_wait_for_local if local else None))
        return {"html": r.text, "text": r.get_all_text(), "status": r.status, "final_url": r.url,
                "extra": {"backend": s.backend_used, "challenge": getattr(s, "last_challenge", None)}}


ENGINES: Dict[str, Dict[str, Any]] = {
    "playwright": {"fn": engine_playwright, "label": "plain Playwright"},
    "stealthy": {"fn": engine_stealthy, "label": "shadowscrape Stealthy"},
}


def run(engines: List[str], targets: List[str], repeat: int = 1, headless: bool = True, timeout_ms: int = 45000,
        out_dir: str = RESULTS_DIR, force_offline: bool = False, seed: int = 1337, verbose: bool = True) -> Dict[str, Any]:
    net = False if force_offline else have_network()
    tg = [t for t in TARGETS if t.name in targets]
    results: List[Dict[str, Any]] = []
    local_srv: Optional[_LocalServer] = None
    if any(t.name == "local-probe" for t in tg):
        local_srv = _LocalServer()
    try:
        for ename in engines:
            spec = ENGINES[ename]
            missing = None
            if spec.get("optional_module"):
                try:
                    importlib.import_module(spec["optional_module"])
                except Exception as e:
                    missing = "%s not installed (%s); skipped" % (spec["optional_module"], type(e).__name__)
            for t in tg:
                for i in range(repeat):
                    rec: Dict[str, Any] = {"engine": ename, "target": t.name, "run": i, "verdict": "skipped"}
                    if missing:
                        rec["note"] = missing
                    elif t.needs_network and not net:
                        rec["note"] = "no network: public detector pages skipped"
                    else:
                        url = local_srv.url if t.url == "@local" and local_srv else t.url
                        t0 = time.time()
                        try:
                            try:
                                f = spec["fn"](url, t.wait_ms, headless, timeout_ms, seed + i)
                            except Exception as first:  # transient network errors (QUIC/H2 resets): retry once
                                if "net::ERR_" not in str(first):
                                    raise
                                rec["retried"] = str(first).splitlines()[0][:120]
                                f = spec["fn"](url, t.wait_ms, headless, timeout_ms, seed + i)
                            verdict, details = t.parser(f["html"], f["text"])
                            rec.update(verdict=verdict, details=details, http_status=f.get("status"), final_url=f.get("final_url"),
                                       extra=f.get("extra"))
                        except Exception as e:
                            rec.update(verdict="error", note="%s: %s" % (type(e).__name__, str(e)[:300]))
                        rec["elapsed_s"] = round(time.time() - t0, 2)
                    results.append(rec)
                    if verbose:
                        print("%-11s %-15s run%d -> %-8s %s" % (ename, t.name, i, rec["verdict"], rec.get("note", rec.get("details", ""))
                              if rec["verdict"] != "pass" else ""), flush=True)
    finally:
        if local_srv:
            local_srv.close()
    report = {"environment": environment(), "network": net, "repeat": repeat, "headless": headless, "seed": seed,
              "engines": engines, "targets": [t.name for t in tg], "results": results}
    write_reports(report, out_dir)
    return report


# ---------------------------------------------------------------------------------------- reports
def summarise(report: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    """(engine, target) -> compact cell, e.g. ``pass 3/3`` / ``fail 0/3`` / ``info`` / ``skipped``."""
    cells: Dict[Tuple[str, str], str] = {}
    for e in report["engines"]:
        for t in report["targets"]:
            rs = [r for r in report["results"] if r["engine"] == e and r["target"] == t]
            verdicts = [r["verdict"] for r in rs]
            if not verdicts or all(v == "skipped" for v in verdicts):
                cells[(e, t)] = "skipped"
            elif "info" in verdicts:
                cells[(e, t)] = "info"
            else:
                p = verdicts.count("pass")
                ran = [v for v in verdicts if v != "skipped"]
                dom = max(set(ran), key=ran.count)
                cells[(e, t)] = "%s %d/%d" % (dom if dom != "pass" else "pass", p, len(ran)) if dom != "error" else "error"
    return cells


def to_markdown(report: Dict[str, Any]) -> str:
    env = report["environment"]
    L = ["# Detector benchmark", "",
         "Generated %s | python %s | %s | playwright %s | patchright %s" % (
             env["timestamp_utc"], env["python"], env["os"], env["playwright"], env["patchright"]),
         "", "Network available: **%s** | runs per cell: %d | headless: %s" % (report["network"], report["repeat"], report["headless"]), ""]
    if not report["network"]:
        L += ["> No network: public detector targets were skipped; only the offline `local-probe` ran.", ""]
    cells = summarise(report)
    L.append("| target | " + " | ".join(ENGINES[e]["label"] for e in report["engines"]) + " |")
    L.append("|---|" + "---|" * len(report["engines"]))
    for t in report["targets"]:
        L.append("| %s | " % t + " | ".join(cells[(e, t)] for e in report["engines"]) + " |")
    L += ["", "`pass n/m` = detector did not flag the client in n of m runs. `info` = informational target (fingerprints below).", "",
          "## Details", ""]
    for r in report["results"]:
        if r["verdict"] in ("skipped",) and r.get("note", "").startswith("no network"):
            continue
        d = r.get("details") or r.get("note") or ""
        L.append("- **%s / %s / run %d**: %s (%.1fs) `%s`" % (r["engine"], r["target"], r["run"], r["verdict"], r.get("elapsed_s", 0),
                                                              json.dumps(d, default=str)[:400]))
    L += ["", "## Caveats", "",
          "- Parsers are heuristics over third-party pages and can break when those pages change; check `details`.",
          "- Passing public detector pages is **not** evidence of bypassing Cloudflare/Akamai/DataDome/PerimeterX/Kasada; those also use IP reputation, TLS/HTTP2 fingerprints, and behavioural/ML scoring.",
          "- Browser engines share Chromium's real TLS fingerprint (`tls-peet`); stealth patches do not alter it.",
          "- Results depend on your IP and machine (GPU, fonts, OS). Run on the machine you intend to scrape from.", ""]
    return "\n".join(L)


def write_reports(report: Dict[str, Any], out_dir: str) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = report["environment"]["timestamp_utc"].replace(":", "").replace("-", "")
    jp, mp = os.path.join(out_dir, "%s.json" % stamp), os.path.join(out_dir, "%s.md" % stamp)
    with open(jp, "w") as f:
        json.dump(report, f, indent=2, default=str)
    md = to_markdown(report)
    with open(mp, "w") as f:
        f.write(md)
    for src, name in ((jp, "latest.json"), (mp, "latest.md")):
        with open(os.path.join(out_dir, name), "w") as f, open(src) as s:
            f.write(s.read())
    return jp, mp


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engines", nargs="+", default=list(ENGINES), choices=list(ENGINES))
    ap.add_argument("--targets", nargs="+", default=[t.name for t in TARGETS], choices=[t.name for t in TARGETS])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--headful", action="store_true", help="run with a visible window (real headful mode)")
    ap.add_argument("--timeout", type=int, default=45000, help="per-page timeout, ms")
    ap.add_argument("--out", default=RESULTS_DIR)
    ap.add_argument("--offline", action="store_true", help="skip network targets (also auto-detected)")
    ap.add_argument("--seed", type=int, default=1337, help="base seed for the stealth fingerprint profile")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for t in TARGETS:
            print("%-15s %s" % (t.name, t.url))
        return 0
    rep = run(a.engines, a.targets, a.repeat, not a.headful, a.timeout, a.out, a.offline, a.seed)
    print("\n" + to_markdown(rep).split("## Details")[0])
    print("reports written to", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
