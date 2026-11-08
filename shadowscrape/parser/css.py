"""CSS to XPath translation with ``::text`` and ``::attr(name)`` pseudo-elements."""
from __future__ import annotations

from functools import lru_cache

from cssselect import GenericTranslator, HTMLTranslator
from cssselect.parser import FunctionalPseudoElement
from cssselect.xpath import ExpressionError
from cssselect.xpath import XPathExpr as _BaseExpr


class _XPathExpr(_BaseExpr):
    textnode = False
    attribute = None

    @classmethod
    def from_xpath(cls, xpath, textnode=False, attribute=None):
        x = cls(path=xpath.path, element=xpath.element, condition=xpath.condition)
        x.textnode = textnode
        x.attribute = attribute
        return x

    def __str__(self):
        path = super().__str__()
        if self.textnode:
            if path == "*":
                path = "text()"
            elif path.endswith("::*/*"):
                path = path[:-3] + "text()"
            else:
                path += "/text()"
        if self.attribute is not None:
            if path.endswith("::*/*"):
                path = path[:-2]
            path += "/@" + self.attribute
        return path


class _Mixin:
    xpathexpr_cls = _XPathExpr

    def xpath_element(self, selector):
        return _XPathExpr.from_xpath(super().xpath_element(selector))

    def xpath_pseudo_element(self, xpath, pseudo_element):
        if isinstance(pseudo_element, FunctionalPseudoElement):
            name = "xpath_%s_functional_pseudo_element" % pseudo_element.name.replace("-", "_")
            method = getattr(self, name, None)
            if method is None:
                raise ExpressionError("Unsupported pseudo-element ::%s()" % pseudo_element.name)
            return method(xpath, pseudo_element)
        name = "xpath_%s_simple_pseudo_element" % str(pseudo_element).replace("-", "_")
        method = getattr(self, name, None)
        if method is None:
            raise ExpressionError("Unsupported pseudo-element ::%s" % pseudo_element)
        return method(xpath)

    def xpath_attr_functional_pseudo_element(self, xpath, function: FunctionalPseudoElement):
        if function.argument_types() not in (["STRING"], ["IDENT"]):
            raise ExpressionError("Expected a single string or ident for ::attr(), got %r" % function.arguments)
        return _XPathExpr.from_xpath(xpath, attribute=function.arguments[0].value)

    def xpath_text_simple_pseudo_element(self, xpath):
        return _XPathExpr.from_xpath(xpath, textnode=True)


class _HTMLTranslator(_Mixin, HTMLTranslator):
    pass


class _XMLTranslator(_Mixin, GenericTranslator):
    pass


_html = _HTMLTranslator()
_xml = _XMLTranslator()


@lru_cache(maxsize=1024)
def css_to_xpath(selector: str, xml: bool = False, include_self: bool = False) -> str:
    """Translate a CSS selector (with ``::text``/``::attr()``) to XPath."""
    prefix = "descendant-or-self::" if include_self else "descendant::"
    return (_xml if xml else _html).css_to_xpath(selector, prefix=prefix)
