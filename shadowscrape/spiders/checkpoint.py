"""Checkpoint persistence for pause/resume."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

FILENAME = "checkpoint.json"
VERSION = 1


def path_for(crawldir: str) -> str:
    return os.path.join(crawldir, FILENAME)


def save(crawldir: str, state: Dict[str, Any]) -> str:
    """Atomically write the checkpoint file."""
    os.makedirs(crawldir, exist_ok=True)
    target = path_for(crawldir)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dict(state, version=VERSION), fh, ensure_ascii=False, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def load(crawldir: str) -> Optional[Dict[str, Any]]:
    target = path_for(crawldir)
    if not os.path.exists(target):
        return None
    with open(target, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    return state if state.get("version") == VERSION else None


def remove(crawldir: str) -> None:
    try:
        os.remove(path_for(crawldir))
    except FileNotFoundError:
        pass
