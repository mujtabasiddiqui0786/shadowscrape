"""Encoding detection and tolerant document parsing."""
from __future__ import annotations

import codecs
import re
from typing import List, Optional, Tuple

from lxml import etree

_META = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([-\w.:]+)", re.I)
_XMLDECL = re.compile(rb"^\s*<\?xml[^>]*encoding\s*=\s*[\"']([-\w.:]+)[\"']", re.I)
_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _norm(name: str) -> Optional[str]:
    try:
        return codecs.lookup(name).name
    except (LookupError, ValueError):
        return None


def decode_bytes(data: bytes, hint: Optional[str] = None) -> Tuple[str, str]:
    """Decode ``data`` to ``(text, encoding)`` using BOM, hint, meta/XML declaration, utf-8, cp1252."""
    candidates: List[str] = []
    if data.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        candidates.append("utf-32")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    if hint and hint.lower().replace("_", "-") not in ("utf-8", "utf8"):
        candidates.append(hint)
    head = data[:4096]
    for rx in (_XMLDECL, _META):
        m = rx.search(head)
        if m:
            candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for name in candidates:
        norm = _norm(name)
        if not norm:
            continue
        try:
            return data.decode(norm), ("utf-8" if norm == "utf-8-sig" else norm)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("cp1252", errors="replace"), "cp1252"


def clean_control_chars(text: str) -> str:
    """Remove control characters that libxml2 refuses."""
    return _CTRL.sub("", text)


def parse_document(text: str, xml: bool = False) -> etree._Element:
    """Parse ``text`` into an lxml element, never failing (empty documents give ``<html/>``)."""
    data = clean_control_chars(text).encode("utf-8", "replace")
    if xml:
        parser = etree.XMLParser(
            recover=True, resolve_entities=False, no_network=True, huge_tree=True,
            remove_comments=True, remove_pis=True, encoding="utf-8",
        )
    else:
        parser = etree.HTMLParser(
            recover=True, huge_tree=True, remove_comments=True, remove_pis=True, encoding="utf-8",
        )
    root = None
    if data.strip():
        try:
            root = etree.fromstring(data, parser)
        except (etree.XMLSyntaxError, ValueError, etree.LxmlError):
            root = None
    if root is None:
        root = etree.fromstring(b"<html></html>", etree.HTMLParser())
    return root
