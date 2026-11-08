"""Cloudflare Turnstile / interstitial ("Just a moment...") detection and solving.

Solving strategy: *behavioural only*.  We wait (the non-interactive "managed"
challenge frequently resolves by itself when the browser fingerprint is
coherent), then, if a checkbox frame is shown, move the mouse along a human
bezier path to the checkbox, dwell, click, and wait for the resolution signal
(``cf_clearance`` cookie / interstitial gone / ``cf-turnstile-response`` filled).

No third-party CAPTCHA-solving services are used.  Limits: this does not defeat
IP-reputation blocks, "Sorry, you have been blocked" (error 1020) pages, or
challenges that require a genuinely trusted fingerprint/IP; success rates
against live Cloudflare were NOT measured here.
"""
from __future__ import annotations

import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .humanize import AsyncHumanizer, Humanizer

CF_FRAME_RE = re.compile(r"challenges\.cloudflare\.com|/cdn-cgi/challenge-platform/.*turnstile|turnstile", re.I)

_INTERSTITIAL_TITLES = ("just a moment", "checking your browser", "un instant", "einen moment", "un momento", "moment…")
# NOTE: "/cdn-cgi/challenge-platform/" alone is NOT a marker: ordinary Cloudflare-proxied pages include that beacon.
_INTERSTITIAL_MARKERS = ("cf-browser-verification", "cf_chl_opt", "cf-challenge-running", "cf-please-wait",
                         "id=\"challenge-stage\"", "id=\"challenge-form\"")
_BLOCKED_MARKERS = ("sorry, you have been blocked", "attention required! | cloudflare", "error code: 1020",
                    "access denied | ", "cloudflare ray id")

_TARGET_SELECTORS = ["#turnstile-wrapper", "iframe[src*='challenges.cloudflare.com']", ".cf-turnstile",
                     "[id^='cf-chl-widget']", "#challenge-stage", "#cf-challenge-running"]


