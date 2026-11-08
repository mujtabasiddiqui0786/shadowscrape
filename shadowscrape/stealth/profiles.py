"""Internally consistent browser fingerprint profiles.

Anti-bot systems rarely rely on one tell; the strongest signal is
*inconsistency* between surfaces (a Windows UA with ``MacIntel`` platform, UA-CH
brands that disagree with the UA string, a ``SwiftShader`` WebGL renderer behind
an "NVIDIA" claim, a screen smaller than the window, a timezone that disagrees
with the proxy exit IP ...).  A :class:`Profile` is generated from a single
seed + OS choice so that every derived value agrees with every other value.

The default (``fingerprint_profile=None``) is :func:`host_profile`, which models
the OS the code is actually running on.  That is deliberate: fonts, GPU
behaviour and rendering quirks of the *real* machine leak through no matter what
JavaScript patches say, so claiming the same OS as the host minimises
contradictions.
"""
from __future__ import annotations

import platform as _platform
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CHROME_VERSION = "148.0.7778.96"

_GREASE_CHARS = [" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_"]
_GREASE_VERSIONS = ["8", "99", "24"]
_PERMUTATIONS = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]]

# country code -> (locale, languages)
COUNTRY_LOCALES: Dict[str, str] = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "NZ": "en-NZ", "IE": "en-IE",
    "IN": "en-IN", "SG": "en-SG", "ZA": "en-ZA", "AE": "en-AE", "DE": "de-DE", "AT": "de-AT",
    "CH": "de-CH", "FR": "fr-FR", "BE": "nl-BE", "NL": "nl-NL", "ES": "es-ES", "MX": "es-MX",
    "AR": "es-AR", "CO": "es-CO", "CL": "es-CL", "IT": "it-IT", "PT": "pt-PT", "BR": "pt-BR",
    "PL": "pl-PL", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "RU": "ru-RU",
    "UA": "uk-UA", "TR": "tr-TR", "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW",
    "HK": "zh-HK", "ID": "id-ID", "TH": "th-TH", "VN": "vi-VN", "CZ": "cs-CZ", "RO": "ro-RO",
    "GR": "el-GR", "IL": "he-IL", "SA": "ar-SA", "EG": "ar-EG",
}

_FONTS = {
    "macos": ["Arial", "Arial Black", "Arial Narrow", "Avenir", "Avenir Next", "Baskerville", "Courier",
              "Courier New", "Futura", "Geneva", "Georgia", "Gill Sans", "Helvetica", "Helvetica Neue",
              "Hoefler Text", "Impact", "Lucida Grande", "Menlo", "Monaco", "Optima", "Palatino",
              "SF Pro", "Times", "Times New Roman", "Trebuchet MS", "Verdana"],
    "windows": ["Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Cambria Math", "Candara",
                "Comic Sans MS", "Consolas", "Constantia", "Corbel", "Courier New", "Ebrima", "Franklin Gothic Medium",
                "Gabriola", "Gadugi", "Georgia", "Impact", "Lucida Console", "Lucida Sans Unicode",
                "Malgun Gothic", "Microsoft Sans Serif", "Palatino Linotype", "Segoe UI", "Segoe UI Emoji",
                "Segoe UI Symbol", "Sylfaen", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana"],
    "linux": ["Arial", "Courier New", "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif", "Liberation Mono",
              "Liberation Sans", "Liberation Serif", "Noto Sans", "Noto Serif", "Times New Roman",
              "Ubuntu", "Ubuntu Mono"],
}

# (vendor, renderer) as reported by UNMASKED_VENDOR/RENDERER_WEBGL on real Chrome
_GPUS = {
    "macos": [
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"),
    ],
    "windows": [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER (0x000021C4) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series (0x000067DF) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ],
    "linux": [
        ("Google Inc. (NVIDIA Corporation)", "ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1060/PCIe/SSE2, OpenGL 4.5.0)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Graphics (radeonsi, renoir, LLVM 15.0.7, DRM 3.49, 6.1.0), OpenGL 4.6)"),
    ],
}

# (width, height, dpr, avail-height deduction)  -- deduction = menubar/taskbar
_SCREENS = {
    "macos": [(1440, 900, 2.0, 25), (1512, 982, 2.0, 38), (1728, 1117, 2.0, 38), (1680, 1050, 2.0, 25)],
    "windows": [(1920, 1080, 1.0, 40), (1536, 864, 1.25, 40), (2560, 1440, 1.0, 40), (1366, 768, 1.0, 40)],
    "linux": [(1920, 1080, 1.0, 27), (1600, 900, 1.0, 27), (2560, 1440, 1.0, 27)],
}
# Vertical space eaten by tabs+toolbar (outerHeight - innerHeight) when maximised
_UI_HEIGHT = {"macos": 79, "windows": 85, "linux": 74}

_PLATFORM = {"macos": "MacIntel", "windows": "Win32", "linux": "Linux x86_64"}
_UA_OS = {
    "macos": "Macintosh; Intel Mac OS X 10_15_7",
    "windows": "Windows NT 10.0; Win64; x64",
    "linux": "X11; Linux x86_64",
}
_CH_PLATFORM = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}
_PLATFORM_VERSION = {"macos": "14.6.1", "windows": "15.0.0", "linux": "6.5.0"}


