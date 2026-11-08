"""Text helpers: ``TextHandler`` (a ``str`` with regex/JSON helpers) and regex utilities."""
from __future__ import annotations

import html as _html
import json as _json
import re
from typing import Any, List, Optional, Union

_WS = re.compile(r"\s+")

Pattern = Union[str, "re.Pattern[str]"]


def clean_whitespace(text: str) -> str:
    """Collapse all whitespace runs into single spaces and strip the ends."""
    return _WS.sub(" ", text).strip()


def compile_pattern(pattern: Pattern, case_sensitive: bool = True) -> "re.Pattern[str]":
    """Compile ``pattern`` unless it already is a compiled regex."""
    if isinstance(pattern, re.Pattern):
        return pattern
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)


def regex_findall(
    text: str,
    pattern: Pattern,
    replace_entities: bool = True,
    clean_match: bool = False,
    case_sensitive: bool = True,
) -> List["TextHandler"]:
    """Run ``pattern`` over ``text``.

    With capture groups every non-empty group of every match is returned (flattened);
    without groups the whole match is returned.
    """
    if clean_match:
        text = clean_whitespace(text)
    rx = compile_pattern(pattern, case_sensitive)
    out: List[TextHandler] = []
    for m in rx.finditer(text):
        if rx.groups:
            vals = [g for g in m.groups() if g is not None]
        else:
            vals = [m.group(0)]
        for v in vals:
            out.append(TextHandler(_html.unescape(v) if replace_entities else v))
    return out


class TextHandler(str):
    """A ``str`` subclass returned for text/attribute results."""

    def get(self, default: Any = None) -> "TextHandler":
        """Return the string itself (mirrors ``Selector.get``)."""
        return self

    def getall(self) -> List["TextHandler"]:
        """Return ``[self]`` (mirrors ``Selector.getall``)."""
        return [self]

    def clean(self) -> "TextHandler":
        """Collapse whitespace and strip."""
        return TextHandler(clean_whitespace(self))

    def re(
        self,
        pattern: Pattern,
        replace_entities: bool = True,
        clean_match: bool = False,
        case_sensitive: bool = True,
    ) -> List["TextHandler"]:
        """Return all regex matches (see ``regex_findall``)."""
        return regex_findall(str(self), pattern, replace_entities, clean_match, case_sensitive)

    def re_first(self, pattern: Pattern, default: Any = None, **kw: Any) -> Any:
        """Return the first regex match or ``default``."""
        res = self.re(pattern, **kw)
        return res[0] if res else default

    def json(self) -> Any:
        """Parse the string as JSON."""
        return _json.loads(str(self))
