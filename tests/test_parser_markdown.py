"""Markdown conversion tests (offline)."""
from __future__ import annotations

from shadowscrape.parser import Selector

ARTICLE = """
<html><head><title>T</title><style>p{}</style></head><body>
<header class="site-header"><nav><a href="/">Home</a><a href="/about">About</a></nav></header>
<div class="layout">
<aside class="sidebar"><h3>Related</h3><ul><li><a href="/x">Other post</a></li></ul></aside>
<article>
  <h1>Main Title</h1>
  <p>First paragraph with <strong>bold</strong>, <em>italic</em> and <code>code()</code>, plus a
     <a href="/rel/link">relative link</a> and <a href="https://x.org/a b">abs</a>.</p>
  <h2>Section</h2>
  <p>Line one<br>Line two</p>
  <ul><li>Alpha</li><li>Beta<ul><li>Nested</li></ul></li></ul>
  <ol start="3"><li>three</li><li>four</li></ol>
  <blockquote><p>Quoted text</p></blockquote>
  <pre><code class="language-python">def f():
    return 1</code></pre>
  <table><thead><tr><th>Name</th><th>Qty</th></tr></thead>
    <tbody><tr><td>Apple</td><td>3</td></tr><tr><td>Pipe|Cell</td><td></td></tr></tbody></table>
  <img src="/i.png" alt="pic">
  <script>alert(1)</script>
  <hr>
  <p>Last paragraph, with enough text to count as content for the heuristic scorer.</p>
</article></div>
<footer id="footer"><p>Copyright nobody</p></footer>
</body></html>
"""


def md(html=ARTICLE, **kw):
    url = kw.pop("url", "https://example.com/blog/post")
    return Selector(html, url=url).to_markdown(**kw)


def test_full_conversion():
    out = md()
    assert "# Main Title" in out and "## Section" in out
    assert "**bold**" in out and "*italic*" in out and "`code()`" in out
    assert "[relative link](https://example.com/rel/link)" in out
    assert "[abs](https://x.org/a%20b)" in out
    assert "Line one  \nLine two" in out
    assert "- Alpha\n- Beta\n  - Nested" in out
    assert "3. three\n4. four" in out
    assert "> Quoted text" in out
    assert "```python\ndef f():\n    return 1\n```" in out
    assert "| Name | Qty |\n| --- | --- |\n| Apple | 3 |\n| Pipe\\|Cell |  |" in out
    assert "---" in out
    assert "alert" not in out and "p{}" not in out
    assert "![" not in out  # images off by default
    assert "\n\n\n" not in out and out == out.strip()


def test_options():
    assert "![pic](https://example.com/i.png)" in md(include_images=True)
    no_links = md(include_links=False)
    assert "relative link" in no_links and "](http" not in no_links


def test_main_content_only():
    out = md(main_content_only=True)
    assert "# Main Title" in out and "Last paragraph" in out
    for noise in ("Home", "About", "Related", "Other post", "Copyright"):
        assert noise not in out


def test_main_content_heuristic_without_semantic_tags():
    long = " ".join(["This is a long sentence, full of commas, words and substance."] * 4)
    html = f"""<html><body>
    <div class="menu"><a href="/1">One</a> <a href="/2">Two</a> <a href="/3">Three</a></div>
    <div id="wrap"><div class="post-body"><p>{long}</p><p>{long}</p></div></div>
    <div class="footer-links"><a href="/t">Terms</a></div></body></html>"""
    out = md(html, main_content_only=True)
    assert "long sentence" in out and "One" not in out and "Terms" not in out
    # semantic <main> wins
    html2 = f"<body><div>{long}</div><main><p>{long} MAIN</p></main></body>"
    assert md(html2, main_content_only=True).count("MAIN") == 1
    assert "long sentence" in md(html2, main_content_only=True)


def test_main_content_fallback_to_body():
    out = md("<html><body><p>tiny</p></body></html>", main_content_only=True)
    assert out == "tiny"


def test_element_level_and_headings():
    s = Selector("<div><h3>Sub <i>x</i></h3><p>para</p></div>")
    assert s.css_first("h3").to_markdown() == "### Sub *x*"
    assert s.css_first("div").to_markdown() == "### Sub *x*\n\npara"
    assert s.css_first("p").to_markdown() == "para"
    assert Selector("<p>a <span>b</span> c</p>").css_first("span").to_markdown() == "b"


def test_edge_cases():
    assert md("") == ""
    assert md("<html><body></body></html>") == ""
    assert md("plain text only") == "plain text only"
    assert md("<p>a</p><p></p><p>&nbsp;</p><p>b</p>") == "a\n\nb"
    assert md("<p>  lots   of \n whitespace </p>") == "lots of whitespace"
    assert md("<p>x <b> </b> y</p>") == "x y"
    assert md("<p><b> bold </b>tail</p>") == "**bold** tail"
    assert md('<p><a href="#top">anchor</a> <a href="javascript:void(0)">js</a> <a>none</a></p>') == "anchor js none"
    assert md('<p><a href="/a"></a>text</p>') == "text"
    assert md("<p>hidden</p><p hidden>secret</p><div style='display:none'>x</div>") == "hidden"
    assert md("<p>`tick` <code>a`b</code></p>") == "`tick` ``a`b``"
    assert md("<pre>```\ncode\n```</pre>").startswith("````\n```")
    assert md("<ul><li></li><li>only</li></ul>") == "- only"
    assert md("<ul><li><p>para1</p><p>para2</p></li></ul>") == "- para1\n\n  para2"
    assert md("<ul><li>a</li><ul><li>direct nested</li></ul></ul>") == "- a\n  - direct nested"
    assert md("<h2></h2><p>x</p>") == "x"
    assert md("<a href='/u'><div>block in link</div></a>") == "block in link"


def test_tables():
    t = md("<table><caption>Cap</caption><tr><td>a</td><td>b</td></tr><tr><td colspan=2>wide</td></tr></table>")
    assert t == "**Cap**\n\n| a | b |\n| --- | --- |\n| wide |  |"
    layout = md("<table><tr><td><p>one</p></td></tr><tr><td><p>two</p></td></tr></table>")
    assert layout == "one\n\ntwo"
    ragged = md("<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>1</td></tr></table>")
    assert ragged.splitlines()[-1] == "| 1 |  |  |"
    nested = md("<table><tr><td>x<table><tr><td>in1</td><td>in2</td></tr></table></td><td>y</td></tr></table>")
    assert "| x" in nested and "y" in nested


def test_definition_list_and_misc_blocks():
    out = md("<dl><dt>Term</dt><dd>Definition</dd></dl><figure><img src=a.png alt=A><figcaption>Cap</figcaption></figure>")
    assert out == "**Term**\n\nDefinition\n\nCap"
    assert md("<div>one<div>two</div>three</div>") == "one\n\ntwo\n\nthree"
    assert md("<p>a<br><br>b</p>") == "a\n\nb"


def test_selectors_to_markdown():
    s = Selector("<p>a</p><p>b</p>")
    assert s.css("p").to_markdown() == "a\n\nb"
