"""CrawlResult and exporters (JSON, JSONL, CSV, XML) incl. incremental JSONL."""
from __future__ import annotations

import csv
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Union

PathLike = Union[str, "os.PathLike[str]"]


def _plain(item: Any) -> Any:
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    return item


def _prep(path: PathLike) -> Path:
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _dumps(obj: Any, **kw: Any) -> str:
    return json.dumps(_plain(obj), ensure_ascii=False, default=str, **kw)


def export_json(items: Iterable[Any], path: PathLike, indent: Optional[int] = 2) -> Path:
    p = _prep(path)
    p.write_text(_dumps(list(items), indent=indent), encoding="utf-8")
    return p


def export_jsonl(items: Iterable[Any], path: PathLike) -> Path:
    p = _prep(path)
    with open(p, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(_dumps(it) + "\n")
    return p


def export_csv(items: Iterable[Any], path: PathLike, fields: Optional[Sequence[str]] = None) -> Path:
    rows = [_plain(i) if isinstance(_plain(i), dict) else {"value": i} for i in items]
    cols: List[str] = list(fields) if fields else []
    if not fields:
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
    p = _prep(path)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([_csv_cell(r.get(c)) for c in cols])
    return p


def _csv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        return _dumps(v)
    return str(v)


_BAD = re.compile(r"[^\w.-]", re.U)


def _tag(name: Any) -> str:
    t = _BAD.sub("_", str(name)) or "_"
    if not (t[0].isalpha() or t[0] == "_"):
        t = "_" + t
    return t


_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _fill(elem: ET.Element, value: Any) -> None:
    value = _plain(value)
    if value is None:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _fill(ET.SubElement(elem, _tag(k)), v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _fill(ET.SubElement(elem, "value"), v)
    elif isinstance(value, bool):
        elem.text = "true" if value else "false"
    else:
        elem.text = _XML_ILLEGAL.sub("", str(value))


def export_xml(items: Iterable[Any], path: PathLike, root_tag: str = "items", item_tag: str = "item") -> Path:
    root = ET.Element(root_tag)
    for it in items:
        _fill(ET.SubElement(root, item_tag), it)
    if hasattr(ET, "indent"):
        ET.indent(root)
    p = _prep(path)
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
    return p


class JSONLWriter:
    """Incremental JSON-lines writer (flushes every item; usable as context manager)."""

    def __init__(self, path: PathLike, append: bool = False) -> None:
        self.path = _prep(path)
        self._fh = open(self.path, "a" if append else "w", encoding="utf-8")
        self.count = 0

    def write(self, item: Any) -> None:
        self._fh.write(_dumps(item) + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "JSONLWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@dataclass
class CrawlResult:
    """Outcome of a crawl: scraped items, stats and whether it was paused."""

    items: List[Any] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    paused: bool = False

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.items)

    def to_json(self, path: PathLike, indent: Optional[int] = 2) -> Path:
        return export_json(self.items, path, indent)

    def to_jsonl(self, path: PathLike) -> Path:
        return export_jsonl(self.items, path)

    def to_csv(self, path: PathLike, fields: Optional[Sequence[str]] = None) -> Path:
        return export_csv(self.items, path, fields)

    def to_xml(self, path: PathLike, root_tag: str = "items", item_tag: str = "item") -> Path:
        return export_xml(self.items, path, root_tag, item_tag)
