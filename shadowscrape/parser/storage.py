"""sqlite3-backed storage for adaptive element fingerprints."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS fingerprints ("
    "domain TEXT NOT NULL, identifier TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL, "
    "PRIMARY KEY (domain, identifier))"
)


def default_storage_path() -> str:
    """Default db path: ``$SHADOWSCRAPE_ADAPTIVE_DB`` or ``~/.shadowscrape/adaptive.sqlite``."""
    env = os.environ.get("SHADOWSCRAPE_ADAPTIVE_DB")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".shadowscrape", "adaptive.sqlite")


class SQLiteStorage:
    """Stores lists of element fingerprints keyed by ``(domain, identifier)``."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._mem: Optional[sqlite3.Connection] = None
        if path == ":memory:":
            self._mem = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            d = os.path.dirname(os.path.abspath(path))
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self):
        # context manager yielding a connection (file dbs are opened per operation)
        storage = self

        class _Ctx:
            def __enter__(self_inner):
                storage._lock.acquire()
                if storage._mem is not None:
                    self_inner.c = storage._mem
                else:
                    self_inner.c = sqlite3.connect(storage.path, timeout=10)
                return self_inner.c

            def __exit__(self_inner, *exc):
                try:
                    if exc[0] is None:
                        self_inner.c.commit()
                    else:
                        self_inner.c.rollback()
                    if storage._mem is None:
                        self_inner.c.close()
                finally:
                    storage._lock.release()
                return False

        return _Ctx()

    def save(self, domain: str, identifier: str, fingerprints: List[Dict[str, Any]]) -> None:
        """Insert or replace the fingerprints stored under ``identifier``."""
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?)",
                (domain, identifier, json.dumps(fingerprints), time.time()),
            )

    def load(self, domain: str, identifier: str) -> Optional[List[Dict[str, Any]]]:
        """Return stored fingerprints or ``None``."""
        with self._conn() as c:
            row = c.execute(
                "SELECT data FROM fingerprints WHERE domain=? AND identifier=?", (domain, identifier)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, domain: str, identifier: str) -> None:
        """Remove an entry."""
        with self._conn() as c:
            c.execute("DELETE FROM fingerprints WHERE domain=? AND identifier=?", (domain, identifier))

    def identifiers(self, domain: Optional[str] = None) -> List[str]:
        """List stored identifiers (optionally for one domain)."""
        with self._conn() as c:
            if domain is None:
                rows = c.execute("SELECT identifier FROM fingerprints").fetchall()
            else:
                rows = c.execute("SELECT identifier FROM fingerprints WHERE domain=?", (domain,)).fetchall()
        return [r[0] for r in rows]


_registry: Dict[str, SQLiteStorage] = {}
_reg_lock = threading.Lock()


def get_storage(path: Optional[str] = None) -> SQLiteStorage:
    """Return a (cached) storage for ``path``; falls back to in-memory if the file is unusable."""
    p = str(path) if path else default_storage_path()
    with _reg_lock:
        st = _registry.get(p)
        if st is None:
            try:
                st = SQLiteStorage(p)
            except (sqlite3.Error, OSError):
                st = _registry.get(":memory:") or SQLiteStorage(":memory:")
                _registry[":memory:"] = st
            _registry[p] = st
        return st
