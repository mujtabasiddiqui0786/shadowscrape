"""Offline, browser-free tests for profiles, humanize planning, challenge detection and script rendering."""
from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import tempfile
import os

import pytest

from shadowscrape.stealth import (build_init_script, build_profile, cdp_user_agent_override, check_consistency,
                                  detect_challenge, host_profile, resolve_profile)
from shadowscrape.stealth.humanize import bezier_path, scroll_plan, typing_delays
from shadowscrape.stealth.launch import (HARDENED_ARGS, IGNORE_DEFAULT_ARGS, StealthConfig, _Propagator,
                                         build_context_options, normalize_proxy)
from shadowscrape.stealth.turnstile import checkbox_point


@pytest.mark.parametrize("os_name", ["macos", "windows", "linux"])
@pytest.mark.parametrize("seed", range(12))
def test_profiles_are_internally_consistent(os_name, seed):
    p = build_profile(os_name, seed=seed)
    assert check_consistency(p) == []
    assert "Headless" not in p.user_agent
    brands = {b["brand"]: b["version"] for b in p.ua_metadata["brands"]}
    assert brands["Google Chrome"] == brands["Chromium"] == str(p.chrome_major)
    assert p.viewport["height"] + p.ui_height <= p.screen["availHeight"]
    assert p.device_memory <= 8
    assert p.sec_ch_ua().count('"') == 12


def test_profile_determinism_and_variation():
    a, b, c = build_profile("windows", seed=1), build_profile("windows", seed=1), build_profile("windows", seed=2)
    assert a.to_dict() == b.to_dict()
    assert any(a.to_dict()[k] != c.to_dict()[k] for k in ("seed", "hardware_concurrency", "webgl", "screen"))


def test_grease_brand_matches_chromium_algorithm():
    # Chrome 124 on real browsers reports: "Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"
    p = build_profile("windows", seed=1, chrome_version="124.0.6367.60")
    assert [b["brand"] for b in p.ua_metadata["brands"]] == ["Chromium", "Google Chrome", "Not-A.Brand"]
    assert p.ua_metadata["brands"][2]["version"] == "99"
    assert p.ua_metadata["fullVersionList"][1]["version"] == "124.0.6367.60"


def test_with_geo_and_version_keep_hardware_stable():
    p = build_profile("macos", seed=7)
    g = p.with_geo(locale="de-DE", timezone="Europe/Berlin")
    assert g.languages == ["de-DE", "de"] and g.timezone == "Europe/Berlin" and g.accept_language.startswith("de-DE,de;q=0.9")
    assert (g.hardware_concurrency, g.webgl, g.screen) == (p.hardware_concurrency, p.webgl, p.screen)
    v = p.with_browser_version("150.0.1.2")
    assert v.chrome_major == 150 and v.hardware_concurrency == p.hardware_concurrency
    assert check_consistency(v) == []


def test_resolve_profile_variants():
    assert resolve_profile("linux").os == "linux"
    assert resolve_profile({"os": "windows", "seed": 3}).seed == 3
    assert resolve_profile(None).os == host_profile().os
    with pytest.raises(TypeError):
        resolve_profile(42)
    with pytest.raises(ValueError):
        build_profile("beos")


def test_cdp_override_has_ch_metadata_and_plain_languages():
    p = build_profile("windows", seed=5)
    o = cdp_user_agent_override(p)
    assert o["platform"] == "Win32" and o["userAgentMetadata"]["platform"] == "Windows"
    assert o["acceptLanguage"] == "en-US,en"  # Chrome derives navigator.languages from this; no q-values


def test_script_renders_and_is_valid_js():
    p = build_profile("macos", seed=9)
    for scope in ("window", "worker"):
        js = build_init_script(p, scope=scope, console_guard=True)
        assert "__CFG__" not in js
    node = shutil.which("node")
    if node:
        js = build_init_script(p)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
        try:
            r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
        finally:
            os.unlink(f.name)
    with pytest.raises(ValueError):
        build_init_script(p, scope="bogus")


def test_launch_options_are_hardened():
    cfg = StealthConfig(profile=build_profile("macos", seed=1), proxy="http://u:p%40ss@h.example:8080")
    prof = cfg.resolved_profile()
    o = build_context_options(cfg, prof, backend="playwright")
    assert "--enable-automation" in IGNORE_DEFAULT_ARGS and "--disable-extensions" in IGNORE_DEFAULT_ARGS
    assert "--disable-blink-features=AutomationControlled" in o["args"]
    assert any(a.startswith("--force-webrtc-ip-handling-policy") for a in o["args"])
    assert o["channel"] == "chromium"  # new headless, not chrome-headless-shell
    assert o["proxy"] == {"server": "http://h.example:8080", "username": "u", "password": "p@ss"}
    assert o["timezone_id"] == prof.timezone and o["locale"] == prof.locale
    assert o["viewport"] == prof.viewport and o["device_scale_factor"] == prof.device_scale_factor
    cfg2 = StealthConfig(headless=False, block_webrtc=False)
    o2 = build_context_options(cfg2, cfg2.resolved_profile(), backend="playwright")
    assert o2["headless"] is False and "channel" not in o2
    assert not any("webrtc" in a for a in o2["args"])


