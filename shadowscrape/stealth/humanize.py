"""Human-like input: bezier mouse paths with jitter, typing and scroll cadence.

The planning functions are pure (given an RNG) so they are unit-testable; the
:class:`Humanizer` / :class:`AsyncHumanizer` classes replay plans through a
Playwright ``page`` (sync / async).
"""
from __future__ import annotations

import math
import random
from typing import Any, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def _ease(t: float) -> float:
    """Smoothstep-ish ease in/out (slow start, fast middle, slow end)."""
    return t * t * (3 - 2 * t)


def bezier_path(
    start: Point,
    end: Point,
    rng: Optional[random.Random] = None,
    *,
    steps: Optional[int] = None,
    jitter: float = 0.8,
    overshoot: bool = True,
) -> List[Tuple[float, float, float]]:
    """Plan a mouse movement as ``[(x, y, dt_ms), ...]``.

    A cubic bezier with two random control points off the straight line, eased
    timing (slow-fast-slow), gaussian micro jitter that fades near the target
    and, for long moves, a small overshoot followed by a correction.
    """
    rng = rng or random.Random()
    (x0, y0), (x1, y1) = start, end
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 1:
        return [(x1, y1, 5.0)]
    if overshoot and dist > 250 and rng.random() < 0.6:
        ang = math.atan2(y1 - y0, x1 - x0)
        o = rng.uniform(4, 14)
        mid = (x1 + math.cos(ang) * o, y1 + math.sin(ang) * o)
        return (bezier_path(start, mid, rng, steps=steps, jitter=jitter, overshoot=False)
                + bezier_path(mid, end, rng, steps=max(4, int(o)), jitter=0.2, overshoot=False))
    n = steps or int(max(12, min(80, dist / 9 + rng.randint(-3, 3))))
    # control points: offset perpendicular to the line proportional to distance
    nx, ny = -(y1 - y0) / dist, (x1 - x0) / dist
    def ctrl(frac: float) -> Point:
        off = rng.uniform(-0.25, 0.25) * dist
        return (x0 + (x1 - x0) * frac + nx * off, y0 + (y1 - y0) * frac + ny * off)
    c1, c2 = ctrl(rng.uniform(0.2, 0.4)), ctrl(rng.uniform(0.6, 0.85))
    # Fitts-like duration: longer for far moves, 180..900 ms
    total = max(180.0, min(900.0, 120 + 140 * math.log2(1 + dist / 40))) * rng.uniform(0.85, 1.2)
    pts: List[Tuple[float, float, float]] = []
    for i in range(1, n + 1):
        t = _ease(i / n)
        mt = 1 - t
        x = mt ** 3 * x0 + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * x1
        y = mt ** 3 * y0 + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * y1
        fade = 1 - i / n
        x += rng.gauss(0, jitter) * fade
        y += rng.gauss(0, jitter) * fade
        if i == n:
            x, y = x1, y1
        # uniform time step; the eased *position* makes the cursor slow at the ends and fast mid-way
        pts.append((x, y, max(1.0, total / n + rng.uniform(-1.0, 2.0))))
    return pts


def typing_delays(text: str, rng: Optional[random.Random] = None, *, wpm: float = 190.0) -> List[float]:
    """Per-character delays in ms: log-normal base, slower after punctuation/space, rare hesitations."""
    rng = rng or random.Random()
    base = 60000.0 / (wpm * 5)  # ms per char
    out: List[float] = []
    prev = ""
    for ch in text:
        d = rng.lognormvariate(math.log(base), 0.35)
        if prev in " ,.;:!?\n":
            d *= rng.uniform(1.3, 2.2)
        if ch.isupper() or ch in "!@#$%^&*()_+{}|:\"<>?":
            d *= 1.4  # shift key
        if rng.random() < 0.02:
            d += rng.uniform(250, 700)  # brief hesitation
        out.append(max(25.0, d))
        prev = ch
    return out


def scroll_plan(distance: float, rng: Optional[random.Random] = None) -> List[Tuple[float, float]]:
    """Plan wheel events ``[(delta_y, dt_ms), ...]`` summing to ~``distance`` with inertial decay."""
    rng = rng or random.Random()
    sign = 1 if distance >= 0 else -1
    remaining = abs(distance)
    plan: List[Tuple[float, float]] = []
    delta = rng.uniform(80, 140)
    while remaining > 1:
        d = min(remaining, delta)
        plan.append((sign * round(d, 1), rng.uniform(12, 28)))
        remaining -= d
        delta *= rng.uniform(0.82, 0.95)
        if delta < 6:
            delta = 6
        if rng.random() < 0.04:  # flick pause (reading)
            plan.append((0.0, rng.uniform(200, 600)))
    return plan


