"""Hardened Chromium launch options, backend selection, geoip and CDP wiring.

Backend selection
-----------------
``patchright`` (a Playwright fork that never sends ``Runtime.enable`` and runs
``evaluate`` in isolated contexts) is used automatically when importable;
otherwise plain ``playwright`` is used and a console-serialisation guard is
injected as a *partial* mitigation of the ``Runtime.enable`` tell (see
:mod:`shadowscrape.stealth.scripts`).
"""
from __future__ import annotations

import importlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from .profiles import COUNTRY_LOCALES, Profile, resolve_profile
from .scripts import build_init_script, cdp_user_agent_override

# Flags Playwright adds by default that real Chrome users do not have.
IGNORE_DEFAULT_ARGS = [
    "--enable-automation",      # sets navigator.webdriver, shows infobar
    "--disable-extensions",     # real browsers have extensions enabled
    "--disable-popup-blocking",  # real browsers block un-gestured popups
    "--disable-component-update",
    "--disable-default-apps",
]

HARDENED_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--use-mock-keychain",
    "--force-color-profile=srgb",
    "--disable-features=Translate,MediaRouter,OptimizationHints,AutomationControlled",
    "--disable-infobars",
]

WEBRTC_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--enforce-webrtc-ip-permission-check",
]

BACKENDS = ("patchright", "playwright")


def get_backend(prefer: str = "auto") -> Tuple[str, Any, Any]:
    """Return ``(name, sync_api_module, async_api_module)``.

    ``prefer``: ``"auto"`` (patchright if importable), ``"patchright"`` or ``"playwright"``.
    """
    order = BACKENDS if prefer == "auto" else (prefer,)
    last: Optional[Exception] = None
    for name in order:
        try:
            return name, importlib.import_module(name + ".sync_api"), importlib.import_module(name + ".async_api")
        except ImportError as e:  # pragma: no cover - depends on env
            last = e
    raise RuntimeError("No browser backend available (pip install 'shadowscrape[browser]'): %s" % last)


def normalize_proxy(proxy: Any) -> Optional[Dict[str, str]]:
    """Normalise ``None | 'http://u:p@h:port' | dict | ProxyRotator-like`` to a Playwright proxy dict."""
    if proxy is None:
        return None
    if hasattr(proxy, "next") and callable(proxy.next):
        proxy = proxy.next()
    if isinstance(proxy, dict):
        d = dict(proxy)
        if "server" not in d and "url" in d:
            d["server"] = d.pop("url")
        return normalize_proxy(d["server"]) if len(d) == 1 else d
    u = urlparse(proxy if "://" in proxy else "http://" + proxy)
    out = {"server": "%s://%s%s" % (u.scheme, u.hostname, ":%d" % u.port if u.port else "")}
    if u.username:
        out["username"] = unquote(u.username)
    if u.password:
        out["password"] = unquote(u.password)
    return out


