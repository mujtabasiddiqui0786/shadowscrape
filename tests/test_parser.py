"""Offline tests for shadowscrape.parser."""
from __future__ import annotations

import re

import pytest

from shadowscrape.parser import Adaptor, Selector, Selectors, TextHandler

HTML = """
<html><head><title>Shop</title><meta charset="utf-8">
<script>var x = "<p>hidden</p>";</script><style>.a{}</style></head>
<body>
  <div id="main" class="container wide">
    <h1 class="title">Products</h1>
    <ul class="items">
      <li class="item" data-id="1"><a href="/p/1">Widget</a> <span class="price">$10</span></li>
      <li class="item" data-id="2"><a href="/p/2">Gadget</a> <span class="price">$20</span></li>
      <li class="item special" data-id="3"><a href="/p/3">Gizmo</a> <span class="price">$30</span></li>
    </ul>
    <p class="note">Call us: <b>555-1234</b> or mail <i>a@b.com</i>.</p>
    <!-- a comment -->
  </div>
  <div id="footer"><p>Copyright 2024</p></div>
</body></html>
"""


@pytest.fixture()
def page():
    return Selector(HTML, url="https://shop.example.com/list")


# ---------------------------------------------------------------- css/xpath
def test_css_basic_and_pseudo(page):
    assert len(page.css("li.item")) == 3
    assert page.css("h1::text").get() == "Products"
    assert page.css("li a::attr(href)").getall() == ["/p/1", "/p/2", "/p/3"]
    assert page.css("li.item::attr(data-id)").getall() == ["1", "2", "3"]
    assert page.css(".price::text").getall() == ["$10", "$20", "$30"]
    assert page.css("html").first.tag == "html"
    assert page.css_first("nothing") is None
    assert page.css("li:nth-child(2) a::text")[0] == "Gadget"
    assert page.css("ul > li, h1")[0].tag in ("h1", "li")


def test_css_scoped_to_element(page):
    ul = page.css_first("ul.items")
    assert len(ul.css("li")) == 3
    assert ul.css("ul") == []  # descendants only, not self
    assert page.css("div li").first.css_first("a").text == "Widget"


def test_xpath(page):
    assert page.xpath("//li[@data-id='2']/a/text()").get() == "Gadget"
    assert len(page.xpath("//li")) == 3
    assert page.xpath("count(//li)")[0] == "3.0"
    assert page.xpath("//li[@data-id=$i]/a/text()", i="3").get() == "Gizmo"
    assert page.xpath("//li/@data-id").getall() == ["1", "2", "3"]


def test_text_results_are_textboxes(page):
    t = page.css(".price::text").first
    assert isinstance(t, TextHandler)
    assert t.re_first(r"\d+") == "10"
    assert page.css(".price::text").re(r"\$(\d+)") == ["10", "20", "30"]


# ------------------------------------------------------------- properties
def test_properties(page):
    li = page.css_first("li.special")
    assert li.tag == "li"
    assert li.attrib == {"class": "item special", "data-id": "3"}
    assert li.has_class("special") and not li.has_class("spec")
    assert li["data-id"] == "3"
    assert "Gizmo" in li.html_content and li.html_content.startswith("<li")
    assert li.url == "https://shop.example.com/list"
    assert li.path == ["html", "body", "div", "ul"]
    assert li.get_all_text(separator=" ") == "Gizmo $30"
    assert li.css_first("span").text == "$30"
    assert li.text == ""  # own leading text only


def test_get_all_text_ignores_script_style(page):
    txt = page.get_all_text()
    assert "hidden" not in txt and ".a{}" not in txt and "Products" in txt
    assert "var x" in page.get_all_text(ignore_tags=())
    raw = page.css_first("p.note").get_all_text(strip=False, separator="")
    assert raw.startswith("Call us: 555-1234 or mail")
    assert page.css_first("p.note").get_all_text(separator=" ") == "Call us: 555-1234 or mail a@b.com ."


def test_navigation(page):
    li = page.css("li.item")[1]
    assert li.parent.tag == "ul"
    assert len(li.parent.children) == 3
    assert len(li.siblings) == 2
    assert li.next_sibling.attrib["data-id"] == "3"
    assert li.previous_sibling.attrib["data-id"] == "1"
    assert page.css("li")[0].previous_sibling is None
    assert page.css("li")[2].next_sibling is None
    assert page.css_first("html").parent is None
    assert li.ancestors[0].tag == "ul"
    assert li.find_ancestor(lambda a: a.attrib.get("id") == "main").tag == "div"
    # comment nodes must not appear as siblings/children
    note = page.css_first("p.note")
    assert note.next_sibling is None