def detect_challenge(html: str = "", title: str = "", url: str = "", cookies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Classify a page.  Pure function (offline-testable).

    Returns ``{"kind": None|"interstitial"|"turnstile"|"blocked", "cleared": bool, "reason": str}``.
    ``cleared`` is True when a ``cf_clearance`` cookie is present.
    """
    h = (html or "").lower()
    t = (title or "").lower()
    cleared = bool(cookies and "cf_clearance" in cookies)
    if any(m in h for m in _BLOCKED_MARKERS[:3]):
        return {"kind": "blocked", "cleared": cleared, "reason": "cloudflare block page (not solvable by interaction)"}
    if any(x in t for x in _INTERSTITIAL_TITLES):
        return {"kind": "interstitial", "cleared": cleared, "reason": "title: %s" % title}
    if any(m in h for m in _INTERSTITIAL_MARKERS):
        return {"kind": "interstitial", "cleared": cleared, "reason": "challenge markers"}
    if "challenges.cloudflare.com/turnstile" in h or "cf-turnstile" in h:
        return {"kind": "turnstile", "cleared": cleared, "reason": "embedded turnstile widget"}
    return {"kind": None, "cleared": cleared, "reason": ""}


def checkbox_point(box: Dict[str, float], rng: Optional[random.Random] = None) -> Tuple[float, float]:
    """Where the Turnstile checkbox sits inside its iframe box (~30px from the left, vertically centred)."""
    rng = rng or random.Random()
    x = box["x"] + min(30.0, box["width"] * 0.14) + rng.uniform(-3, 3)
    y = box["y"] + box["height"] / 2 + rng.uniform(-4, 4)
    return x, y


def _result(kind: Optional[str], solved: bool, attempts: int, t0: float, method: str, note: str = "") -> Dict[str, Any]:
    return {"detected": kind is not None, "kind": kind, "solved": solved, "attempts": attempts,
            "elapsed": round(time.time() - t0, 2), "method": method, "note": note}


def solve_sync(page: Any, *, timeout_ms: int = 45000, settle_ms: int = 4500, max_clicks: int = 3,
               frame_pattern: "re.Pattern[str]" = CF_FRAME_RE, rng: Optional[random.Random] = None,
               humanizer: Optional[Humanizer] = None) -> Dict[str, Any]:
    """Detect and, if needed, interact with a Cloudflare challenge on ``page`` (sync API)."""
    rng = rng or random.Random()
    hz = humanizer or Humanizer(page, rng)
    t0 = time.time()
    first_kind: Optional[str] = None
    clicks = 0
    last_click = 0.0
    method = "none"
    while (time.time() - t0) * 1000 < timeout_ms:
        try:
            html, title = page.content(), page.title()
            cookies = {c["name"]: c["value"] for c in page.context.cookies()}
        except Exception:
            page.wait_for_timeout(300)
            continue
        st = detect_challenge(html, title, page.url, cookies)
        if st["kind"] and first_kind is None:
            first_kind = st["kind"]
        if st["kind"] == "blocked":
            return _result("blocked", False, clicks, t0, method, st["reason"])
        if st["kind"] is None:
            return _result(first_kind, first_kind is not None, clicks, t0, method if clicks else "auto" if first_kind else "none")
        if st["kind"] == "turnstile" and _token_filled_sync(page):
            return _result(first_kind, True, clicks, t0, method)
        elapsed_ms = (time.time() - t0) * 1000
        if elapsed_ms > settle_ms and clicks < max_clicks and time.time() - last_click > 6:
            box = _find_box_sync(page, frame_pattern)
            if box:
                tx, ty = checkbox_point(box, rng)
                hz.click(tx, ty, spread=1.5)
                clicks += 1
                last_click = time.time()
                method = "click"
                continue
        # look alive while waiting
        hz.move_to(hz.pos[0] + rng.uniform(-40, 40), hz.pos[1] + rng.uniform(-25, 25))
        page.wait_for_timeout(rng.uniform(350, 800))
    return _result(first_kind, False, clicks, t0, method, "timeout")


def _find_box_sync(page: Any, pattern: "re.Pattern[str]") -> Optional[Dict[str, float]]:
    for fr in page.frames:
        if fr is page.main_frame or not pattern.search(fr.url or ""):
            continue
        try:
            box = fr.frame_element().bounding_box()
            if box and box["width"] > 20 and box["height"] > 20:
                return box
        except Exception:
            continue
    for sel in _TARGET_SELECTORS:
        try:
            el = page.query_selector(sel)
            box = el.bounding_box() if el else None
            if box and box["width"] > 20 and box["height"] > 20:
                return box
        except Exception:
            continue
    return None


def _token_filled_sync(page: Any) -> bool:
    try:
        return bool(page.evaluate("() => { const e = document.querySelector('[name=cf-turnstile-response]'); return !!(e && e.value); }"))
    except Exception:
        return False


async def solve_async(page: Any, *, timeout_ms: int = 45000, settle_ms: int = 4500, max_clicks: int = 3,
                      frame_pattern: "re.Pattern[str]" = CF_FRAME_RE, rng: Optional[random.Random] = None,
                      humanizer: Optional[AsyncHumanizer] = None) -> Dict[str, Any]:
    """Async twin of :func:`solve_sync`."""
    rng = rng or random.Random()
    hz = humanizer or AsyncHumanizer(page, rng)
    t0 = time.time()
    first_kind: Optional[str] = None
    clicks = 0
    last_click = 0.0
    method = "none"
    while (time.time() - t0) * 1000 < timeout_ms:
        try:
            html, title = await page.content(), await page.title()
            cookies = {c["name"]: c["value"] for c in await page.context.cookies()}
        except Exception:
            await page.wait_for_timeout(300)
            continue
        st = detect_challenge(html, title, page.url, cookies)
        if st["kind"] and first_kind is None:
            first_kind = st["kind"]
        if st["kind"] == "blocked":
            return _result("blocked", False, clicks, t0, method, st["reason"])
        if st["kind"] is None:
            return _result(first_kind, first_kind is not None, clicks, t0, method if clicks else "auto" if first_kind else "none")
        if st["kind"] == "turnstile" and await _token_filled_async(page):
            return _result(first_kind, True, clicks, t0, method)
        elapsed_ms = (time.time() - t0) * 1000
        if elapsed_ms > settle_ms and clicks < max_clicks and time.time() - last_click > 6:
            box = await _find_box_async(page, frame_pattern)
            if box:
                tx, ty = checkbox_point(box, rng)
                await hz.click(tx, ty, spread=1.5)
                clicks += 1
                last_click = time.time()
                method = "click"
                continue
        await hz.move_to(hz.pos[0] + rng.uniform(-40, 40), hz.pos[1] + rng.uniform(-25, 25))
        await page.wait_for_timeout(rng.uniform(350, 800))
    return _result(first_kind, False, clicks, t0, method, "timeout")


async def _find_box_async(page: Any, pattern: "re.Pattern[str]") -> Optional[Dict[str, float]]:
    for fr in page.frames:
        if fr is page.main_frame or not pattern.search(fr.url or ""):
            continue
        try:
            box = await (await fr.frame_element()).bounding_box()
            if box and box["width"] > 20 and box["height"] > 20:
                return box
        except Exception:
            continue
    for sel in _TARGET_SELECTORS:
        try:
            el = await page.query_selector(sel)
            box = await el.bounding_box() if el else None
            if box and box["width"] > 20 and box["height"] > 20:
                return box
        except Exception:
            continue
    return None


async def _token_filled_async(page: Any) -> bool:
    try:
        return bool(await page.evaluate("() => { const e = document.querySelector('[name=cf-turnstile-response]'); return !!(e && e.value); }"))
    except Exception:
        return False
