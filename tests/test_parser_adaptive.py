"""Adaptive element tracking tests (offline)."""
from __future__ import annotations

import pytest

from shadowscrape.parser import Selector
from shadowscrape.parser.storage import SQLiteStorage

URL = "https://shop.example.com/products"

OLD = """
<html><body>
<div id="wrapper" class="page">
  <div class="product-list">
    <div class="product-card" id="p1"><h2 class="title">Widget Pro</h2><span class="price">$10.00</span>
      <a class="buy" href="/buy/1">Buy now</a></div>
    <div class="product-card" id="p2"><h2 class="title">Gadget Max</h2><span class="price">$20.00</span>
      <a class="buy" href="/buy/2">Buy now</a></div>
    <div class="product-card" id="p3"><h2 class="title">Gizmo Mini</h2><span class="price">$30.00</span>
      <a class="buy" href="/buy/3">Buy now</a></div>
  </div>
  <div class="sidebar"><h2 class="title">Categories</h2><span class="price-note">Prices in USD</span></div>
</div></body></html>
"""

# redesign: different tags, classes, ids and nesting; text and some structure survive
NEW = """
<html><body>
<main class="layout-v2"><section class="catalog"><div class="grid">
  <article class="item" data-sku="p1"><div class="meta"><h3 class="item-title">Widget Pro</h3></div>
     <div class="cost">$10.00</div><a class="cta" href="/buy/1">Buy now</a></article>
  <article class="item" data-sku="p2"><div class="meta"><h3 class="item-title">Gadget Max</h3></div>
     <div class="cost">$20.00</div><a class="cta" href="/buy/2">Buy now</a></article>
  <article class="item" data-sku="p3"><div class="meta"><h3 class="item-title">Gizmo Mini</h3></div>
     <div class="cost">$30.00</div><a class="cta" href="/buy/3">Buy now</a></article>
</div></section></main>
<aside class="side"><h3 class="aside-title">Categories</h3><p>Prices in USD</p></aside>
</body></html>
"""


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "adaptive.sqlite")


def make(html, db, **kw):
    return Selector(html, url=URL, storage_path=db, adaptive=True, **kw)


def test_auto_save_then_relocate_single(db):
    old = make(OLD, db)
    res = old.css("#p2 .price", identifier="gadget_price", auto_save=True)
    assert res.first.text == "$20.00"

    new = make(NEW, db)
    assert new.css("#p2 .price") == []  # selector is broken after redesign
    relocated = new.css("#p2 .price", identifier="gadget_price", adaptive=True)
    assert len(relocated) == 1
    assert relocated[0].tag == "div" and relocated[0].text == "$20.00"
    assert relocated[0].has_class("cost")


def test_persisted_across_instances_and_default_identifier(db):
    make(OLD, db).css("h2.title", auto_save=True)  # identifier defaults to selector
    new = Selector(NEW, url=URL, storage_path=db)  # adaptive flag not set on the instance
    got = new.css("h2.title", adaptive=True)
    assert [g.text for g in got][0] == "Widget Pro"
    assert got[0].tag == "h3"


def test_relocate_many_elements_distinct(db):
    make(OLD, db).css(".product-card .price", identifier="prices", auto_save=True)
    got = make(NEW, db).css(".product-card .price", identifier="prices", adaptive=True)
    assert [g.text for g in got] == ["$10.00", "$20.00", "$30.00"]
    assert len({id(g._root) for g in got}) == 3


def test_relocate_card_container(db):
    make(OLD, db).css("#p3", identifier="card3", auto_save=True)
    got = make(NEW, db).css("#p3", identifier="card3", adaptive=True)
    assert len(got) == 1 and got[0].tag == "article"
    assert "Gizmo Mini" in got[0].get_all_text()


def test_xpath_adaptive(db):
    make(OLD, db).xpath("//div[@id='p1']//a", identifier="buy1", auto_save=True)
    got = make(NEW, db).xpath("//div[@id='p1']//a", identifier="buy1", adaptive=True)
    assert len(got) == 1 and got[0].attrib["href"] == "/buy/1"


def test_no_relocation_when_selector_still_matches(db):
    s = make(OLD, db)
    s.css(".price", identifier="x", auto_save=True)
    again = make(OLD, db).css(".price", identifier="x", adaptive=True)
    assert len(again) == 3  # normal match, relocation not used


def test_unknown_identifier_and_threshold(db):
    new = make(NEW, db)
    assert new.css("#nothing", identifier="never_saved", adaptive=True) == []
    make(OLD, db).css("#p2 .price", identifier="gp", auto_save=True)
    unrelated = Selector("<html><body><footer><small>zzz</small></footer></body></html>", url=URL, storage_path=db)
    assert unrelated.css("#p2 .price", identifier="gp", adaptive=True) == []
    # explicit relocate with a very high threshold fails, default succeeds
    assert make(NEW, db).relocate("gp", threshold=99) == []
    assert len(make(NEW, db).relocate("gp")) == 1


def test_manual_save_relocate_forget(db):
    old = make(OLD, db)
    el = old.css_first("#p1 .title")
    old.save(el, "t1")
    assert old.has_saved("t1")
    got = make(NEW, db).relocate("t1")
    assert got[0].text == "Widget Pro"
    # relocate directly from a live element of the old page
    assert make(NEW, db).relocate(el)[0].text == "Widget Pro"
    old.forget("t1")
    assert not old.has_saved("t1") and make(NEW, db).relocate("t1") == []


def test_domains_are_isolated(db):
    a = Selector(OLD, url="https://a.com/x", storage_path=db)
    a.css("#p1 .price", identifier="pr", auto_save=True)
    b = Selector(NEW, url="https://b.com/x", storage_path=db)
    assert b.css("#p1 .price", identifier="pr", adaptive=True) == []


def test_ids_changed_but_sibling_text_helps(db):
    old = Selector('<div><ul><li id="a">Alpha</li><li id="b">Beta</li><li id="c">Gamma</li></ul></div>',
                   url=URL, storage_path=db)
    old.css("#b", identifier="beta", auto_save=True)
    new = Selector('<div><ol class="n"><li class="x">Alpha</li><li class="y">Beta</li><li class="z">Gamma</li></ol></div>',
                   url=URL, storage_path=db)
    got = new.css("#b", identifier="beta", adaptive=True)
    assert len(got) == 1 and got[0].text == "Beta"


def test_storage_unit(tmp_path):
    st = SQLiteStorage(str(tmp_path / "s.db"))
    assert st.load("d", "i") is None
    st.save("d", "i", [{"tag": "p"}])
    st.save("d", "i", [{"tag": "div"}])
    assert st.load("d", "i") == [{"tag": "div"}]
    assert st.identifiers("d") == ["i"]
    st.delete("d", "i")
    assert st.load("d", "i") is None
    mem = SQLiteStorage(":memory:")
    mem.save("d", "i", [1])
    assert mem.load("d", "i") == [1]


def test_adaptive_on_empty_doc(db):
    assert Selector("", url=URL, storage_path=db).css("p", adaptive=True, identifier="q") == []
    assert Selector("", url=URL, storage_path=db).relocate("whatever") == []