def test_get_getall_and_selectors_api(page):
    items = page.css("li.item")
    assert isinstance(items, Selectors)
    assert items.first.attrib["data-id"] == "1" and items.last.attrib["data-id"] == "3"
    assert items[:2].__class__ is Selectors and len(items[:2]) == 2
    assert items.get().startswith("<li")
    assert len(items.getall()) == 3
    assert items.css("a::text").getall() == ["Widget", "Gadget", "Gizmo"]
    assert items.xpath(".//span/text()").getall() == ["$10", "$20", "$30"]
    assert items.re(r"\$(\d+)") == ["10", "20", "30"]
    assert items.re_first(r"Gi\w+") == "Gizmo"
    assert Selectors().get("dflt") == "dflt" and Selectors().first is None
    assert len(items.filter(lambda e: e.has_class("special"))) == 1
    assert items.search(lambda e: e.has_class("special")).attrib["data-id"] == "3"
    assert items.find_by_text("Gadget").tag == "a"
    assert items.find("span").text == "$10"


# ------------------------------------------------------------------ find
def test_find_all_variants(page):
    assert len(page.find_all("li")) == 3
    assert len(page.find_all("li", "span")) == 6
    assert len(page.find_all(["h1", "p"])) == 3
    assert len(page.find_all({"class": "item"})) == 3
    assert len(page.find_all("li", class_="special")) == 1
    assert len(page.find_all(class_="item special")) == 1
    assert len(page.find_all("li", data_id="2")) == 1
    assert len(page.find_all("a", href=re.compile(r"/p/[12]$"))) == 2
    assert len(page.find_all("li", **{"data-id": ["1", "3"]})) == 2
    assert len(page.find_all("li", data_id=lambda v: int(v) > 1)) == 2
    assert len(page.find_all("li", class_="special", data_id=True)) == 1
    assert len(page.find_all("li", special=False)) == 3
    assert len(page.find_all(lambda e: e.tag == "span" and e.text == "$20")) == 1
    assert len(page.find_all(re.compile(r"Giz"))) >= 1  # regex on text
    assert page.find("table") is None
    assert page.find("a").text == "Widget"
    assert page.find_all("li", class_="nope") == []


def test_find_by_text_and_regex(page):
    assert page.find_by_text("Gadget").attrib["href"] == "/p/2"
    assert page.find_by_text("gadget").tag == "a"
    assert page.find_by_text("gadget", case_sensitive=True) is None
    assert page.find_by_text("Gad") is None
    assert page.find_by_text("Gad", partial=True).tag == "a"
    assert len(page.find_by_text("$", partial=True, first_match=False)) == 3
    assert page.find_by_text("nope", first_match=False) == []
    assert page.find_by_regex(r"^\$2\d$").attrib["class"] == "price"
    assert len(page.find_by_regex(r"\$\d+", first_match=False)) == 3
    assert page.find_by_regex(re.compile("COPYRIGHT", re.I)).tag == "p"
    ws = Selector("<p>  Hello \n   World  </p>")
    assert ws.find_by_text("hello world").tag == "p"
    assert ws.find_by_text("hello world", clean_match=False) is None


def test_re_and_re_first(page):
    note = page.css_first("p.note")
    assert note.re(r"\d{3}-\d{4}") == ["555-1234"]
    assert note.re_first(r"(\w+)@(\w+)\.com") == "a"
    assert note.re(r"(\w+)@(\w+)\.com") == ["a", "b"]
    assert note.re_first(r"zzz", default="none") == "none"
    assert note.re(r"MAIL", case_sensitive=False) == ["mail"]
    assert Selector("<p>a &amp; b</p>").re(r"a .*") == ["a & b"]
    assert Selector("<p>a &amp;amp; b</p>").re(r"a .*", replace_entities=False) == ["a &amp; b"]
    assert Selector("<p>a   \n b</p>").re(r"a b", clean_match=True) == ["a b"]


# ------------------------------------------------------------ find_similar
def test_find_similar(page):
    first = page.css_first("li.item")
    sim = first.find_similar()
    assert len(sim) == 2
    assert {s.attrib["data-id"] for s in sim} == {"2", "3"}
    assert page.css_first("h1").find_similar() == []
    # stricter threshold excludes the "special" one (class differs)
    strict = first.find_similar(similarity_threshold=0.9, ignore_attributes=("href", "src", "data-id"))
    assert {s.attrib["data-id"] for s in strict} == {"2"}
    # text matching
    t = Selector("<ul><li>apple</li><li>apple</li><li>zzzzzzzz</li></ul>").css_first("li")
    assert len(t.find_similar(match_text=True, similarity_threshold=0.9)) == 1
    # elements at a different depth/path are not similar
    nested = Selector("<div><p class=x>a</p><section><p class=x>b</p></section></div>").css_first("p")
    assert nested.find_similar() == []