class _Base:
    def __init__(self, page: Any, rng: Optional[random.Random] = None, start: Optional[Point] = None) -> None:
        self.page = page
        self.rng = rng or random.Random()
        vp = page.viewport_size or {"width": 1280, "height": 720}
        self.pos: Point = start or (self.rng.uniform(0.2, 0.8) * vp["width"], self.rng.uniform(0.2, 0.6) * vp["height"])

    def _target(self, x: float, y: float, spread: float = 3.0) -> Point:
        return (x + self.rng.gauss(0, spread), y + self.rng.gauss(0, spread))


class Humanizer(_Base):
    """Replays human-like input on a **sync** Playwright page."""

    def sleep(self, ms: float) -> None:
        self.page.wait_for_timeout(ms)

    def move_to(self, x: float, y: float) -> None:
        for px, py, dt in bezier_path(self.pos, (x, y), self.rng):
            self.page.mouse.move(px, py)
            if dt > 4:
                self.sleep(dt)
        self.pos = (x, y)

    def click(self, x: float, y: float, *, spread: float = 3.0) -> None:
        tx, ty = self._target(x, y, spread)
        self.move_to(tx, ty)
        self.sleep(self.rng.uniform(60, 220))  # hover dwell
        self.page.mouse.down()
        self.sleep(self.rng.uniform(45, 130))
        self.page.mouse.up()

    def type_text(self, text: str) -> None:
        for ch, d in zip(text, typing_delays(text, self.rng)):
            self.page.keyboard.type(ch)
            self.sleep(d)

    def scroll(self, distance: float) -> None:
        x, y = self.pos
        self.page.mouse.move(x, y)
        for dy, dt in scroll_plan(distance, self.rng):
            if dy:
                self.page.mouse.wheel(0, dy)
            self.sleep(dt)

    def idle(self, *, scroll: bool = True) -> None:
        """Light 'looked at the page' activity: a wander and a small scroll."""
        vp = self.page.viewport_size or {"width": 1280, "height": 720}
        self.move_to(self.rng.uniform(0.15, 0.85) * vp["width"], self.rng.uniform(0.15, 0.7) * vp["height"])
        if scroll:
            self.scroll(self.rng.uniform(120, 420))
            self.sleep(self.rng.uniform(150, 450))
            self.scroll(-self.rng.uniform(40, 200))


class AsyncHumanizer(_Base):
    """Replays human-like input on an **async** Playwright page."""

    async def sleep(self, ms: float) -> None:
        await self.page.wait_for_timeout(ms)

    async def move_to(self, x: float, y: float) -> None:
        for px, py, dt in bezier_path(self.pos, (x, y), self.rng):
            await self.page.mouse.move(px, py)
            if dt > 4:
                await self.sleep(dt)
        self.pos = (x, y)

    async def click(self, x: float, y: float, *, spread: float = 3.0) -> None:
        tx, ty = self._target(x, y, spread)
        await self.move_to(tx, ty)
        await self.sleep(self.rng.uniform(60, 220))
        await self.page.mouse.down()
        await self.sleep(self.rng.uniform(45, 130))
        await self.page.mouse.up()

    async def type_text(self, text: str) -> None:
        for ch, d in zip(text, typing_delays(text, self.rng)):
            await self.page.keyboard.type(ch)
            await self.sleep(d)

    async def scroll(self, distance: float) -> None:
        x, y = self.pos
        await self.page.mouse.move(x, y)
        for dy, dt in scroll_plan(distance, self.rng):
            if dy:
                await self.page.mouse.wheel(0, dy)
            await self.sleep(dt)

    async def idle(self, *, scroll: bool = True) -> None:
        vp = self.page.viewport_size or {"width": 1280, "height": 720}
        await self.move_to(self.rng.uniform(0.15, 0.85) * vp["width"], self.rng.uniform(0.15, 0.7) * vp["height"])
        if scroll:
            await self.scroll(self.rng.uniform(120, 420))
            await self.sleep(self.rng.uniform(150, 450))
            await self.scroll(-self.rng.uniform(40, 200))
