"""Crawl statistics collector."""
from __future__ import annotations

from typing import Any, Dict


class StatsCollector:
    """Tiny counter/gauge store used by the crawl engine."""

    def __init__(self, initial: Dict[str, Any] | None = None) -> None:
        self._d: Dict[str, Any] = dict(initial or {})

    def inc(self, key: str, n: float = 1) -> None:
        self._d[key] = self._d.get(key, 0) + n

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value

    def max(self, key: str, value: float) -> None:
        if key not in self._d or value > self._d[key]:
            self._d[key] = value

    def get(self, key: str, default: Any = 0) -> Any:
        return self._d.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._d)
