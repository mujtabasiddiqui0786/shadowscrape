"""``Selector`` / ``Selectors``: an lxml-backed HTML parsing and querying API."""
from __future__ import annotations

import json as _json
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse

from lxml import etree

from . import adaptive as _adaptive
from .css import css_to_xpath
from .encoding import decode_bytes, parse_document
from .markdown import to_markdown as _to_markdown
from .storage import get_storage
from .text import TextHandler, clean_whitespace, compile_pattern, regex_findall

_IGNORED_FOR_SIMILAR = ("href", "src")
_PLAIN_ID = re.compile(r"-?[A-Za-z_][\w-]*\Z")


def _is_el(el: Any) -> bool:
    return isinstance(getattr(el, "tag", None), str)


def _iter_texts(el: Any, ignore: Sequence[str]) -> Iterator[str]:
    """Yield text nodes of ``el`` in document order, skipping ``ignore`` subtrees (iteratively)."""
    ig = {t.lower() for t in ignore}
    walker = etree.iterwalk(el, events=("start", "end"))
    for ev, e in walker:
        if ev == "start":
            if not _is_el(e):
                continue
            if e.tag.lower() in ig:
                walker.skip_subtree()
                continue
            if e.text:
                yield e.text
        elif e is not el and e.tail:
            yield e.tail