def _grease_brands(major: int, full_version: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Reproduce Chromium's GenerateBrandVersionList so brand order/grease matches real Chrome."""
    seed = major
    grease_brand = "Not%sA%sBrand" % (_GREASE_CHARS[seed % 11], _GREASE_CHARS[(seed + 1) % 11])
    grease_ver = _GREASE_VERSIONS[seed % 3]
    order = _PERMUTATIONS[seed % 6]
    entries_short = [None, None, None]
    entries_full = [None, None, None]
    entries_short[order[0]] = {"brand": grease_brand, "version": grease_ver}
    entries_full[order[0]] = {"brand": grease_brand, "version": grease_ver + ".0.0.0"}
    entries_short[order[1]] = {"brand": "Chromium", "version": str(major)}
    entries_full[order[1]] = {"brand": "Chromium", "version": full_version}
    entries_short[order[2]] = {"brand": "Google Chrome", "version": str(major)}
    entries_full[order[2]] = {"brand": "Google Chrome", "version": full_version}
    return entries_short, entries_full  # type: ignore[return-value]


@dataclass
class Profile:
    """A complete, self-consistent browser identity.  Use :func:`build_profile`."""

    os: str
    chrome_version: str
    user_agent: str
    ua_metadata: Dict[str, Any]
    platform: str
    languages: List[str]
    locale: str
    timezone: str
    screen: Dict[str, int]
    viewport: Dict[str, int]
    device_scale_factor: float
    ui_height: int
    hardware_concurrency: int
    device_memory: int
    webgl: Dict[str, Any]
    fonts: List[str]
    connection: Dict[str, Any]
    battery: Dict[str, Any]
    seed: int
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def chrome_major(self) -> int:
        return int(self.chrome_version.split(".")[0])

    @property
    def accept_language(self) -> str:
        parts = []
        for i, lang in enumerate(self.languages):
            parts.append(lang if i == 0 else "%s;q=%.1f" % (lang, max(0.1, 1 - 0.1 * i)))
        return ",".join(parts)

    def sec_ch_ua(self) -> str:
        """Value a real Chrome sends in the ``Sec-CH-UA`` header."""
        return ", ".join('"%s";v="%s"' % (b["brand"], b["version"]) for b in self.ua_metadata["brands"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_js_config(self) -> Dict[str, Any]:
        """The subset injected into the page-side init script."""
        return {
            "userAgent": self.user_agent,
            "appVersion": self.user_agent.split("Mozilla/", 1)[-1],
            "platform": self.platform,
            "languages": self.languages,
            "hardwareConcurrency": self.hardware_concurrency,
            "deviceMemory": self.device_memory,
            "webgl": dict(self.webgl, patch=bool(self.extra.get("patch_webgl", True))),
            "fonts": self.fonts,
            "connection": self.connection,
            "battery": self.battery,
            "seed": self.seed,
            "uiHeight": self.ui_height,
            "screen": self.screen,
        }

    def with_browser_version(self, version: str) -> "Profile":
        """Return a copy re-targeted at the real Chromium version in use.

        Claiming a Chrome major that differs from the engine's real feature set
        (e.g. new APIs present/absent) is detectable, so the fetcher aligns the
        profile to ``browser.version``.
        """
        m = re.match(r"^(\d+)(?:\.\d+){0,3}$", version or "")
        if not m:
            return self
        full = version if version.count(".") == 3 else "%s.0.0.0" % m.group(1)
        return build_profile(os=self.os, seed=self.seed, chrome_version=full, locale=self.locale,
                             timezone=self.timezone, _gpu_idx=self.extra.get("gpu_idx"),
                             _screen_idx=self.extra.get("screen_idx"))

    def with_real_hardware(self, *, hardware_concurrency: Optional[int] = None,
                           webgl_vendor: Optional[str] = None, webgl_renderer: Optional[str] = None) -> "Profile":
        """Adopt the machine's *real* CPU count / GPU strings (call last; survives no later ``with_*``).

        Untouched natives are the least detectable surface, so for the host-OS default profile we keep the
        real values and the evasion script skips patching them.  Do not adopt software renderers
        (SwiftShader/llvmpipe): those are a headless tell and stay replaced by the synthetic GPU.
        """
        from dataclasses import replace

        extra = dict(self.extra)
        webgl = dict(self.webgl)
        if webgl_vendor and webgl_renderer:
            webgl.update(vendor=webgl_vendor, renderer=webgl_renderer)
            extra["patch_webgl"] = False
        return replace(self, hardware_concurrency=hardware_concurrency or self.hardware_concurrency,
                       webgl=webgl, extra=extra)

    def with_geo(self, *, locale: Optional[str] = None, timezone: Optional[str] = None) -> "Profile":
        """Return a copy with locale/languages/timezone replaced (e.g. from proxy geoip)."""
        return build_profile(os=self.os, seed=self.seed, chrome_version=self.chrome_version,
                             locale=locale or self.locale, timezone=timezone or self.timezone,
                             _gpu_idx=self.extra.get("gpu_idx"), _screen_idx=self.extra.get("screen_idx"))


def detect_host_os() -> str:
    """Map the running OS to a profile OS key (``macos``/``windows``/``linux``)."""
    s = _platform.system().lower()
    return {"darwin": "macos", "windows": "windows"}.get(s, "linux")


def _languages_for(locale: str) -> List[str]:
    base = locale.split("-")[0]
    return [locale] if base == locale else [locale, base]


def build_profile(
    os: Optional[str] = None,
    *,
    seed: Optional[int] = None,
    chrome_version: Optional[str] = None,
    locale: str = "en-US",
    timezone: str = "America/New_York",
    _gpu_idx: Optional[int] = None,
    _screen_idx: Optional[int] = None,
) -> Profile:
    """Build a deterministic profile for ``os`` from ``seed``.

    Everything is derived from the seed with one RNG, so equal arguments yield
    identical profiles (reproducible benchmarks) while different seeds yield
    different canvas/audio noise, GPUs, screens and hardware.
    """
    os = os or detect_host_os()
    if os not in _PLATFORM:
        raise ValueError("os must be one of %s" % sorted(_PLATFORM))
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2 ** 31)
    rng = random.Random(seed)
    full = chrome_version or DEFAULT_CHROME_VERSION
    major = int(full.split(".")[0])
    if full.count(".") < 3:
        full = "%d.0.0.0" % major

    # always draw (even when overridden) so later draws (cores, ...) stay stable
    gpu_draw, scr_draw = rng.randrange(len(_GPUS[os])), rng.randrange(len(_SCREENS[os]))
    gpu_idx = _gpu_idx if _gpu_idx is not None else gpu_draw
    scr_idx = _screen_idx if _screen_idx is not None else scr_draw
    vendor, renderer = _GPUS[os][gpu_idx]
    sw, sh, dpr, deduct = _SCREENS[os][scr_idx]
    ui = _UI_HEIGHT[os]
    avail_h = sh - deduct
    viewport = {"width": sw, "height": avail_h - ui}

    brands, full_list = _grease_brands(major, full)
    arch = "arm" if (os == "macos" and "Apple" in vendor) else "x86"
    ua_metadata = {
        "brands": brands,
        "fullVersionList": full_list,
        "fullVersion": full,
        "platform": _CH_PLATFORM[os],
        "platformVersion": _PLATFORM_VERSION[os],
        "architecture": arch,
        "model": "",
        "mobile": False,
        "bitness": "64",
        "wow64": False,
        "formFactors": ["Desktop"],
    }
    ua = "Mozilla/5.0 (%s) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/%d.0.0.0 Safari/537.36" % (_UA_OS[os], major)

    if os == "macos":
        cores = rng.choice([8, 10, 12])
    else:
        cores = rng.choice([4, 8, 8, 12, 16])
    mem = 8  # navigator.deviceMemory is capped at 8 by Chrome
    return Profile(
        os=os,
        chrome_version=full,
        user_agent=ua,
        ua_metadata=ua_metadata,
        platform=_PLATFORM[os],
        languages=_languages_for(locale),
        locale=locale,
        timezone=timezone,
        screen={"width": sw, "height": sh, "availWidth": sw, "availHeight": avail_h,
                "availTop": deduct if os == "macos" else 0,
                "availLeft": 0, "colorDepth": 24},
        viewport=viewport,
        device_scale_factor=dpr,
        ui_height=ui,
        hardware_concurrency=cores,
        device_memory=mem,
        webgl={"vendor": vendor, "renderer": renderer,
               "params": {"3379": 16384, "34076": 16384, "34024": 16384, "3386": [32767, 32767]}},
        fonts=list(_FONTS[os]),
        connection={"effectiveType": "4g", "rtt": rng.choice([50, 50, 100]), "downlink": rng.choice([1.45, 5.65, 10]),
                    "saveData": False},
        battery={"charging": True, "level": 1.0, "chargingTime": 0, "dischargingTime": None},
        seed=seed,
        extra={"gpu_idx": gpu_idx, "screen_idx": scr_idx},
    )


def host_profile(seed: Optional[int] = None, **kw: Any) -> Profile:
    """Profile that claims the same OS as the machine we run on (recommended default)."""
    return build_profile(detect_host_os(), seed=seed, **kw)


def resolve_profile(spec: Any) -> Profile:
    """Coerce ``fingerprint_profile`` argument: None -> host, str -> OS name, dict -> kwargs, Profile -> itself."""
    if spec is None:
        return host_profile()
    if isinstance(spec, Profile):
        return spec
    if isinstance(spec, str):
        return build_profile(spec)
    if isinstance(spec, dict):
        return build_profile(**spec)
    raise TypeError("fingerprint_profile must be None, an OS name, a dict of build_profile kwargs or a Profile")


def check_consistency(p: Profile) -> List[str]:
    """Return a list of human-readable inconsistencies (empty list == consistent)."""
    problems: List[str] = []
    if p.platform != _PLATFORM[p.os]:
        problems.append("navigator.platform %r does not match os %r" % (p.platform, p.os))
    if _UA_OS[p.os] not in p.user_agent:
        problems.append("UA string OS token does not match os")
    if "Chrome/%d.0.0.0" % p.chrome_major not in p.user_agent:
        problems.append("UA major version != chrome_version")
    if p.ua_metadata["platform"] != _CH_PLATFORM[p.os]:
        problems.append("UA-CH platform mismatch")
    for b in p.ua_metadata["brands"]:
        if b["brand"] == "Google Chrome" and b["version"] != str(p.chrome_major):
            problems.append("UA-CH brand version != UA major")
    if "Headless" in p.user_agent:
        problems.append("UA leaks Headless")
    if p.os == "macos" and "Direct3D" in p.webgl["renderer"]:
        problems.append("macOS profile with Direct3D renderer")
    if p.os == "windows" and "Metal" in p.webgl["renderer"]:
        problems.append("Windows profile with Metal renderer")
    if p.viewport["width"] > p.screen["availWidth"] or p.viewport["height"] > p.screen["availHeight"]:
        problems.append("viewport larger than available screen")
    if p.languages[0] != p.locale:
        problems.append("languages[0] != locale")
    if p.device_memory > 8:
        problems.append("deviceMemory above Chrome's cap of 8")
    return problems
