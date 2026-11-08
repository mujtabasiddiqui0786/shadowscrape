"""Adaptive element tracking: fingerprints and similarity-based relocation."""
from __future__ import annotations

import re
from functools import lru_cache
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DEFAULT_THRESHOLD = 35.0
_WS = re.compile(r"\s+")
_SKIP_TAGS = {"script", "style", "head", "meta", "link", "noscript", "title"}
TEXT_CAP = 300

WEIGHTS = {
    "tag": 8.0, "text": 30.0, "attrs": 25.0, "path": 8.0,
    "parent": 10.0, "siblings": 8.0, "children": 4.0, "index": 7.0,
}


def _is_el(el: Any) -> bool:
    return isinstance(getattr(el, "tag", None), str)


def _direct_text(el: Any) -> str:
    parts = [el.text or ""]
    for c in el:
        parts.append(c.tail or "")
    return _WS.sub(" ", " ".join(parts)).strip()[:TEXT_CAP]


def _snippet(el: Any, cap: int = TEXT_CAP) -> str:
    """First ``cap`` characters of the element's text content (lazy, so cheap on huge subtrees)."""
    out: List[str] = []
    n = 0
    for t in el.itertext():
        out.append(t)
        n += len(t)
        if n >= cap * 3:
            break
    return _WS.sub(" ", " ".join(out)).strip()[:cap]


def features(el: Any) -> Dict[str, Any]:
    """Compute the structural/textual feature dict of an lxml element (JSON-serialisable)."""
    parent = el.getparent()
    attrs = {str(k): str(v) for k, v in el.attrib.items()}
    sibs: List[str] = []
    index = 0
    parent_info = None
    if parent is not None and _is_el(parent):
        # bounded neighbourhood (3 each side) keeps this O(1) on huge flat lists
        prev, nxt = [], []
        n = el.getprevious()
        while n is not None and len(prev) < 3:
            if _is_el(n):
                prev.append(str(n.tag))
            n = n.getprevious()
        n = el.getnext()
        while n is not None and len(nxt) < 3:
            if _is_el(n):
                nxt.append(str(n.tag))
            n = n.getnext()
        sibs = prev[::-1] + nxt
        n = el.getprevious()
        while n is not None and index < 50:
            if _is_el(n):
                index += 1
            n = n.getprevious()
        parent_info = {"tag": str(parent.tag), "attrs": {str(k): str(v) for k, v in parent.attrib.items()}}
    return {
        "v": 1,
        "tag": str(el.tag),
        "text": _direct_text(el),
        "full_text": _snippet(el),
        "attrs": attrs,
        "path": [str(a.tag) for a in reversed(list(el.iterancestors())) if _is_el(a)],
        "parent": parent_info,
        "siblings": sibs,
        "children": [str(c.tag) for c in el if _is_el(c)],
        "index": index,
    }


def fingerprint(el: Any) -> Dict[str, Any]:
    """Return the fingerprint saved for an element."""
    return features(el)


@lru_cache(maxsize=65536)
def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.2 or sm.quick_ratio() < 0.2:
        return 0.0
    return sm.ratio()


def _text_sim(a: str, b: str) -> float:
    if not a and not b:
        return 0.5
    return _ratio(a, b)


def _value_sim(key: str, a: str, b: str) -> float:
    if a == b:
        return 1.0
    if key == "class":
        sa, sb = set(a.split()), set(b.split())
        jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0
        return max(jac, 0.5 * _ratio(a, b))
    return _ratio(a[:200], b[:200])


def _attr_sim(a: Dict[str, str], b: Dict[str, str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    return sum(_value_sim(k, a[k], b[k]) for k in keys if k in a and k in b) / len(keys)


@lru_cache(maxsize=65536)
def _seq_sim_cached(a: Tuple[str, ...], b: Tuple[str, ...]) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _seq_sim(a: Sequence[str], b: Sequence[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return _seq_sim_cached(tuple(a), tuple(b))


def score(fp: Dict[str, Any], cand: Dict[str, Any]) -> float:
    """Similarity of two feature dicts on a 0..100 scale."""
    s = 0.0
    s += WEIGHTS["tag"] * (1.0 if fp["tag"] == cand["tag"] else 0.0)
    own = _text_sim(fp.get("text", ""), cand["text"])
    full = _text_sim(fp.get("full_text", ""), cand["full_text"])
    s += WEIGHTS["text"] * (0.5 * own + 0.5 * full)
    s += WEIGHTS["attrs"] * _attr_sim(fp.get("attrs", {}), cand["attrs"])
    s += WEIGHTS["path"] * _seq_sim(fp.get("path", []), cand["path"])
    pa, pb = fp.get("parent"), cand.get("parent")
    if pa and pb:
        psim = 0.4 * (pa["tag"] == pb["tag"]) + 0.6 * _attr_sim(pa["attrs"], pb["attrs"])
    else:
        psim = 1.0 if (pa is None and pb is None) else 0.0
    s += WEIGHTS["parent"] * psim
    s += WEIGHTS["siblings"] * _seq_sim(fp.get("siblings", []), cand["siblings"])
    s += WEIGHTS["children"] * _seq_sim(fp.get("children", []), cand["children"])
    s += WEIGHTS["index"] * (1.0 - min(abs(fp.get("index", 0) - cand["index"]), 5) / 5.0)
    return s


def relocate(
    candidates: Iterable[Any],
    fps: List[Dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> List[Any]:
    """Find, for each fingerprint, its best distinct matching element among ``candidates``.

    Returns matched lxml elements in fingerprint order (unmatched fingerprints are omitted).
    """
    wanted_tags = {fp["tag"] for fp in fps}
    cands = [c for c in candidates if _is_el(c) and (c.tag not in _SKIP_TAGS or c.tag in wanted_tags)]
    feats = [features(c) for c in cands]
    triples = []
    for fi, fp in enumerate(fps):
        for ci, cf in enumerate(feats):
            sc = score(fp, cf)
            if sc >= threshold:
                triples.append((-sc, ci, fi))
    triples.sort()
    used_c: Set[int] = set()
    used_f: Set[int] = set()
    assigned: Dict[int, int] = {}
    for negsc, ci, fi in triples:
        if ci in used_c or fi in used_f:
            continue
        used_c.add(ci)
        used_f.add(fi)
        assigned[fi] = ci
    return [cands[assigned[fi]] for fi in sorted(assigned)]