class Selector:
    """A parsed HTML document or element with CSS/XPath/find/adaptive/Markdown helpers.

    ``Selector("<html>...")`` parses a document (``str`` or ``bytes`` with encoding detection);
    ``Selector(root=lxml_element)`` wraps an existing element.  ``text`` returns the element's own
    leading text (stripped); use :meth:`get_all_text` for the full text content.
    """

    def __init__(
        self,
        content: Union[str, bytes] = "",
        *,
        root: Any = None,
        url: str = "",
        adaptive: bool = False,
        storage_path: Optional[str] = None,
        encoding: str = "utf-8",
        xml: bool = False,
    ):
        self.url = url
        self.adaptive = adaptive
        self.storage_path = storage_path
        self.encoding = encoding
        self._xml = xml
        self._text_override: Optional[str] = None
        self._raw: Optional[str] = None
        if root is not None:
            if isinstance(root, etree._ElementTree):
                root = root.getroot()
            self._root = root
        else:
            if content is None:
                content = ""
            if isinstance(content, (bytes, bytearray, memoryview)):
                text, enc = decode_bytes(bytes(content), encoding)
                self.encoding = enc
            else:
                text = str(content)
            self._raw = text
            self._root = parse_document(text, xml)

    # ------------------------------------------------------------------ basics
    def _wrap(self, el: Any) -> "Selector":
        return Selector(root=el, url=self.url, adaptive=self.adaptive, storage_path=self.storage_path,
                        encoding=self.encoding, xml=self._xml)

    def _is_doc(self) -> bool:
        return self._root.getparent() is None

    @property
    def tag(self) -> str:
        """Element tag name."""
        return self._root.tag if _is_el(self._root) else ""

    @property
    def attrib(self) -> Dict[str, str]:
        """Copy of the element's attributes."""
        return {str(k): str(v) for k, v in self._root.attrib.items()}

    def get_attr(self, name: str, default: Any = None) -> Any:
        """Return attribute ``name`` or ``default``."""
        return self._root.get(name, default)

    def __getitem__(self, name: str) -> str:
        v = self._root.get(name)
        if v is None:
            raise KeyError(name)
        return v

    def has_class(self, name: str) -> bool:
        """True if the element has CSS class ``name``."""
        return name in (self._root.get("class") or "").split()

    @property
    def text(self) -> str:
        """Own leading text of the element, stripped (settable override for subclasses)."""
        if self._text_override is not None:
            return self._text_override
        return TextHandler((self._root.text or "").strip())

    @text.setter
    def text(self, value: str) -> None:
        self._text_override = value

    @property
    def html_content(self) -> str:
        """Outer HTML of the element."""
        return TextHandler(etree.tostring(self._root, encoding="unicode",
                                          method="xml" if self._xml else "html", with_tail=False))

    def prettify(self) -> str:
        """Indented HTML."""
        return etree.tostring(self._root, encoding="unicode", method="xml" if self._xml else "html",
                              pretty_print=True, with_tail=False)

    def __str__(self) -> str:
        return self.html_content

    def __repr__(self) -> str:
        h = self.html_content
        return "<Selector tag=%r data=%r>" % (self.tag, (h[:60] + "...") if len(h) > 60 else h)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Selector) and other._root is self._root

    def __hash__(self) -> int:
        return id(self._root)

    def get(self, default: Any = None) -> str:
        """Outer HTML string."""
        return self.html_content

    def getall(self) -> List[str]:
        """``[outer html]``."""
        return [self.html_content]

    extract = getall

    def extract_first(self, default: Any = None) -> str:
        """Alias of :meth:`get`."""
        return self.get(default)

    def get_all_text(self, separator: str = "\n", strip: bool = True,
                     ignore_tags: Sequence[str] = ("script", "style")) -> str:
        """Text content of the element and descendants joined with ``separator``."""
        pieces = _iter_texts(self._root, ignore_tags)
        if strip:
            pieces = (p.strip() for p in pieces)  # type: ignore[assignment]
            return TextHandler(separator.join(p for p in pieces if p))
        return TextHandler(separator.join(pieces))

    def _direct_texts(self, el: Any = None) -> str:
        el = self._root if el is None else el
        parts = [el.text or ""] + [c.tail or "" for c in el]
        return " ".join(p for p in parts if p)

    # -------------------------------------------------------------- navigation
    @property
    def parent(self) -> Optional["Selector"]:
        """Parent element or ``None``."""
        p = self._root.getparent()
        return self._wrap(p) if p is not None and _is_el(p) else None

    @property
    def children(self) -> "Selectors":
        """Child elements."""
        return Selectors(self._wrap(c) for c in self._root if _is_el(c))

    @property
    def siblings(self) -> "Selectors":
        """Other children of the same parent."""
        p = self._root.getparent()
        if p is None:
            return Selectors()
        return Selectors(self._wrap(c) for c in p if _is_el(c) and c is not self._root)

    @property
    def next_sibling(self) -> Optional["Selector"]:
        """Next sibling element or ``None``."""
        n = self._root.getnext()
        while n is not None and not _is_el(n):
            n = n.getnext()
        return self._wrap(n) if n is not None else None

    @property
    def previous_sibling(self) -> Optional["Selector"]:
        """Previous sibling element or ``None``."""
        n = self._root.getprevious()
        while n is not None and not _is_el(n):
            n = n.getprevious()
        return self._wrap(n) if n is not None else None

    next = next_sibling
    previous = previous_sibling

    @property
    def ancestors(self) -> "Selectors":
        """Ancestor elements, nearest first."""
        return Selectors(self._wrap(a) for a in self._root.iterancestors() if _is_el(a))

    @property
    def path(self) -> List[str]:
        """Tags of the ancestors, from the document root down to the parent."""
        return [str(a.tag) for a in reversed(list(self._root.iterancestors())) if _is_el(a)]

    def find_ancestor(self, func: Callable[["Selector"], bool]) -> Optional["Selector"]:
        """First ancestor (nearest first) for which ``func`` is true."""
        for a in self.ancestors:
            if func(a):
                return a
        return None

    # ------------------------------------------------------------------- query
    def _run_xpath(self, query: str, **variables: Any) -> "Selectors":
        raw = self._root.xpath(query, **variables)
        if not isinstance(raw, list):
            raw = [raw]
        out = Selectors()
        for r in raw:
            if isinstance(r, etree._Element):
                if _is_el(r):
                    out.append(self._wrap(r))
            elif isinstance(r, bytes):
                out.append(TextHandler(r.decode("utf-8", "replace")))
            else:
                out.append(TextHandler(str(r)))
        return out

    def _adaptive_flow(self, results: "Selectors", expr: str, identifier: Optional[str],
                       auto_save: bool, adaptive: bool) -> "Selectors":
        ident = identifier or expr
        if results:
            if auto_save:
                els = [r for r in results if isinstance(r, Selector)]
                if els:
                    self.save(els, ident)
            return results
        if adaptive or self.adaptive:
            if self.has_saved(ident):
                return self.relocate(ident)
        return results

    def css(self, selector: str, *, identifier: Optional[str] = None, auto_save: bool = False,
            adaptive: bool = False) -> "Selectors":
        """Select with CSS (supports ``::text`` and ``::attr(name)``).

        ``auto_save=True`` stores the matched elements' fingerprints under ``identifier`` (default:
        the selector).  ``adaptive=True`` relocates the saved elements by similarity when the
        selector matches nothing (e.g. after a page redesign).
        """
        xp = css_to_xpath(selector, self._xml, self._is_doc())
        return self._adaptive_flow(self._run_xpath(xp), selector, identifier, auto_save, adaptive)

    def css_first(self, selector: str, **kw: Any) -> Optional["Selector"]:
        """First CSS match or ``None``."""
        res = self.css(selector, **kw)
        return res[0] if res else None

    def xpath(self, query: str, *, identifier: Optional[str] = None, auto_save: bool = False,
              adaptive: bool = False, **variables: Any) -> "Selectors":
        """Select with XPath; extra keyword arguments are XPath variables (``$name``)."""
        return self._adaptive_flow(self._run_xpath(query, **variables), query, identifier, auto_save, adaptive)

    def xpath_first(self, query: str, **kw: Any) -> Optional["Selector"]:
        """First XPath match or ``None``."""
        res = self.xpath(query, **kw)
        return res[0] if res else None

    # ---- find / find_all
    @staticmethod
    def _attr_matches(el: Any, name: str, want: Any) -> bool:
        val = el.get(name)
        if want is True:
            return val is not None
        if want is False or want is None:
            return val is None
        if val is None:
            return False
        if callable(want) and not isinstance(want, re.Pattern):
            return bool(want(val))
        if isinstance(want, re.Pattern):
            return bool(want.search(val))
        if isinstance(want, (list, tuple, set, frozenset)):
            return any(Selector._attr_matches(el, name, w) for w in want)
        want = str(want)
        if name == "class":
            return set(want.split()) <= set(val.split())
        return val == want

    def _build_filters(self, args: Tuple[Any, ...], kwargs: Dict[str, Any]):
        tags: List[str] = []
        attrs: Dict[str, Any] = {}
        funcs: List[Callable[[Selector], bool]] = []
        regexes: List[Any] = []
        for a in args:
            if isinstance(a, str):
                tags.append(a)
            elif isinstance(a, re.Pattern):
                regexes.append(a)
            elif isinstance(a, dict):
                attrs.update(a)
            elif isinstance(a, (list, tuple, set, frozenset)):
                tags.extend(str(x) for x in a)
            elif callable(a):
                funcs.append(a)
            else:
                raise TypeError("unsupported find argument: %r" % (a,))
        for k, v in kwargs.items():
            k = k[:-1] if k.endswith("_") else k
            attrs[k.replace("_", "-") if k != "class" else k] = v
        tags_l = {t.lower() for t in tags}
        return tags_l, attrs, funcs, regexes

    def find_all(self, *args: Any, **kwargs: Any) -> "Selectors":
        """Find descendants by tag name(s), attribute dict/kwargs, regex (on text) or function.

        ``class_`` / ``data_x`` kwargs map to ``class`` / ``data-x``.  Attribute values may be a
        string (``class`` matches by token containment), regex, list (any of), callable or
        ``True``/``False`` (present/absent).  All given criteria must match.
        """
        tags, attrs, funcs, regexes = self._build_filters(args, kwargs)
        out = Selectors()
        for el in self._root.iterdescendants():
            if not _is_el(el):
                continue
            if tags and "*" not in tags and el.tag.lower() not in tags:
                continue
            if any(not self._attr_matches(el, k, v) for k, v in attrs.items()):
                continue
            if regexes:
                txt = " ".join(_iter_texts(el, ("script", "style")))
                if not all(r.search(txt) for r in regexes):
                    continue
            sel = self._wrap(el)
            if any(not f(sel) for f in funcs):
                continue
            out.append(sel)
        return out

    def find(self, *args: Any, **kwargs: Any) -> Optional["Selector"]:
        """First result of :meth:`find_all` or ``None``."""
        res = self.find_all(*args, **kwargs)
        return res[0] if res else None

    def _text_matcher(self, test: Callable[[str], bool], first_match: bool, clean_match: bool):
        out = Selectors()
        for el in self._root.iterdescendants():
            if not _is_el(el) or el.tag in ("script", "style"):
                continue
            txt = self._direct_texts(el)
            if not txt.strip():
                continue
            if clean_match:
                txt = clean_whitespace(txt)
            if test(txt):
                sel = self._wrap(el)
                if first_match:
                    return sel
                out.append(sel)
        return None if first_match else out

    def find_by_text(self, text: str, *, first_match: bool = True, partial: bool = False,
                     case_sensitive: bool = False, clean_match: bool = True):
        """Find elements whose own text nodes equal (or contain, with ``partial``) ``text``."""
        needle = clean_whitespace(text) if clean_match else text
        if not case_sensitive:
            needle = needle.lower()

        def test(t: str) -> bool:
            t = t if case_sensitive else t.lower()
            return (needle in t) if partial else (t == needle)

        return self._text_matcher(test, first_match, clean_match)

    def find_by_regex(self, pattern: Any, *, first_match: bool = True, case_sensitive: bool = False,
                      clean_match: bool = True):
        """Find elements whose own text matches regex ``pattern`` (search semantics)."""
        rx = compile_pattern(pattern, case_sensitive)
        return self._text_matcher(lambda t: bool(rx.search(t)), first_match, clean_match)

    # ---- regex on text
    def re(self, pattern: Any, replace_entities: bool = True, clean_match: bool = False,
           case_sensitive: bool = True) -> List[TextHandler]:
        """Regex over the element's text content (groups flattened, entities unescaped)."""
        return regex_findall(str(self.get_all_text(separator=" ")), pattern, replace_entities,
                             clean_match, case_sensitive)

    def re_first(self, pattern: Any, default: Any = None, **kw: Any) -> Any:
        """First :meth:`re` match or ``default``."""
        res = self.re(pattern, **kw)
        return res[0] if res else default

    # ---- similar elements
    def find_similar(self, similarity_threshold: float = 0.2,
                     ignore_attributes: Sequence[str] = _IGNORED_FOR_SIMILAR,
                     match_text: bool = False) -> "Selectors":
        """Find elements like this one: same tag and ancestor path, attribute similarity >= threshold."""
        me = self._root
        ignore = set(ignore_attributes)
        my_path = self.path
        my_attrs = {k: v for k, v in me.attrib.items() if k not in ignore}
        my_text = clean_whitespace(self._direct_texts())
        root = me.getroottree().getroot()
        out = Selectors()
        for el in root.iter(me.tag):
            if el is me or not _is_el(el):
                continue
            if [str(a.tag) for a in reversed(list(el.iterancestors()))] != my_path:
                continue
            attrs = {k: v for k, v in el.attrib.items() if k not in ignore}
            sc = _adaptive._attr_sim(my_attrs, attrs)
            if match_text:
                sc = (sc + _adaptive._ratio(my_text, clean_whitespace(self._direct_texts(el)))) / 2
            if sc >= similarity_threshold:
                out.append(self._wrap(el))
        return out

    # ----------------------------------------------------------------- adaptive
    def _domain(self) -> str:
        return urlparse(self.url).netloc.lower() or "default"

    def has_saved(self, identifier: str) -> bool:
        """True if fingerprints exist for ``identifier`` (for this URL's domain)."""
        return get_storage(self.storage_path).load(self._domain(), identifier) is not None

    def save(self, element: Union["Selector", Sequence["Selector"]], identifier: str) -> None:
        """Save fingerprint(s) of ``element`` (a Selector or Selectors) under ``identifier``."""
        els = [element] if isinstance(element, Selector) else [e for e in element if isinstance(e, Selector)]
        if not els:
            return
        get_storage(self.storage_path).save(self._domain(), identifier,
                                            [_adaptive.fingerprint(e._root) for e in els])

    def relocate(self, element_or_saved: Any, *, threshold: float = _adaptive.DEFAULT_THRESHOLD) -> "Selectors":
        """Find saved element(s) again by similarity scoring (tag, text, attributes, ancestors, siblings).

        ``element_or_saved`` is an identifier string, a Selector/Selectors (fingerprinted now), or
        raw fingerprint dict(s).  Returns the best distinct match per fingerprint scoring at least
        ``threshold`` (0-100).
        """
        if isinstance(element_or_saved, str):
            fps = get_storage(self.storage_path).load(self._domain(), element_or_saved) or []
        elif isinstance(element_or_saved, Selector):
            fps = [_adaptive.fingerprint(element_or_saved._root)]
        elif isinstance(element_or_saved, dict):
            fps = [element_or_saved]
        else:
            fps = [e if isinstance(e, dict) else _adaptive.fingerprint(e._root) for e in element_or_saved]
        if not fps:
            return Selectors()
        cands = self._root.iter() if self._is_doc() else self._root.iterdescendants()
        return Selectors(self._wrap(e) for e in _adaptive.relocate(cands, fps, threshold))

    def forget(self, identifier: str) -> None:
        """Delete saved fingerprints for ``identifier``."""
        get_storage(self.storage_path).delete(self._domain(), identifier)

    # ------------------------------------------------------- generated selectors
    @property
    def generate_css_selector(self) -> str:
        """A CSS selector that uniquely identifies this element in its document."""
        parts: List[str] = []
        cur = self._root
        tree = cur.getroottree()
        while cur is not None and _is_el(cur):
            tag = str(cur.tag)
            id_ = cur.get("id")
            if id_ and len(tree.xpath("//*[@id=$i]", i=id_)) == 1:
                if _PLAIN_ID.match(id_):
                    parts.append("%s#%s" % (tag, id_))
                else:
                    esc = id_.replace("\\", "\\\\").replace('"', '\\"')
                    parts.append('%s[id="%s"]' % (tag, esc))
                break
            parent = cur.getparent()
            seg = tag
            if parent is not None:
                same = [s for s in parent if s.tag == cur.tag]
                if len(same) > 1:
                    seg = "%s:nth-of-type(%d)" % (tag, next(i for i, s in enumerate(same) if s is cur) + 1)
            parts.append(seg)
            cur = parent
        return " > ".join(reversed(parts))

    @property
    def generate_xpath_selector(self) -> str:
        """An absolute XPath (``//*[@id=..]`` shortcut when the id is unique)."""
        el = self._root
        tree = el.getroottree()
        id_ = el.get("id")
        if id_ and len(tree.xpath("//*[@id=$i]", i=id_)) == 1:
            if '"' not in id_:
                return '//*[@id="%s"]' % id_
            if "'" not in id_:
                return "//*[@id='%s']" % id_
        return tree.getpath(el)

    # --------------------------------------------------------------------- misc
    def json(self) -> Any:
        """Parse JSON from the source (documents) or from the element's text."""
        cands: List[str] = []
        if self._is_doc() and self._raw is not None:
            cands.append(self._raw)
        cands.append(str(self.text))
        cands.append(str(self._root.text or ""))
        cands.append(str(self.get_all_text(separator="")))
        err: Optional[Exception] = None
        for c in cands:
            if not c or not c.strip():
                continue
            try:
                return _json.loads(c)
            except ValueError as e:
                err = e
        raise err or ValueError("no JSON content found")

    def to_markdown(self, *, main_content_only: bool = False, include_links: bool = True,
                    include_images: bool = False) -> str:
        """Convert to clean Markdown (headings, lists, tables, links, code blocks).

        ``main_content_only`` keeps the main article region and drops nav/footer/ads/sidebars.
        """
        return _to_markdown(self._root, main_content_only=main_content_only, include_links=include_links,
                            include_images=include_images, base_url=self.url)


