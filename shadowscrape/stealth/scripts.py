"""JavaScript init scripts (evasions) injected before any page script.

One JS template (:data:`_JS`) is rendered with a JSON config derived from a
:class:`~shadowscrape.stealth.profiles.Profile`.  All patches are installed on
*prototypes* (like the native accessors) and every replaced function is
registered in a ``Function.prototype.toString`` mask so
``fn.toString()`` reports ``function name() { [native code] }``.

Honest scope: these patches defeat *property-level* checks.  They cannot change
real GPU/driver behaviour, TLS/HTTP2 fingerprints, font metrics of the host or
server-side behavioural scoring.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .profiles import Profile

_JS = r"""
(() => {
'use strict';
const CFG = __CFG__;
const G = globalThis;
const IS_WIN = typeof window !== 'undefined' && typeof document !== 'undefined' && G === window;
const IS_WORKER = !IS_WIN;

// ---------------------------------------------------------------- helpers
// Design note: we do NOT replace Function.prototype.toString (a Proxy there is detectable through
// error stacks / lie probes).  Instead every patched native is a Proxy *around the original native*, so
// toString/descriptors/name/length/illegal-invocation semantics come from the engine.  Functions we must
// fabricate (chrome.loadTimes ...) are bound functions, which stringify as "function () { [native code] }".
const mask = (fn, name) => {
  const b = Function.prototype.bind.call(fn, null);
  Object.defineProperty(b, 'name', { value: name, configurable: true });
  return b;
};
// replace an accessor's getter; the original getter still runs first so receiver validation (TypeError on
// Navigator.prototype.x) stays native, then valueFn(original result) decides the value
const patchGetter = (obj, prop, valueFn) => {
  if (!obj) return false;
  const d = Object.getOwnPropertyDescriptor(obj, prop);
  if (!d || !d.get) return false;
  const g = new Proxy(d.get, {
    apply(target, thisArg, args) { const r = Reflect.apply(target, thisArg, args); return valueFn.call(thisArg, r); }
  });
  Object.defineProperty(obj, prop, { get: g, set: d.set, enumerable: d.enumerable, configurable: true });
  return true;
};
const patchMethod = (obj, name, impl) => {
  if (!obj) return;
  const d = Object.getOwnPropertyDescriptor(obj, name);
  const orig = d ? d.value : undefined;
  if (typeof orig !== 'function') return;
  const p = new Proxy(orig, { apply(target, thisArg, args) { return impl.call(thisArg, target, args); } });
  Object.defineProperty(obj, name, { value: p, writable: d.writable, enumerable: d.enumerable, configurable: true });
};
// make prototype getters consult a per-instance store (used for fabricated native-class instances)
const shimGetters = (Proto, store, names) => {
  if (!Proto) return;
  for (const n of names) {
    const d = Object.getOwnPropertyDescriptor(Proto, n);
    if (!d || !d.get) continue;
    const g = new Proxy(d.get, {
      apply(target, thisArg, args) { const s = store.get(thisArg); return s ? s[n] : Reflect.apply(target, thisArg, args); }
    });
    Object.defineProperty(Proto, n, { get: g, set: d.set, enumerable: d.enumerable, configurable: true });
  }
};
// patch a getter only when the engine's current value differs: an untouched native is undetectable
const patchIfDiff = (inst, proto, prop, want) => {
  let cur; try { cur = inst[prop]; } catch (e) {}
  if (cur !== undefined && JSON.stringify(cur) === JSON.stringify(want)) return;
  patchGetter(proto, prop, () => want);
};
const hash32 = (x) => { x = Math.imul(x ^ (x >>> 16), 0x45d9f3b); x = Math.imul(x ^ (x >>> 16), 0x45d9f3b); return (x ^ (x >>> 16)) >>> 0; };
const SEED = CFG.seed | 0;

// ---------------------------------------------------------------- navigator
const navProto = IS_WIN ? G.Navigator && Navigator.prototype : G.WorkerNavigator && WorkerNavigator.prototype;
if (navProto) {
  const nav = G.navigator;
  patchIfDiff(nav, navProto, 'userAgent', CFG.userAgent);
  if (IS_WIN) { patchIfDiff(nav, navProto, 'appVersion', CFG.appVersion); if (nav.webdriver !== false) patchGetter(navProto, 'webdriver', () => false); }
  patchIfDiff(nav, navProto, 'platform', CFG.platform);
  patchIfDiff(nav, navProto, 'hardwareConcurrency', CFG.hardwareConcurrency);
  if (G.isSecureContext && 'deviceMemory' in navProto) patchIfDiff(nav, navProto, 'deviceMemory', CFG.deviceMemory);
  const langs = Object.freeze(CFG.languages.slice());
  if (JSON.stringify(nav.languages) !== JSON.stringify(langs)) patchGetter(navProto, 'languages', () => langs);
  patchIfDiff(nav, navProto, 'language', langs[0]);
  if (IS_WIN) {
    patchIfDiff(nav, navProto, 'vendor', 'Google Inc.');
    patchIfDiff(nav, navProto, 'maxTouchPoints', 0);
    if ('pdfViewerEnabled' in navProto) patchIfDiff(nav, navProto, 'pdfViewerEnabled', true);
  }
}

// ---------------------------------------------------------------- WebGL / WebGL2 (window + OffscreenCanvas in workers)
for (const name of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
  const Ctor = G[name];
  if (!Ctor || !CFG.webgl.patch) continue;  // patch=false: real GPU strings adopted into the profile, leave native
  patchMethod(Ctor.prototype, 'getParameter', function (orig, args) {
    const p = args[0];
    const r = orig.apply(this, args);   // native call first: keeps GL error state / receiver checks natural
    if (p === 37445) return r === null ? r : CFG.webgl.vendor;
    if (p === 37446) return r === null ? r : CFG.webgl.renderer;
    const o = CFG.webgl.params && CFG.webgl.params[String(p)];
    if (o !== undefined && r !== null) return Array.isArray(o) ? new Int32Array(o) : o;
    return r;
  });
}

if (IS_WORKER) { /* worker scope stops here: no DOM-only surfaces */ return; }

// ---------------------------------------------------------------- worker-scope propagation (blob:/data: workers)
// addInitScript does not reach Workers, and the CDP auto-attach path races Playwright's own resume of
// the paused worker (best effort only).  Blob/data workers -- the pattern fingerprinting libraries use --
// are patched deterministically by prepending the worker-scope patch to their source.  Same-origin
// http(s) workers are not rewritten (a blob re-host would change location/relative URL resolution).
if (CFG.workerPrelude) {
  const WPRE = CFG.workerPrelude;
  for (const name of ['Worker', 'SharedWorker']) {
    const Orig = G[name];
    if (!Orig) continue;
    const P = new Proxy(Orig, {
      construct(target, args, nt) {
        try {
          const raw = args[0];
          const u = String(raw instanceof URL ? raw.href : raw);
          if (/^(blob|data):/i.test(u)) {
            const x = new XMLHttpRequest();
            x.open('GET', u, false); x.send();
            if (x.status === 200 || x.status === 0) {
              const nu = URL.createObjectURL(new Blob([WPRE + ';' + x.responseText], { type: 'text/javascript' }));
              args = [nu].concat(args.slice(1));
            }
          }
        } catch (e) {}
        return Reflect.construct(target, args, nt === P ? target : nt);
      }
    });
    Object.defineProperty(G, name, { value: P, writable: true, configurable: true, enumerable: false });
  }
}

// ---------------------------------------------------------------- window.chrome
if (!window.chrome) {
  Object.defineProperty(window, 'chrome', { value: {}, writable: true, configurable: true, enumerable: true });
}
const chromeObj = window.chrome;
if (!chromeObj.app) {
  const app = {
    isInstalled: false,
    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    getDetails: mask(({ getDetails() { return null; } }).getDetails, 'getDetails'),
    getIsInstalled: mask(({ getIsInstalled() { return false; } }).getIsInstalled, 'getIsInstalled'),
    installState: mask(({ installState(cb) { if (cb) cb('not_installed'); } }).installState, 'installState'),
    runningState: mask(({ runningState() { return 'cannot_run'; } }).runningState, 'runningState'),
  };
  chromeObj.app = app;
}
if (!chromeObj.loadTimes) {
  const t0 = Date.now() / 1000;
  chromeObj.loadTimes = mask(({ loadTimes() {
    const nav = (performance.getEntriesByType && performance.getEntriesByType('navigation')[0]) || {};
    const base = (performance.timeOrigin || Date.now()) / 1000;
    const at = (v) => base + (v || 0) / 1000;
    const fp = (performance.getEntriesByName && performance.getEntriesByName('first-paint')[0]);
    return {
      requestTime: at(nav.startTime), startLoadTime: at(nav.startTime), commitLoadTime: at(nav.responseStart),
      finishDocumentLoadTime: at(nav.domContentLoadedEventEnd), finishLoadTime: at(nav.loadEventEnd),
      firstPaintTime: fp ? at(fp.startTime) : t0, firstPaintAfterLoadTime: 0,
      navigationType: 'Other', wasFetchedViaSpdy: true, wasNpnNegotiated: true,
      npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false, connectionInfo: 'h2' };
  } }).loadTimes, 'loadTimes');
}
if (!chromeObj.csi) {
  chromeObj.csi = mask(({ csi() {
    const nav = (performance.getEntriesByType && performance.getEntriesByType('navigation')[0]) || {};
    const base = Math.round(performance.timeOrigin || Date.now());
    return { onloadT: Math.round(base + (nav.loadEventEnd || 0)), startE: base,
             pageT: performance.now(), tran: 15 };
  } }).csi, 'csi');
}
if (CFG.chromeRuntime && !chromeObj.runtime && location.protocol === 'https:') {
  const rt = {
    OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
    OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
    PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
    PlatformOs: { ANDROID: 'android', CROS: 'cros', FUCHSIA: 'fuchsia', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
    RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
    connect: mask(({ connect() { throw new TypeError("Error in invocation of runtime.connect(optional string extensionId, optional object connectInfo): chrome.runtime.connect() called from a webpage must specify an Extension ID (string) for its first argument."); } }).connect, 'connect'),
    sendMessage: mask(({ sendMessage() { throw new TypeError("Error in invocation of runtime.sendMessage(optional string extensionId, any message, optional object options, optional function callback): chrome.runtime.sendMessage() called from a webpage must specify an Extension ID (string) for its first argument."); } }).sendMessage, 'sendMessage'),
  };
  chromeObj.runtime = rt;
}

// ---------------------------------------------------------------- plugins / mimeTypes (only when natively empty: old headless)
try {
  if (G.PluginArray && navigator.plugins.length === 0) {
    const defs = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer', 'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'];
    const mimeDefs = [['application/pdf', 'pdf', 'Portable Document Format'], ['text/pdf', 'pdf', 'Portable Document Format']];
    const pStore = new WeakMap(), mStore = new WeakMap(), paStore = new WeakMap(), maStore = new WeakMap();
    const plugins = defs.map((n) => Object.create(Plugin.prototype));
    const mimes = mimeDefs.map(() => Object.create(MimeType.prototype));
    const pa = Object.create(PluginArray.prototype), ma = Object.create(MimeTypeArray.prototype);
    plugins.forEach((p, i) => {
      pStore.set(p, { name: defs[i], filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: mimes.length });
      mimes.forEach((m, j) => Object.defineProperty(p, String(j), { value: m, enumerable: true, configurable: true }));
      Object.defineProperty(p, mimeDefs[0][0], { value: mimes[0], enumerable: false, configurable: true });
      Object.defineProperty(p, mimeDefs[1][0], { value: mimes[1], enumerable: false, configurable: true });
    });
    mimes.forEach((m, j) => {
      mStore.set(m, { type: mimeDefs[j][0], suffixes: mimeDefs[j][1], description: mimeDefs[j][2], enabledPlugin: plugins[0] });
    });
    paStore.set(pa, { length: plugins.length }); maStore.set(ma, { length: mimes.length });
    plugins.forEach((p, i) => { Object.defineProperty(pa, String(i), { value: p, enumerable: true, configurable: true });
      Object.defineProperty(pa, defs[i], { value: p, enumerable: false, configurable: true }); });
    mimes.forEach((m, j) => { Object.defineProperty(ma, String(j), { value: m, enumerable: true, configurable: true });
      Object.defineProperty(ma, mimeDefs[j][0], { value: m, enumerable: false, configurable: true }); });
    shimGetters(Plugin.prototype, pStore, ['name', 'filename', 'description', 'length']);
    shimGetters(MimeType.prototype, mStore, ['type', 'suffixes', 'description', 'enabledPlugin']);
    shimGetters(PluginArray.prototype, paStore, ['length']);
    shimGetters(MimeTypeArray.prototype, maStore, ['length']);
    patchMethod(PluginArray.prototype, 'item', function (orig, a) { return paStore.has(this) ? (plugins[a[0] >>> 0] || null) : orig.apply(this, a); });
    patchMethod(PluginArray.prototype, 'namedItem', function (orig, a) { return paStore.has(this) ? (this[a[0]] || null) : orig.apply(this, a); });
    patchMethod(PluginArray.prototype, 'refresh', function (orig, a) { return paStore.has(this) ? undefined : orig.apply(this, a); });
    patchMethod(MimeTypeArray.prototype, 'item', function (orig, a) { return maStore.has(this) ? (mimes[a[0] >>> 0] || null) : orig.apply(this, a); });
    patchMethod(MimeTypeArray.prototype, 'namedItem', function (orig, a) { return maStore.has(this) ? (this[a[0]] || null) : orig.apply(this, a); });
    patchMethod(Plugin.prototype, 'item', function (orig, a) { return pStore.has(this) ? (mimes[a[0] >>> 0] || null) : orig.apply(this, a); });
    patchMethod(Plugin.prototype, 'namedItem', function (orig, a) { return pStore.has(this) ? (this[a[0]] || null) : orig.apply(this, a); });
    patchMethod(PluginArray.prototype, Symbol.iterator, function (orig, a) { return paStore.has(this) ? plugins[Symbol.iterator]() : orig.apply(this, a); });
    patchGetter(navProto, 'plugins', () => pa);
    patchGetter(navProto, 'mimeTypes', () => ma);
  }
} catch (e) {}

// ---------------------------------------------------------------- permissions / notifications consistency
try {
  if (G.Notification) patchGetter(Notification, 'permission', () => 'default');
  if (G.Permissions && G.PermissionStatus) {
    const permStore = new WeakMap();
    shimGetters(PermissionStatus.prototype, permStore, ['state']);
    patchMethod(Permissions.prototype, 'query', function (orig, args) {
      const p = orig.apply(this, args);
      const d = args[0];
      if (d && d.name === 'notifications') {
        return p.then((r) => { if (r.state === 'denied') permStore.set(r, { state: 'prompt' }); return r; });
      }
      return p;
    });
  }
} catch (e) {}

// ---------------------------------------------------------------- screen / window geometry
try {
  const s = CFG.screen;
  const sp = G.Screen && Screen.prototype;
  if (sp) {
    patchIfDiff(screen, sp, 'availWidth', s.availWidth);
    patchIfDiff(screen, sp, 'availHeight', s.availHeight);
    if ('availTop' in sp) patchIfDiff(screen, sp, 'availTop', s.availTop);
    if ('availLeft' in sp) patchIfDiff(screen, sp, 'availLeft', s.availLeft);
    patchIfDiff(screen, sp, 'colorDepth', s.colorDepth);
    patchIfDiff(screen, sp, 'pixelDepth', s.colorDepth);
  }
  // headless windows have outer == inner; a real maximised window has browser chrome around it
  if (window.outerHeight - window.innerHeight < 20) {
    patchGetter(window, 'outerHeight', () => window.innerHeight + CFG.uiHeight);
    patchGetter(window, 'outerWidth', () => window.innerWidth);
  }
} catch (e) {}

// ---------------------------------------------------------------- media codecs (open-source Chromium lacks H.264/AAC)
try {
  const PROPRIETARY = /avc1|mp4a|h\.?264|aac|mp3|^(audio\/mpeg|audio\/aac|video\/mp4|audio\/mp4|audio\/x-m4a|video\/x-m4v)/i;
  patchMethod(HTMLMediaElement.prototype, 'canPlayType', function (orig, args) {
    const r = orig.apply(this, args);
    if (r === '' && typeof args[0] === 'string' && PROPRIETARY.test(args[0]))
      return /codecs\s*=/.test(args[0]) ? 'probably' : 'maybe';
    return r;
  });
  if (G.MediaSource) patchMethod(MediaSource, 'isTypeSupported', function (orig, args) {
    const r = orig.apply(this, args);
    return r || (typeof args[0] === 'string' && PROPRIETARY.test(args[0]));
  });
} catch (e) {}

// ---------------------------------------------------------------- connection / battery / mediaDevices
try {
  if (G.NetworkInformation) {
    const c = CFG.connection;
    for (const k of ['effectiveType', 'rtt', 'downlink', 'saveData']) patchIfDiff(navigator.connection, NetworkInformation.prototype, k, c[k]);
  }
  if (navigator.getBattery && G.BatteryManager) {
    const bStore = new WeakMap();
    const bm = new EventTarget(); Object.setPrototypeOf(bm, BatteryManager.prototype);
    bStore.set(bm, { charging: CFG.battery.charging, level: CFG.battery.level, chargingTime: CFG.battery.chargingTime,
                     dischargingTime: CFG.battery.dischargingTime === null ? Infinity : CFG.battery.dischargingTime });
    shimGetters(BatteryManager.prototype, bStore, ['charging', 'level', 'chargingTime', 'dischargingTime']);
    patchMethod(Navigator.prototype, 'getBattery', function () { return Promise.resolve(bm); });
  }
  if (navigator.mediaDevices && G.MediaDeviceInfo) {
    const dStore = new WeakMap();
    const mk = (kind) => { const o = Object.create(MediaDeviceInfo.prototype); dStore.set(o, { deviceId: '', kind, label: '', groupId: '' }); return o; };
    const fakeDevs = [mk('audioinput'), mk('videoinput'), mk('audiooutput')];
    shimGetters(MediaDeviceInfo.prototype, dStore, ['deviceId', 'kind', 'label', 'groupId']);
    patchMethod(MediaDevices.prototype, 'enumerateDevices', function (orig, args) {
      return orig.apply(this, args).then((l) => (l.length === 0 ? fakeDevs.slice() : l));
    });
  }
} catch (e) {}

// ---------------------------------------------------------------- fonts
// Deliberately NOT patched: document.fonts.check() natively returns true for unknown families, and
// width-measurement font probes read the *host's* real fonts.  Profile.fonts is informational; keep the
// claimed OS equal to the host OS (default) so the real font set is consistent with the UA.

// ---------------------------------------------------------------- canvas / audio noise (deterministic per seed)
// Opt-out via noise=False: strict lie-detectors (CreepJS) flag every patched canvas/audio API.
try { if (CFG.noise) {
  const noisy = (data) => {
    for (let i = 0; i < data.length; i += 4) {
      let h = hash32((i >> 2) ^ SEED);
      if (h % 61 === 0 && data[i + 3] !== 0) {
        const ch = h % 3, v = data[i + ch] + (((h >>> 8) & 1) ? 1 : -1);
        data[i + ch] = v < 0 ? 0 : v > 255 ? 255 : v;
      }
    }
  };
  const C2D = G.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype;
  const origGID = C2D && C2D.getImageData;
  const origCreate = Document.prototype.createElement;
  if (C2D) patchMethod(C2D, 'getImageData', function (orig, args) { const r = orig.apply(this, args); try { noisy(r.data); } catch (e) {} return r; });
  const withNoise = (canvas, fn) => {
    try {
      const w = canvas.width, h = canvas.height;
      if (!w || !h || w * h > 4e6 || !origGID) return fn(canvas);
      const tmp = origCreate.call(document, 'canvas'); tmp.width = w; tmp.height = h;
      const ctx = tmp.getContext('2d'); ctx.drawImage(canvas, 0, 0);
      const id = origGID.call(ctx, 0, 0, w, h); noisy(id.data); ctx.putImageData(id, 0, 0);
      return fn(tmp);
    } catch (e) { return fn(canvas); }
  };
  if (G.HTMLCanvasElement) {
    patchMethod(HTMLCanvasElement.prototype, 'toDataURL', function (orig, args) { return withNoise(this, (c) => orig.apply(c, args)); });
    patchMethod(HTMLCanvasElement.prototype, 'toBlob', function (orig, args) { return withNoise(this, (c) => orig.apply(c, args)); });
  }
  if (G.AudioBuffer) {
    const seen = new WeakSet();
    const jitter = (i) => (((hash32(i ^ SEED) & 0xffff) / 65535) - 0.5) * 2e-7;
    patchMethod(AudioBuffer.prototype, 'getChannelData', function (orig, args) {
      const d = orig.apply(this, args);
      if (!seen.has(d)) { seen.add(d); for (let i = 0; i < d.length; i += 97) d[i] += jitter(i); }
      return d;
    });
    patchMethod(AudioBuffer.prototype, 'copyFromChannel', function (orig, args) {
      const r = orig.apply(this, args); const dst = args[0], off = args[2] || 0;
      for (let i = 0; i < dst.length; i++) if (((i + off) % 97) === 0) dst[i] += jitter(i + off);
      return r;
    });
  }
  if (G.AnalyserNode) {
    patchMethod(AnalyserNode.prototype, 'getFloatFrequencyData', function (orig, args) {
      const r = orig.apply(this, args); const a = args[0];
      for (let i = 0; i < a.length; i++) a[i] += jitter_f(i);
      return r;
    });
  }
  function jitter_f(i) { return (((hash32(i ^ SEED) & 0xffff) / 65535) - 0.5) * 1e-4; }
} } catch (e) {}

// ---------------------------------------------------------------- WebRTC leak block (relay-only ICE)
if (CFG.blockWebRTC) {
  try {
    for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection']) {
      const Orig = G[name];
      if (!Orig) continue;
      const P = new Proxy(Orig, {
        construct(target, args, nt) {
          const cfg = Object.assign({}, args[0] || {}, { iceTransportPolicy: 'relay' });
          return Reflect.construct(target, [cfg].concat(args.slice(1)), nt === P ? target : nt);
        },
        apply(target, thisArg, args) { return Reflect.apply(target, thisArg, args); }
      });
        Object.defineProperty(G, name, { value: P, writable: true, configurable: true, enumerable: false });
    }
  } catch (e) {}
}

// ---------------------------------------------------------------- fallback only: console CDP-serialisation guard
// Plain Playwright sends Runtime.enable, which makes DevTools serialise logged
// Error objects (touching .stack getters) -- a classic automation tell.  With
// patchright this is unnecessary because Runtime.enable is never sent.
if (CFG.consoleGuard) {
  try {
    for (const n of ['debug', 'log', 'info', 'warn', 'error', 'dir', 'table', 'trace', 'group', 'groupCollapsed']) {
      patchMethod(console, n, function (orig, args) {
        return orig.apply(this, args.map((a) => (a instanceof Error ? String(a) : a)));
      });
    }
  } catch (e) {}
}
})();
"""


def build_init_script(
    profile: Profile,
    *,
    scope: str = "window",
    block_webrtc: bool = True,
    chrome_runtime: bool = False,
    console_guard: bool = False,
    noise: bool = True,
) -> str:
    """Render the evasion script for ``profile``.

    Args:
        scope: ``"window"`` (full) or ``"worker"`` (navigator/WebGL subset for
            Dedicated/Service/Shared workers, evaluated via CDP auto-attach).
        block_webrtc: force relay-only ICE so host/srflx IPs cannot leak.
        chrome_runtime: define a ``chrome.runtime`` stub on https pages.
            Off by default: real Chrome without extensions leaves it undefined.
        noise: add deterministic (per profile seed) noise to canvas/audio readouts.  Defeats
            cross-session canvas/audio linking, but strict lie detectors flag the patched APIs;
            turn it off if you only care about not being flagged.
        console_guard: mitigation for ``Runtime.enable`` detection when running
            on plain Playwright (not needed with patchright).
    """
    if scope not in ("window", "worker"):
        raise ValueError("scope must be 'window' or 'worker'")
    cfg: Dict[str, Any] = profile.to_js_config()
    cfg.update(blockWebRTC=bool(block_webrtc), chromeRuntime=bool(chrome_runtime), consoleGuard=bool(console_guard), noise=bool(noise))
    if scope == "window":
        cfg["workerPrelude"] = build_init_script(profile, scope="worker", block_webrtc=False, noise=False)
    js = _JS.replace("__CFG__", json.dumps(cfg))
    if scope == "worker":
        # force worker branch irrespective of globals
        js = js.replace("const IS_WIN = typeof window !== 'undefined' && typeof document !== 'undefined' && G === window;",
                        "const IS_WIN = false;")
    return js


def cdp_user_agent_override(profile: Profile) -> Dict[str, Any]:
    """Params for ``Emulation/Network.setUserAgentOverride`` incl. UA-CH metadata."""
    return {
        "userAgent": profile.user_agent,
        "acceptLanguage": ",".join(profile.languages),
        "platform": profile.platform,
        "userAgentMetadata": profile.ua_metadata,
    }
