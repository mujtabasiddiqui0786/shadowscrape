"""HTML (lxml) to clean Markdown conversion, with a main-content heuristic."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from lxml import etree

_BR = ""
_SKIP = {
    "script", "style", "noscript", "template", "head", "title", "meta", "link", "svg", "canvas",
    "iframe", "object", "embed", "input", "select", "option", "textarea", "button", "datalist",
    "audio", "video", "map", "dialog",
}
_BLOCK = {
    "address", "article", "aside", "blockquote", "body", "dd", "details", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
    "table", "tbody", "thead", "tfoot", "tr", "td", "th", "ul", "center", "caption", "dir", "menu",
}
_WRAPPERS = {"a", "span", "font", "label", "strong", "b", "em", "i", "u", "small", "big", "mark", "cite"}
_NOISE_TAGS = {"nav", "aside", "footer", "form"}
_NOISE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
_NOISE_CLASS = re.compile(
    r"(?:^|[-_\s])(nav|navbar|navigation|menu|sidebar|footer|cookie|cookies|ads?|advert|advertisement|"
    r"promo|popup|modal|share|sharing|social|breadcrumbs?|newsletter|related|pagination)(?:$|[-_\s])",
    re.I,
)
_POSITIVE = re.compile(r"article|body|content|entry|main|page|post|text|blog|story", re.I)
_NEGATIVE = re.compile(r"comment|meta|footer|footnote|sidebar|share|social|promo|ad-|related|nav|menu", re.I)
_LANG = re.compile(r"(?:language|lang|highlight-source|brush:)[-_:\s]*([\w+#.-]+)", re.I)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_LIST_START = re.compile(r"(?:[-*+]|\d+[.)])\s")


def _is_el(el: Any) -> bool:
    return isinstance(getattr(el, "tag", None), str)


def _tag(el: Any) -> str:
    t = el.tag
    return t.rsplit("}", 1)[-1].lower() if isinstance(t, str) else ""


def _text_len(el: Any) -> int:
    n = 0
    for e in el.iter():
        if _is_el(e) and _tag(e) in ("script", "style"):
            continue
        if e.text and _is_el(e) and _tag(e) not in ("script", "style"):
            n += len(e.text.strip())
        if e.tail and e is not el:
            n += len(e.tail.strip())
    return n


def _link_density(el: Any) -> float:
    total = _text_len(el)
    if not total:
        return 0.0
    links = sum(_text_len(a) for a in el.iter("a"))
    return min(1.0, links / total)


def _classid(el: Any) -> str:
    return "%s %s" % (el.get("class", ""), el.get("id", ""))


def find_main_content(root: Any) -> Any:
    """Heuristically pick the element holding the page's main content."""
    body = root if _tag(root) == "body" else (root.find(".//body") if root.find(".//body") is not None else root)

    def biggest(nodes: List[Any], min_len: int = 50) -> Optional[Any]:
        best, bl = None, 0
        for n in nodes:
            if not _is_el(n):
                continue
            ln = _text_len(n)
            if ln >= min_len and ln > bl:
                best, bl = n, ln
        return best

    tiers = [
        "descendant-or-self::main | descendant-or-self::*[@role='main']",
        "descendant-or-self::article",
        "descendant-or-self::*[@id='content' or @id='main' or @id='main-content' or @id='article' "
        "or @id='post' or contains(@class,'post-content') or contains(@class,'article-body') "
        "or contains(@class,'entry-content') or contains(@class,'main-content') "
        "or contains(@class,'article-content')]",
    ]
    for q in tiers:
        found = biggest(root.xpath(q))
        if found is not None:
            return _with_title(found, body)

    # readability-lite scoring
    scores: Dict[Any, float] = {}
    for p in root.iter("p", "pre", "td", "blockquote"):
        txt = "".join(p.itertext()).strip()
        if len(txt) < 25:
            continue
        s = 1 + txt.count(",") + min(len(txt) // 100, 3)
        parent = p.getparent()
        grand = parent.getparent() if parent is not None else None
        for node, factor in ((parent, 1.0), (grand, 0.5)):
            if node is None or not _is_el(node) or _tag(node) in ("html",):
                continue
            if node not in scores:
                base = 0.0
                cid = _classid(node)
                if _POSITIVE.search(cid):
                    base += 25
                if _NEGATIVE.search(cid):
                    base -= 25
                if _tag(node) in ("div", "article", "section"):
                    base += 5
                scores[node] = base
            scores[node] += s * factor
    best, bs = None, 0.0
    for node, sc in scores.items():
        sc *= 1.0 - _link_density(node)
        if sc > bs:
            best, bs = node, sc
    return _with_title(best if best is not None else body, body)


def _with_title(node: Any, body: Any) -> Any:
    return node


class _Converter:
    def __init__(self, base_url: str = "", include_links: bool = True, include_images: bool = False,
                 strip_noise: bool = False):
        self.base_url = base_url
        self.include_links = include_links
        self.include_images = include_images
        self.strip_noise = strip_noise
        self._root: Any = None

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def _norm(s: str) -> str:
        s = s.replace("​", "").replace("﻿", "")
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"(?: *%s *)+" % _BR, lambda m: "  \n" if m.group(0).count(_BR) == 1 else "\n\n", s)
        return s.strip()

    def _skip(self, el: Any) -> bool:
        t = _tag(el)
        if t in _SKIP:
            return True
        if el is self._root:
            return False
        if el.get("hidden") is not None:
            return True
        st = el.get("style")
        if st and _HIDDEN_STYLE.search(st):
            return True
        if self.strip_noise:
            if t in _NOISE_TAGS or (el.get("role") or "").lower() in _NOISE_ROLES:
                return True
            if _NOISE_CLASS.search(el.get("class", "") or "") or _NOISE_CLASS.search(el.get("id", "") or ""):
                return True
        return False

    def _is_block(self, el: Any) -> bool:
        t = _tag(el)
        if t in _BLOCK:
            return True
        if t in _WRAPPERS:
            return any(_is_el(c) and _tag(c) in _BLOCK for c in el)
        return False

    # ---- block level ---------------------------------------------------
    def convert(self, root: Any) -> str:
        self._root = root
        if self._is_block(root):
            blocks = self.block(root)
        else:
            blocks = [self._norm(self.inline(root))]
        text = "\n\n".join(b for b in blocks if b and b.strip())
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def children_blocks(self, el: Any) -> List[str]:
        out: List[str] = []
        buf: List[str] = []

        def flush() -> None:
            s = self._norm("".join(buf))
            buf.clear()
            if s:
                out.append(s)

        if el.text:
            buf.append(el.text)
        for c in el:
            if _is_el(c) and not self._skip(c):
                if self._is_block(c):
                    flush()
                    out.extend(self.block(c))
                else:
                    buf.append(self.inline(c))
            if c.tail:
                buf.append(c.tail)
        flush()
        return out

    def block(self, el: Any) -> List[str]:
        t = _tag(el)
        if self._skip(el):
            return []
        if len(t) == 2 and t[0] == "h" and t[1] in "123456":
            txt = self._norm(self.inline_children(el))
            return ["#" * int(t[1]) + " " + txt.replace("\n", " ")] if txt else []
        if t in ("ul", "ol", "menu", "dir"):
            s = self._list(el, t == "ol")
            return [s] if s else []
        if t == "pre":
            return self._pre(el)
        if t == "hr":
            return ["---"]
        if t == "blockquote":
            inner = "\n\n".join(self.children_blocks(el))
            if not inner.strip():
                return []
            return ["\n".join(("> " + ln) if ln.strip() else ">" for ln in inner.split("\n"))]
        if t == "table":
            return self._table(el)
        if t in ("dt",):
            txt = self._norm(self.inline_children(el))
            return ["**%s**" % txt] if txt else []
        if t == "dd":
            return self.children_blocks(el)
        return self.children_blocks(el)

    def _list(self, el: Any, ordered: bool) -> str:
        try:
            n = int(el.get("start", "1")) if ordered else 1
        except ValueError:
            n = 1
        items: List[str] = []
        for li in el:
            if not _is_el(li) or self._skip(li):
                continue
            t = _tag(li)
            if t in ("ul", "ol"):
                nested = self._list(li, t == "ol")
                if nested:
                    nested = "\n".join("  " + ln for ln in nested.split("\n"))
                    if items:
                        items[-1] += "\n" + nested
                    else:
                        items.append(nested)
                continue
            blocks = self.children_blocks(li)
            if not blocks:
                continue
            marker = ("%d. " % n) if ordered else "- "
            n += 1
            body = blocks[0]
            for b in blocks[1:]:
                body += ("\n" if _LIST_START.match(b) else "\n\n") + b
            indent = " " * len(marker)
            lines = body.split("\n")
            items.append(marker + lines[0] + "".join("\n" + (indent + ln if ln.strip() else "") for ln in lines[1:]))
        return "\n".join(items)

    def _raw_text(self, el: Any) -> str:
        parts: List[str] = []
        for ev, e in etree.iterwalk(el, events=("start", "end")):
            if ev == "start":
                if _is_el(e):
                    if _tag(e) == "br":
                        parts.append("\n")
                    if e.text:
                        parts.append(e.text)
            elif e is not el and e.tail:
                parts.append(e.tail)
        return "".join(parts)

    def _pre(self, el: Any) -> List[str]:
        text = self._raw_text(el).replace("\r\n", "\n").strip("\n").rstrip()
        if not text.strip():
            return []
        lang = ""
        cands = [el] + [c for c in el.iter("code")][:1]
        for c in cands:
            m = _LANG.search(c.get("class", "") or "") or _LANG.search(c.get("data-lang", "") or "")
            if m:
                lang = m.group(1)
                break
            if c.get("data-lang"):
                lang = c.get("data-lang")
                break
        fence = "```"
        while fence in text:
            fence += "`"
        return ["%s%s\n%s\n%s" % (fence, lang, text, fence)]

    def _rows(self, el: Any) -> List[Any]:
        rows: List[Any] = []
        for c in el:
            if not _is_el(c):
                continue
            t = _tag(c)
            if t == "tr":
                rows.append(c)
            elif t in ("thead", "tbody", "tfoot"):
                rows.extend(self._rows(c))
        return rows

    def _cell(self, td: Any) -> str:
        txt = " ".join(b.replace("\n", " ") for b in self.children_blocks(td))
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt.replace("|", "\\|")

    def _table(self, el: Any) -> List[str]:
        rows = self._rows(el)
        grid: List[List[str]] = []
        cells_per_row: List[List[Any]] = []
        for tr in rows:
            cells = [c for c in tr if _is_el(c) and _tag(c) in ("td", "th") and not self._skip(c)]
            if cells:
                cells_per_row.append(cells)
        if not cells_per_row:
            return self.children_blocks(el)
        if max(len(r) for r in cells_per_row) <= 1:  # layout table
            out: List[str] = []
            for r in cells_per_row:
                out.extend(self.children_blocks(r[0]))
            return out
        for cells in cells_per_row:
            row: List[str] = []
            for c in cells:
                row.append(self._cell(c))
                try:
                    span = min(int(c.get("colspan", "1")), 20)
                except ValueError:
                    span = 1
                row.extend([""] * (span - 1))
            grid.append(row)
        ncols = max(len(r) for r in grid)
        for r in grid:
            r.extend([""] * (ncols - len(r)))
        lines = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * ncols) + " |"]
        for r in grid[1:]:
            lines.append("| " + " | ".join(r) + " |")
        out = []
        cap = el.find("caption")
        if cap is not None:
            ct = self._norm(self.inline_children(cap))
            if ct:
                out.append("**%s**" % ct)
        out.append("\n".join(lines))
        return out

    # ---- inline --------------------------------------------------------
    def inline_children(self, el: Any) -> str:
        parts: List[str] = []
        if el.text:
            parts.append(el.text)
        for c in el:
            if _is_el(c) and not self._skip(c):
                if self._is_block(c):
                    parts.append(" " + " ".join(self.children_blocks(c) if _tag(c) not in ("ul", "ol") else [self._list(c, _tag(c) == "ol")]) + " ")
                else:
                    parts.append(self.inline(c))
            if c.tail:
                parts.append(c.tail)
        return "".join(parts)

    @staticmethod
    def _wrap(inner: str, mark: str) -> str:
        m = re.match(r"(\s*)(.*?)(\s*)$", inner, re.S)
        lead, core, trail = m.group(1), m.group(2), m.group(3)
        if not core.strip():
            return inner
        return "%s%s%s%s%s" % (lead, mark, core, mark, trail)

    def inline(self, el: Any) -> str:
        t = _tag(el)
        if t == "br":
            return _BR
        if t == "img":
            if not self.include_images:
                return ""
            src = el.get("src") or el.get("data-src")
            if not src:
                return ""
            alt = (el.get("alt") or "").strip()
            return "![%s](%s)" % (alt, urljoin(self.base_url, src) if self.base_url else src)
        inner = self.inline_children(el)
        if t == "a":
            href = (el.get("href") or "").strip()
            if (self.include_links and href and not href.startswith("#")
                    and not href.lower().startswith(("javascript:",))):
                href = urljoin(self.base_url, href) if self.base_url else href
                m = re.match(r"(\s*)(.*?)(\s*)$", inner, re.S)
                core = m.group(2)
                if core.strip():
                    return "%s[%s](%s)%s" % (m.group(1), core, href.replace(" ", "%20").replace(")", "%29"), m.group(3))
            return inner
        if t in ("strong", "b"):
            return self._wrap(inner, "**")
        if t in ("em", "i"):
            return self._wrap(inner, "*")
        if t in ("del", "s", "strike"):
            return self._wrap(inner, "~~")
        if t in ("code", "kbd", "samp", "tt"):
            raw = re.sub(r"\s+", " ", "".join(el.itertext()))
            if not raw.strip():
                return inner
            tick = "`"
            while tick in raw:
                tick += "`"
            pad = " " if raw.startswith("`") or raw.endswith("`") else ""
            return "%s%s%s%s%s" % (tick, pad, raw.strip(), pad, tick)
        return inner


def to_markdown(
    element: Any,
    *,
    main_content_only: bool = False,
    include_links: bool = True,
    include_images: bool = False,
    base_url: str = "",
) -> str:
    """Convert an lxml element/document to Markdown.

    ``main_content_only`` picks the main article region and drops navigation, footers, ads, etc.
    """
    root = element.getroot() if hasattr(element, "getroot") else element
    if root is None or not _is_el(root):
        return ""
    target = find_main_content(root) if main_content_only else root
    conv = _Converter(base_url, include_links, include_images, strip_noise=main_content_only)
    try:
        return conv.convert(target)
    except RecursionError:
        return " ".join("".join(target.itertext()).split())