class Selectors(list):
    """A list of :class:`Selector` (or text results) with the query API mapped over members."""

    def __getitem__(self, idx):  # type: ignore[override]
        r = super().__getitem__(idx)
        return Selectors(r) if isinstance(idx, slice) else r

    @property
    def first(self) -> Any:
        """First member or ``None``."""
        return self[0] if self else None

    @property
    def last(self) -> Any:
        """Last member or ``None``."""
        return self[-1] if self else None

    def _sels(self) -> List[Selector]:
        return [s for s in self if isinstance(s, Selector)]

    def css(self, selector: str, **kw: Any) -> "Selectors":
        """``Selector.css`` over every member, flattened."""
        out = Selectors()
        for s in self._sels():
            out.extend(s.css(selector, **kw))
        return out

    def css_first(self, selector: str, **kw: Any) -> Optional[Selector]:
        """First CSS match across members."""
        res = self.css(selector, **kw)
        return res[0] if res else None

    def xpath(self, query: str, **kw: Any) -> "Selectors":
        """``Selector.xpath`` over every member, flattened."""
        out = Selectors()
        for s in self._sels():
            out.extend(s.xpath(query, **kw))
        return out

    def find_all(self, *a: Any, **kw: Any) -> "Selectors":
        """``Selector.find_all`` over every member, flattened."""
        out = Selectors()
        for s in self._sels():
            out.extend(s.find_all(*a, **kw))
        return out

    def find(self, *a: Any, **kw: Any) -> Optional[Selector]:
        """First :meth:`find_all` result."""
        res = self.find_all(*a, **kw)
        return res[0] if res else None

    def _mapped_find(self, name: str, first_match: bool, *a: Any, **kw: Any):
        out = Selectors()
        for s in self._sels():
            r = getattr(s, name)(*a, first_match=first_match, **kw)
            if first_match:
                if r is not None:
                    return r
            else:
                out.extend(r)
        return None if first_match else out

    def find_by_text(self, text: str, *, first_match: bool = True, **kw: Any):
        """``Selector.find_by_text`` mapped over members."""
        return self._mapped_find("find_by_text", first_match, text, **kw)

    def find_by_regex(self, pattern: Any, *, first_match: bool = True, **kw: Any):
        """``Selector.find_by_regex`` mapped over members."""
        return self._mapped_find("find_by_regex", first_match, pattern, **kw)

    def re(self, pattern: Any, **kw: Any) -> List[TextHandler]:
        """Regex matches over all members, flattened."""
        out: List[TextHandler] = []
        for s in self:
            out.extend(s.re(pattern, **kw))
        return out

    def re_first(self, pattern: Any, default: Any = None, **kw: Any) -> Any:
        """First regex match over all members."""
        res = self.re(pattern, **kw)
        return res[0] if res else default

    def get(self, default: Any = None) -> Any:
        """First member's ``get()`` (outer HTML or text) or ``default``."""
        return self[0].get() if self else default

    def getall(self) -> List[str]:
        """``get()`` of every member."""
        return [s.get() for s in self]

    extract = getall

    def extract_first(self, default: Any = None) -> Any:
        """Alias of :meth:`get`."""
        return self.get(default)

    def filter(self, func: Callable[[Any], bool]) -> "Selectors":  # type: ignore[override]
        """Members for which ``func`` is truthy."""
        return Selectors(s for s in self if func(s))

    def search(self, func: Callable[[Any], bool]) -> Optional[Any]:
        """First member for which ``func`` is truthy."""
        for s in self:
            if func(s):
                return s
        return None

    def to_markdown(self, **kw: Any) -> str:
        """Markdown of all element members joined by blank lines."""
        return "\n\n".join(s.to_markdown(**kw) for s in self._sels())