# --------------------------------------------------------------- generated
def test_generated_selectors_roundtrip(page):
    for el in list(page.css("li")) + list(page.css("a")) + [page.css_first("p.note"), page.css_first("h1")]:
        css = el.generate_css_selector
        found = page.css(css)
        assert len(found) == 1 and found[0] == el, css
        xp = el.generate_xpath_selector
        found = page.xpath(xp)
        assert len(found) == 1 and found[0] == el, xp
    assert page.css_first("#main").generate_css_selector == "div#main"
    assert page.css_first("#main").generate_xpath_selector == '//*[@id="main"]'
    odd = Selector('<div><p id="a b">x</p><p id="a b">y</p></div>').css("p")[1]
    assert page_roundtrip(odd)


def page_roundtrip(el):
    doc = el._root.getroottree().getroot()
    s = Selector(root=doc)
    return s.css(el.generate_css_selector)[0] == el and s.xpath(el.generate_xpath_selector)[0] == el


# ------------------------------------------------------------------ json
def test_json():
    s = Selector('<html><body><script id="d" type="application/json">{"a": [1, 2], "b": "x"}</script></body></html>')
    assert s.css_first("#d").json() == {"a": [1, 2], "b": "x"}
    assert Selector('{"k": 1}').json() == {"k": 1}  # JSON document body
    assert Selector("<p>[1,2]</p>").css_first("p").json() == [1, 2]
    with pytest.raises(ValueError):
        Selector("<p>not json</p>").css_first("p").json()
    assert Selector('<p>{"z": 2}</p>').css_first("p").css("::text").get().json() == {"z": 2}


# --------------------------------------------------------- robustness/edge
def test_empty_and_malformed():
    for content in ("", "   \n", b"", None):
        s = Selector(content)
        assert s.tag == "html"
        assert s.css("p") == [] and s.css_first("p") is None
        assert s.get_all_text() == "" and s.to_markdown() == ""
        assert s.find_all("div") == [] and s.find_by_text("x") is None
    s = Selector("<div><p>one<p>two<b>bold</div><span>unclosed")
    assert [p.get_all_text() for p in s.css("p")] == ["one", "two\nbold"]
    assert s.css_first("span").text == "unclosed"
    assert Selector("just text, no tags").get_all_text() == "just text, no tags"
    assert Selector("<p>a\x00b\x01c</p>").get_all_text() == "abc"
    xs = Selector('<?xml version="1.0" encoding="UTF-8"?><html><body><p>é</p></body></html>')
    assert xs.css_first("p").text == "é"
    deep = Selector("<div>" * 1500 + "x" + "</div>" * 1500)
    assert "x" in deep.get_all_text()
    deep.to_markdown()  # must not raise RecursionError
    Selector("<div>" * 5000 + "x").to_markdown()  # libxml2 depth limit: truncated but no crash


def test_bytes_encoding_detection():
    text = '<html><head><meta charset="iso-8859-1"></head><body><p>café naïve</p></body></html>'
    s = Selector(text.encode("iso-8859-1"))
    assert s.css_first("p").text == "café naïve"
    assert s.encoding == "iso8859-1"
    u = Selector("<p>日本語</p>".encode("utf-8"))
    assert u.css_first("p").text == "日本語" and u.encoding == "utf-8"
    bom = Selector(b"\xef\xbb\xbf<p>bom</p>")
    assert bom.css_first("p").text == "bom"
    u16 = Selector("<p>sixteen</p>".encode("utf-16"))
    assert u16.css_first("p").text == "sixteen"
    xml_decl = Selector(b'<?xml version="1.0" encoding="windows-1252"?><html><body><p>\x93q\x94</p></body></html>')
    assert xml_decl.css_first("p").text == "“q”"
    bad = Selector(b"<p>bad \xff\xfe bytes</p>")  # undecodable as utf-8 -> cp1252 fallback, no crash
    assert "bad" in bad.get_all_text()
    hinted = Selector("<p>é</p>".encode("latin-1"), encoding="latin-1")
    assert hinted.css_first("p").text == "é"


def test_xml_mode_and_adaptor_alias():
    assert Adaptor is Selector
    x = Selector("<urlset><url><loc>http://a/</loc></url><url><loc>http://b/</loc></url></urlset>", xml=True)
    assert x.css("loc::text").getall() == ["http://a/", "http://b/"]
    assert x.xpath("//url/loc/text()").get() == "http://a/"


def test_repr_eq_hash(page):
    a = page.css_first("li")
    b = page.css_first("li")
    assert a == b and hash(a) == hash(b) and a != page.css("li")[1]
    assert "li" in repr(a) and str(a) == a.html_content
    assert bool(Selector(""))


def test_text_setter_for_subclasses():
    class R(Selector):
        def __init__(self, body):
            super().__init__(body)
            self.text = body

    r = R("<p>x</p>")
    assert r.text == "<p>x</p>" and r.css_first("p").text == "x"


def test_children_inherit_context(page):
    li = page.css_first("li")
    assert li.url == page.url and li.storage_path == page.storage_path