def test_normalize_proxy_forms():
    assert normalize_proxy(None) is None
    assert normalize_proxy("host:3128") == {"server": "http://host:3128"}
    assert normalize_proxy({"server": "socks5://h:1", "username": "a"})["username"] == "a"

    class Rot:
        def next(self):
            return "http://r:9"
    assert normalize_proxy(Rot()) == {"server": "http://r:9"}


def test_geo_override_applies_without_network(monkeypatch):
    import shadowscrape.stealth.launch as L
    monkeypatch.setattr(L, "resolve_geo", lambda proxy=None, timeout=8: {"ip": "1.2.3.4", "country": "DE", "timezone": "Europe/Berlin", "locale": "de-DE"})
    cfg = StealthConfig(profile=build_profile("linux", seed=1), geoip=True, proxy="http://x:1")
    p = cfg.resolved_profile()
    assert p.timezone == "Europe/Berlin" and p.locale == "de-DE" and p.languages == ["de-DE", "de"]
    monkeypatch.setattr(L, "resolve_geo", lambda proxy=None, timeout=8: None)  # offline: falls back silently
    assert StealthConfig(profile=build_profile("linux", seed=1), geoip=True).resolved_profile().timezone == "America/New_York"


def test_worker_propagator_state_machine():
    p = build_profile("windows", seed=1)
    prop = _Propagator(p, "/*patch*/")
    steps = prop.on_attached({"sessionId": "S", "targetInfo": {"type": "worker"}, "waitingForDebugger": True})
    methods = [m for _, m, _ in steps]
    assert methods[:2] == ["Debugger.enable", "Debugger.setInstrumentationBreakpoint"]
    assert methods[-1] == "Runtime.runIfWaitingForDebugger"  # resume only after breakpoint is armed
    paused = {"sessionId": "S", "message": json.dumps({"method": "Debugger.paused", "params": {}})}
    first = [m for _, m, _ in prop.on_message(paused)]
    assert first == ["Runtime.evaluate", "Debugger.resume", "Debugger.disable"]
    assert [m for _, m, _ in prop.on_message(paused)] == ["Debugger.resume", "Debugger.disable"]  # patch only once
    iframe = prop.on_attached({"sessionId": "F", "targetInfo": {"type": "iframe"}, "waitingForDebugger": True})
    assert [m for _, m, _ in iframe] == ["Emulation.setUserAgentOverride", "Runtime.runIfWaitingForDebugger"]
    assert prop.on_attached({"sessionId": "X", "targetInfo": {"type": "page"}}) == []


def test_bezier_path_properties():
    rng = random.Random(1)
    pts = bezier_path((10, 10), (810, 410), rng)
    assert len(pts) >= 12
    assert (round(pts[-1][0]), round(pts[-1][1])) == (810, 410)
    assert all(dt >= 1 for _, _, dt in pts)
    # not a straight line: some point deviates from the chord
    def dev(x, y):
        return abs((410 - 10) * (x - 10) - (810 - 10) * (y - 10)) / math.hypot(800, 400)
    assert max(dev(x, y) for x, y, _ in pts) > 2
    # eased timing: first and last steps are slower than the middle
    path = bezier_path((0, 0), (500, 0), random.Random(3), overshoot=False, jitter=0)
    xs = [0.0] + [x for x, _, _ in path]
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert abs(steps[0]) < max(abs(s) for s in steps) / 2 and abs(steps[-1]) < max(abs(s) for s in steps) / 2
    assert bezier_path((5, 5), (5, 5))[0][:2] == (5, 5)
    assert bezier_path((0, 0), (400, 0), random.Random(0)) != bezier_path((0, 0), (400, 0), random.Random(1))


def test_typing_and_scroll_plans():
    d = typing_delays("Hello, World! this is typed", random.Random(2))
    assert len(d) == 27 and min(d) >= 25 and 30 < sum(d) / len(d) < 600
    assert len(set(round(x) for x in d)) > 10  # not constant
    plan = scroll_plan(1000, random.Random(4))
    assert abs(sum(dy for dy, _ in plan) - 1000) < 1.5
    assert sum(dy for dy, _ in scroll_plan(-300, random.Random(4))) < -298
    mags = [abs(dy) for dy, _ in plan if dy]
    assert mags[0] > mags[-1]  # inertial decay


def test_checkbox_point_inside_frame():
    box = {"x": 100, "y": 200, "width": 300, "height": 65}
    for s in range(20):
        x, y = checkbox_point(box, random.Random(s))
        assert 100 + 20 < x < 100 + 50 and 200 + 25 < y < 200 + 40


def test_detect_challenge():
    assert detect_challenge("<html><title>Just a moment...</title>", "Just a moment...")["kind"] == "interstitial"
    assert detect_challenge("<script>window._cf_chl_opt={}</script>", "")["kind"] == "interstitial"
    assert detect_challenge('<div class="cf-turnstile" data-sitekey="x"></div>', "Login")["kind"] == "turnstile"
    assert detect_challenge("<h1>Sorry, you have been blocked</h1>", "Attention Required! | Cloudflare")["kind"] == "blocked"
    # the ubiquitous beacon on ordinary CF-proxied pages must not be treated as a challenge
    assert detect_challenge('<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>', "Home")["kind"] is None
    assert detect_challenge("<p>hi</p>", "x", cookies={"cf_clearance": "1"}) == {"kind": None, "cleared": True, "reason": ""}