def resolve_geo(proxy: Any = None, timeout: float = 8.0) -> Optional[Dict[str, str]]:
    """Best-effort exit-IP geolocation -> ``{ip, country, timezone, locale}`` (None offline).

    Queries ``ip-api.com`` *through the proxy* (or directly if none) so the
    reported timezone matches the address sites will see.
    """
    p = normalize_proxy(proxy)
    try:
        handlers: List[Any] = []
        if p:
            u = urlparse(p["server"])
            auth = ""
            if p.get("username"):
                auth = "%s:%s@" % (p["username"], p.get("password", ""))
            purl = "%s://%s%s" % (u.scheme, auth, u.netloc)
            handlers.append(urllib.request.ProxyHandler({"http": purl, "https": purl}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open("http://ip-api.com/json/?fields=status,countryCode,timezone,query", timeout=timeout) as r:
            data = json.loads(r.read().decode())
        if data.get("status") != "success":
            return None
        cc = data.get("countryCode", "US")
        return {"ip": data.get("query", ""), "country": cc, "timezone": data["timezone"],
                "locale": COUNTRY_LOCALES.get(cc, "en-US")}
    except Exception:
        return None


@dataclass
class StealthConfig:
    """Everything needed to launch a consistent stealth browser context."""

    profile: Any = None                  # Profile | None | os-name | dict
    headless: Any = True                 # True -> new headless (full chromium), False -> real headful, "old" -> headless shell
    proxy: Any = None
    block_webrtc: bool = True
    geoip: bool = False                  # derive timezone/locale from the proxy exit IP
    user_data_dir: Optional[str] = None  # None -> temp dir (ephemeral); path -> persistent profile
    extra_args: List[str] = field(default_factory=list)
    channel: Optional[str] = None        # e.g. "chrome" for a real installed Chrome
    executable_path: Optional[str] = None
    backend: str = "auto"
    chrome_runtime: bool = False
    noise: bool = True                   # canvas/audio readout noise (see build_init_script)
    adopt_real_hardware: bool = True     # default profile only: keep real CPU count / GPU strings if not software-rendered
    locale: Optional[str] = None
    timezone: Optional[str] = None

    def resolved_profile(self) -> Profile:
        prof = resolve_profile(self.profile)
        loc, tz = self.locale, self.timezone
        if self.geoip and not (loc and tz):
            geo = resolve_geo(self.proxy)
            if geo:
                loc, tz = loc or geo["locale"], tz or geo["timezone"]
        if loc or tz:
            prof = prof.with_geo(locale=loc, timezone=tz)
        return prof


def build_launch_args(cfg: StealthConfig, profile: Profile) -> List[str]:
    args = list(HARDENED_ARGS)
    args.append("--lang=%s" % profile.locale)
    if cfg.block_webrtc:
        args += WEBRTC_ARGS
    if cfg.headless is False:
        args.append("--window-position=0,0")
        args.append("--window-size=%d,%d" % (profile.viewport["width"], profile.viewport["height"] + profile.ui_height))
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # only when running as root (containers); the flag is itself a tell
    args += cfg.extra_args
    return args


def build_context_options(cfg: StealthConfig, profile: Profile, *, backend: str) -> Dict[str, Any]:
    """kwargs for ``chromium.launch_persistent_context`` (excluding user_data_dir)."""
    headless = cfg.headless
    opts: Dict[str, Any] = {
        "headless": bool(headless),
        "args": build_launch_args(cfg, profile),
        "ignore_default_args": list(IGNORE_DEFAULT_ARGS),
        "viewport": dict(profile.viewport),
        "screen": {"width": profile.screen["width"], "height": profile.screen["height"]},
        "device_scale_factor": profile.device_scale_factor,
        "locale": profile.locale,
        "timezone_id": profile.timezone,
        "color_scheme": "light",
        "has_touch": False,
        "is_mobile": False,
        "java_script_enabled": True,
    }
    if cfg.executable_path:
        opts["executable_path"] = cfg.executable_path
    elif cfg.channel:
        opts["channel"] = cfg.channel
    elif headless is True:
        opts["channel"] = "chromium"  # full chromium in *new* headless mode (not chrome-headless-shell)
    px = normalize_proxy(cfg.proxy)
    if px:
        opts["proxy"] = px
    return opts


def console_guard_needed(backend: str) -> bool:
    return backend != "patchright"


def make_scripts(cfg: StealthConfig, profile: Profile, backend: str) -> Tuple[str, str]:
    """(window_script, worker_script) for the final profile."""
    win = build_init_script(profile, scope="window", block_webrtc=cfg.block_webrtc, noise=cfg.noise,
                            chrome_runtime=cfg.chrome_runtime, console_guard=console_guard_needed(backend))
    worker = build_init_script(profile, scope="worker", block_webrtc=False)
    return win, worker


_ID = [1000]


def _msg(method: str, params: Dict[str, Any]) -> str:
    _ID[0] += 1
    return json.dumps({"id": _ID[0], "method": method, "params": params})


_AUTO_ATTACH = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": False}
_VERSION_RE = re.compile(r"/(\d+\.\d+\.\d+\.\d+)")
_WORKER_TYPES = ("worker", "shared_worker", "service_worker")


class _Propagator:
    """Pure state machine deciding which CDP messages to send to child targets.

    Dedicated workers are not covered by ``addInitScript``.  We attach to them
    paused-on-start, install a ``beforeScriptExecution`` instrumentation
    breakpoint (``Runtime.evaluate`` right after attach loses the race against
    the worker's first script), evaluate the worker patch when the breakpoint
    hits, then resume.  Out-of-process iframes only need the UA override.
    """

    def __init__(self, profile: Profile, worker_script: str) -> None:
        self.profile = profile
        self.worker_script = worker_script
        self.targets: Dict[str, Dict[str, Any]] = {}

    def on_attached(self, params: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
        sid, ttype = params["sessionId"], params["targetInfo"]["type"]
        ua = cdp_user_agent_override(self.profile)
        out: List[Tuple[str, str, Dict[str, Any]]] = []
        if ttype == "iframe":
            out.append((sid, "Emulation.setUserAgentOverride", ua))
        elif ttype in _WORKER_TYPES:
            self.targets[sid] = {"patched": False}
            out += [(sid, "Debugger.enable", {}),
                    (sid, "Debugger.setInstrumentationBreakpoint", {"instrumentation": "beforeScriptExecution"}),
                    (sid, "Network.setUserAgentOverride", ua)]
        else:
            return []
        if params.get("waitingForDebugger"):
            out.append((sid, "Runtime.runIfWaitingForDebugger", {}))
        return out

    def on_message(self, params: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
        sid = params.get("sessionId")
        st = self.targets.get(sid)
        if st is None:
            return []
        try:
            msg = json.loads(params["message"])
        except Exception:
            return []
        if msg.get("method") != "Debugger.paused":
            return []
        out: List[Tuple[str, str, Dict[str, Any]]] = []
        if not st["patched"]:
            st["patched"] = True
            out.append((sid, "Runtime.evaluate", {"expression": self.worker_script, "silent": True}))
        out.append((sid, "Debugger.resume", {}))
        out.append((sid, "Debugger.disable", {}))
        return out


def _wrap(sid: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"sessionId": sid, "message": _msg(method, params)}


def setup_page_sync(context: Any, page: Any, profile: Profile, worker_script: str) -> Any:
    """Apply UA/UA-CH overrides to ``page`` and propagate patches into workers & OOPIFs (sync API)."""
    cdp = context.new_cdp_session(page)
    cdp.send("Emulation.setUserAgentOverride", cdp_user_agent_override(profile))
    try:  # native override (no JS patch needed, also reaches workers)
        cdp.send("Emulation.setHardwareConcurrencyOverride", {"hardwareConcurrency": profile.hardware_concurrency})
    except Exception:
        pass
    prop = _Propagator(profile, worker_script)

    def run(steps: List[Tuple[str, str, Dict[str, Any]]]) -> None:
        for sid, method, p in steps:
            try:
                cdp.send("Target.sendMessageToTarget", _wrap(sid, method, p))
            except Exception:
                pass

    cdp.on("Target.attachedToTarget", lambda params: run(prop.on_attached(params)))
    cdp.on("Target.receivedMessageFromTarget", lambda params: run(prop.on_message(params)))
    cdp.send("Target.setAutoAttach", _AUTO_ATTACH)
    return cdp


async def setup_page_async(context: Any, page: Any, profile: Profile, worker_script: str) -> Any:
    """Async twin of :func:`setup_page_sync`."""
    import asyncio

    cdp = await context.new_cdp_session(page)
    await cdp.send("Emulation.setUserAgentOverride", cdp_user_agent_override(profile))
    try:
        await cdp.send("Emulation.setHardwareConcurrencyOverride", {"hardwareConcurrency": profile.hardware_concurrency})
    except Exception:
        pass
    prop = _Propagator(profile, worker_script)

    async def run(steps: List[Tuple[str, str, Dict[str, Any]]]) -> None:
        for sid, method, p in steps:
            try:
                await cdp.send("Target.sendMessageToTarget", _wrap(sid, method, p))
            except Exception:
                pass

    cdp.on("Target.attachedToTarget", lambda params: asyncio.ensure_future(run(prop.on_attached(params))))
    cdp.on("Target.receivedMessageFromTarget", lambda params: asyncio.ensure_future(run(prop.on_message(params))))
    await cdp.send("Target.setAutoAttach", _AUTO_ATTACH)
    return cdp


def browser_full_version_sync(context: Any, page: Any) -> Optional[str]:
    cdp = context.new_cdp_session(page)
    try:
        m = _VERSION_RE.search(cdp.send("Browser.getVersion").get("product", ""))
        return m.group(1) if m else None
    finally:
        try:
            cdp.detach()
        except Exception:
            pass


async def browser_full_version_async(context: Any, page: Any) -> Optional[str]:
    cdp = await context.new_cdp_session(page)
    try:
        m = _VERSION_RE.search((await cdp.send("Browser.getVersion")).get("product", ""))
        return m.group(1) if m else None
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


_HW_PROBE = """() => { const o = { hc: navigator.hardwareConcurrency, vendor: null, renderer: null };
  try { const gl = document.createElement('canvas').getContext('webgl'); const e = gl.getExtension('WEBGL_debug_renderer_info');
        o.vendor = gl.getParameter(e.UNMASKED_VENDOR_WEBGL); o.renderer = gl.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (err) {}
  return o; }"""
_SOFTWARE_GPU = re.compile(r"swiftshader|llvmpipe|softpipe|software|microsoft basic render", re.I)


def adopt_hardware(cfg: StealthConfig, profile: Profile, probe: Optional[Dict[str, Any]]) -> Profile:
    """Default (host-OS) profile keeps the machine's real CPU count and GPU strings unless software-rendered."""
    if not probe or cfg.profile is not None or not cfg.adopt_real_hardware:
        return profile
    v, r = probe.get("vendor"), probe.get("renderer")
    real_gpu = bool(v and r and not _SOFTWARE_GPU.search(r))
    return profile.with_real_hardware(hardware_concurrency=probe.get("hc"),
                                      webgl_vendor=v if real_gpu else None, webgl_renderer=r if real_gpu else None)


def start_stealth_context_sync(cfg: StealthConfig) -> Tuple[Any, Any, Profile, str, Optional[str]]:
    """Launch a hardened persistent context and wire all evasions.

    Returns ``(playwright, context, final_profile, backend_name, tmpdir_or_None)``.
    The context already has the init script installed and every existing and
    future page wired for UA-CH / worker propagation.
    """
    backend, sync_api, _ = get_backend(cfg.backend)
    profile = cfg.resolved_profile()
    tmp = None
    udd = cfg.user_data_dir
    if not udd:
        tmp = tempfile.mkdtemp(prefix="shadowscrape-")
        udd = tmp
    pw = sync_api.sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(udd, **build_context_options(cfg, profile, backend=backend))
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        ver = browser_full_version_sync(ctx, first)
        if ver:
            profile = profile.with_browser_version(ver)
        try:
            probe = first.evaluate(_HW_PROBE)
        except Exception:
            probe = None
        profile = adopt_hardware(cfg, profile, probe)
        win, worker = make_scripts(cfg, profile, backend)
        ctx.add_init_script(win)

        def prepare(page: Any) -> None:
            """Idempotent per-page wiring; call before navigating a page you created."""
            state = getattr(page, "_ss_state", None)
            if state is None:
                page._ss_state = "busy"
                try:
                    page._ss_cdp = setup_page_sync(ctx, page, profile, worker)
                finally:
                    page._ss_state = "done"
            else:
                while getattr(page, "_ss_state", "done") == "busy":  # another greenlet (page event) is wiring it
                    page.wait_for_timeout(5)

        ctx._ss_prepare = prepare
        prepare(first)
        ctx.on("page", prepare)
    except Exception:
        pw.stop()
        raise
    return pw, ctx, profile, backend, tmp


async def start_stealth_context_async(cfg: StealthConfig) -> Tuple[Any, Any, Profile, str, Optional[str]]:
    """Async twin of :func:`start_stealth_context_sync`."""
    import asyncio

    backend, _, async_api = get_backend(cfg.backend)
    profile = await asyncio.get_event_loop().run_in_executor(None, cfg.resolved_profile)
    tmp = None
    udd = cfg.user_data_dir
    if not udd:
        tmp = tempfile.mkdtemp(prefix="shadowscrape-")
        udd = tmp
    pw = await async_api.async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(udd, **build_context_options(cfg, profile, backend=backend))
        first = ctx.pages[0] if ctx.pages else await ctx.new_page()
        ver = await browser_full_version_async(ctx, first)
        if ver:
            profile = profile.with_browser_version(ver)
        try:
            probe = await first.evaluate(_HW_PROBE)
        except Exception:
            probe = None
        profile = adopt_hardware(cfg, profile, probe)
        win, worker = make_scripts(cfg, profile, backend)
        await ctx.add_init_script(win)

        async def prepare(page: Any) -> None:
            """Idempotent per-page wiring; await before navigating a page you created."""
            fut = getattr(page, "_ss_cdp", None)
            if fut is None:
                fut = page._ss_cdp = asyncio.ensure_future(setup_page_async(ctx, page, profile, worker))
            await fut

        ctx._ss_prepare = prepare
        await prepare(first)
        ctx.on("page", lambda p: asyncio.ensure_future(prepare(p)))
    except Exception:
        await pw.stop()
        raise
    return pw, ctx, profile, backend, tmp
