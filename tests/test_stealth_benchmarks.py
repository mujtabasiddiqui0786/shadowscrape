"""Tests for the benchmark runner: parsers on canned pages, offline behaviour, local end-to-end run."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, for `benchmarks`
from benchmarks import detectors as D  # noqa: E402


def test_parse_sannysoft():
    ok = '<table><tr><td>WebDriver</td><td class="passed">missing (passed)</td></tr><tr><td>x</td><td class="warn">w</td></tr></table>'
    bad = ok + '<tr><td>Chrome (New)</td><td class="failed">missing (failed)</td></tr>'
    assert D.parse_sannysoft(ok, "")[0] == "pass"
    v, d = D.parse_sannysoft(bad, "")
    assert v == "fail" and d["failed"] == 1 and "Chrome (New)" in d["failed_rows"][0]
    assert D.parse_sannysoft("<html>blocked</html>", "")[0] == "unknown"


def test_parse_small_pages():
    assert D.parse_areyouheadless("", "You are not Chrome headless")[0] == "pass"
    assert D.parse_areyouheadless("", "You are Chrome headless")[0] == "fail"
    assert D.parse_areyouheadless("", "502 Bad Gateway")[0] == "unknown"
    assert D.parse_browserscan("", "Test Results: Normal")[0] == "pass"
    assert D.parse_browserscan("", "Test Results: Robot")[0] == "fail"
    assert D.parse_nowsecure("<title>Just a moment...</title>", "Just a moment")[0] == "fail"
    assert D.parse_nowsecure("<h1>nowSecure</h1>", "nowSecure OK")[0] == "pass"
    v, d = D.parse_tls("", json.dumps({"http_version": "h2", "user_agent": "UA", "tls": {"ja3_hash": "abc", "ja4": "t13"}, "http2": {"akamai_fingerprint_hash": "z"}}))
    assert v == "info" and d["ja3_hash"] == "abc" and d["akamai_hash"] == "z"


def test_parse_creepjs():
    good = "<div>0% headless: ... 31% like headless: ... 0% stealth: Headless webDriverIsOn: false hasToStringProxy: false hasBadWebGL: false</div>"
    v, d = D.parse_creepjs(good, "")
    assert v == "pass" and d["like_headless_pct"] == 31 and d["webdriver_is_on"] is False
    bad = good.replace("0% stealth", "20% stealth").replace("hasToStringProxy: false", "hasToStringProxy: true")
    assert D.parse_creepjs(bad, "")[0] == "fail"
    assert D.parse_creepjs("<p>loading</p>", "")[0] == "unknown"


def test_offline_run_skips_public_targets_gracefully(tmp_path):
    rep = D.run(["playwright", "stealthy"],["sannysoft", "creepjs"], out_dir=str(tmp_path), force_offline=True, verbose=False)
    assert rep["network"] is False
    assert {r["verdict"] for r in rep["results"]} == {"skipped"}
    assert all("no network" in r["note"] or "not installed" in r["note"] for r in rep["results"])
    assert (tmp_path / "latest.json").exists() and "No network" in (tmp_path / "latest.md").read_text()


def test_markdown_summary_cells():
    rep = {"engines": ["playwright"], "targets": ["t"], "results": [
        {"engine": "playwright", "target": "t", "verdict": "pass"}, {"engine": "playwright", "target": "t", "verdict": "fail"},
        {"engine": "playwright", "target": "t", "verdict": "pass"}]}
    assert D.summarise(rep)[("playwright", "t")] == "pass 2/3"


@pytest.mark.browser
def test_local_probe_end_to_end(tmp_path):
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            if not os.path.exists(p.chromium.executable_path):
                pytest.skip("chromium not installed")
    except ImportError:
        pytest.skip("playwright not installed")
    rep = D.run(["playwright", "stealthy"], ["local-probe"], out_dir=str(tmp_path), force_offline=True, verbose=False)
    by = {r["engine"]: r for r in rep["results"]}
    assert by["playwright"]["verdict"] == "fail" and by["stealthy"]["verdict"] == "pass", by
    assert os.path.exists(tmp_path / "latest.md")
