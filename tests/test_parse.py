from harness import fixture

from raisa.parse import Page
from raisa.style import DIM, IMG, LINK, H, S


def texts(page):
    return [p.text() for p in page.paras]


def test_article_meta():
    pg = Page("en", "scp-173", fixture("en_scp173.html"))
    assert pg.title == "SCP-173"
    assert pg.rating == "+9876"
    assert pg.tags == ["euclid", "scp"]
    assert pg.images == [("https://scp-wiki.wdfiles.com/local--files/scp-173/SCP-173.jpg", "SCP-173.jpg", False)]
    assert [t for _, t in pg.links] == ["SCP-172", "SCP-174", "statues"]
    assert pg.links[0][0] == "https://scp-wiki.wikidot.com/scp-172"


def test_article_structure():
    pg = Page("en", "scp-173", fixture("en_scp173.html"))
    t = texts(pg)
    assert "Item #: SCP-173" in t
    assert any(x.endswith("statues[1].") for x in t)           # footnote
    assert "License text" not in t and "« SCP-172 | SCP-173 | SCP-174 »" not in t
    assert "- Hide log" not in t and "Hidden log text." in t     # unfolded block, no button
    assert "Name | Value" in t                                    # table
    assert "══ Tab One ══" in t and "══ Tab Two ══" in t          # tabs
    by_text = {p.text(): p for p in pg.paras}
    assert by_text["Nested item"].first == "  • "
    assert by_text["One"].first == "1. "
    quote = next(p for p in pg.paras if p.text().startswith("Quoted"))
    assert quote.first == "▏ "
    pre = next(p for p in pg.paras if p.kind == "pre" and "line one" in p.text())
    assert pre.text() == "line one\n    indented line two"
    assert any(p.kind == "hr" for p in pg.paras)
    img = next(p for p in pg.paras if p.kind == "img")
    assert img.runs[0][1] & IMG and img.img == 0
    keter = [r for p in pg.paras for r in p.runs if r[0] == "Keter"]
    assert keter and keter[0][1] & S                             # strike-through via style
    folded = [r for p in pg.paras for r in p.runs if r[0] == "+ Show log"]
    assert folded and folded[0][1] & DIM
    links = [r for p in pg.paras for r in p.runs if r[2] is not None]
    assert all(r[1] & LINK for r in links)


def test_article_strips_raw_escape_sequences():
    html = ('<div id="page-content"><p>Raw \x1b]52;c;SGVsbG8=\x07 and \x1b[2J here'
            '</p></div>' + " " * 200)
    pg = Page("en", "x", html)
    joined = "".join(texts(pg)) + pg.title
    assert "\x1b" not in joined and "\x07" not in joined


def test_unclosed_tags_and_broken_html():
    pg = Page("en", "x", '<div id="page-content"><h2>Head</h2><p>Body <b>bold<i>both</p></div>')
    assert texts(pg)[0] == "Head"
    assert pg.paras[0].runs[0][1] & H
    assert "Body boldboth" in texts(pg)
    # badly broken HTML must not break parsing; a malformed link is plain text
    Page("en", "x", "<div id=page-content><ul><li><a href='/a'>x</ul></div></b></i><p")
    bad = Page("en", "x", '<div id="page-content"><a href="http://[x">bad</a> <img src="http://[y"> text</div>')
    assert texts(bad)[0].split() == ["bad", "text"] and not bad.links and not bad.images


def test_main_page_starts_with_the_site_menu():
    """The main page: the side bar's headings and links to this branch on top, then the page."""
    pg = Page("en", "", fixture("en_main.html"))
    t = [x for x in texts(pg) if x]
    assert t[:3] == ["About the SCP Foundation", "SCP by Series: Series I · Series II · Joke SCPs",
                     "Tales: Tales Hub · Guide Hub"]          # a foreign site is skipped, scpwiki.com is ours
    assert t[3].startswith("Welcome") and pg.links[1][1] == "Series I"
    assert all("+ More" not in x and "default.png" not in x for x in t[:3])
    assert Page("en", "scp-173", fixture("en_scp173.html")).paras[0].text() != "Series I"   # only the main page


def test_acs_bar_stays_on_one_line():
    """The anomaly classification bar: its blocks are divs, but "category: value" is one line."""
    pair = '<div class="{}"><div class="class-category">{}:</div>{}<div class="class-text">{}</div></div>'
    bar = ('<div class="anom-bar"><div class="top-left-box"><span class="item">Item#:</span> '
           '<span class="number">039</span></div>' + pair.format("contain-class", "Containment Class", "\n", "euclid")
           + pair.format("risk-class", "Risk Class", "", "caution") + "</div>")
    pg = Page("en", "x", f'<div id="page-content">{bar}<p>Text.</p></div>')
    assert [" ".join(x.split()) for x in texts(pg)] == ["Item#: 039", "Containment Class: euclid",
                                                        "Risk Class: caution", "", "Text."]


def test_avatar_is_not_an_image():
    html = ('<div id="page-content"><p>by <span class="printuser avatarhover"><a href="https://www.wikidot.com/user:info/x">'
            '<img class="small" src="https://www.wikidot.com/avatar.php?userid=1" alt="X"/></a>'
            '<a href="https://www.wikidot.com/user:info/x">Author</a></span> and more</p>'
            '<img src="pic.png" srcset="a.png 1x"/><img srcset="b.png 1x, b2.png 2x"/></div>')
    pg = Page("en", "x", html)
    assert "by Author and more" in texts(pg)
    assert [u.rsplit("/", 1)[1] for u, *_ in pg.images] == ["pic.png", "b.png"]


def test_hidden_blocks():
    """display:none hides template leftovers, but not a collapsible block's content or tabs."""
    html = ('<div id="page-content"><p>Visible</p><div style="display: none;"><p>{$comments2}</p></div>'
            '<div class="collapsible-block"><div class="collapsible-block-folded"><a>+ show</a></div>'
            '<div class="collapsible-block-unfolded" style="display:none"><p>Secret log</p></div></div>'
            '<div class="yui-navset"><ul class="yui-nav"><li>A</li><li>B</li></ul><div class="yui-content">'
            '<div><p>Tab one</p></div><div style="display:none"><p>Tab two</p></div></div></div></div>')
    text = " ".join(p.text() for p in Page("en", "x", html).paras)
    assert "{$comments2}" not in text
    assert "Secret log" in text and "Tab one" in text and "Tab two" in text


def test_object_class_colour():
    from raisa.style import EUCLID, KETER, SAFE, THAUMIEL

    def page(body, tags=""):
        links = "".join(f'<a href="/t/{t}">{t}</a>' for t in tags.split())
        tags = f'<div class="page-tags"><span>{links}</span></div>'
        return Page("en", "x", f'<div id="page-content">{body}</div>{tags}')
    assert page("<p>text</p>", "scp euclid").class_bg == EUCLID                          # from the tags
    assert page("<p><b>Object Class:</b> Thaumiel</p>").class_bg == THAUMIEL             # from the class line
    assert page("<p><strong>Класс объекта:</strong> Кетер</p>").class_bg == KETER
    assert page("<p>Containment Class: safe</p>").class_bg == SAFE                       # ACS bar
    assert page("<p>Description: Safe to touch.</p><p>Keter</p>").class_bg == 0         # not a class line
    assert page("<p>A hub.</p>", "hub").class_bg == 0
    # as other branches write it (found on the real sites)
    assert page("<p>x</p>", "scp sûr").class_bg == SAFE and page("<p>x</p>", "euclídeo").class_bg == EUCLID
    assert page("<p><b>项目等级：</b>Keter</p>").class_bg == KETER                          # full-width colon
    assert page("<p><b>ระดับ:</b> Keter</p>").class_bg == KETER                           # th, no tags
    assert page("<p><b>등급:</b> 케테르</p>").class_bg == KETER
    assert page("<p><b>Clasificación del Objeto:</b> Taumiel</p>").class_bg == THAUMIEL


def test_hidden_branch_chooser_is_skipped():
    """zh-tr puts a branch chooser and a title override (hidden by CSS) before the article."""
    pg = Page("zh", "x", '<div id="page-content">'
                         '<div class="multibranch-wrapper content-panel"><p>您正在查詢的標題為：關注組織</p></div>'
                         '<div class="overwritting"><div class="pseudocrumbs">[[[|]]] » null</div></div>'
                         '<p><strong>項目編號：</strong>SCP-173</p></div>')
    assert texts(pg) == ["項目編號：SCP-173"]


def test_framed_block_gets_lines():
    """A block with a border in its style (a notice, a log) gets a line above and below."""
    pg = Page("ru", "x", '<div id="page-content"><p>before</p>'
                         '<div style="border: solid 2px #a52a2a; padding: 5px"><p>inside</p></div><p>after</p>'
                         '<div style="border: none"><p>no frame</p></div><div style="border:unset">x</div></div>')
    assert [p.kind if p.kind != "text" else p.text() for p in pg.paras] == \
        ["before", "blank", "hr", "blank", "inside", "blank", "hr", "blank", "after", "blank", "no frame", "blank", "x"]


def test_frame_from_page_css_and_ru_widgets():
    """ru: a card framed by the page's own CSS; the rating widget gives the rating, not text;
    tooltips shown only on hover are not text."""
    pg = Page("ru", "x", '<div id="page-content"><style>.card{border:1px solid #ccc}.tip{border:0}</style>'
                         '<div class="w-stars-rate-module"><div class="w-stars-rate-rating">рейтинг:&nbsp;'
                         '<span class="w-stars-rate-number">4.3</span></div><div class="w-stars-rate-votes">'
                         '<span class="w-stars-rate-number">65</span>/92%</div></div>'
                         '<div class="card"><div>CONFIDENTIAL</div><span class="apcs-tooltip">Метаморфоза</span>'
                         '<div class="tip">tip</div></div><p>text</p></div>')
    assert pg.rating == "4.3"
    assert [p.kind if p.kind != "text" else p.text() for p in pg.paras] == \
        ["hr", "CONFIDENTIAL", "tip", "hr", "blank", "text"]


def test_table_columns_line_up():
    pg = Page("ru", "x", '<div id="page-content"><table><tr><td></td><th>За</th><th>Против</th></tr>'
                         '<tr><td>O5-1</td><td>✔</td><td></td></tr><tr><td>O5-10</td><td></td>'
                         '<td><b>да</b> нет</td></tr></table></div>')
    assert [p.text() for p in pg.paras if p.kind == "pre"] == [
        "      | За | Против", "O5-1  | ✔  |       ", "O5-10 |    | да нет"]
    wide = Page("en", "x", '<div id="page-content"><table><tr><td>' + "x" * 50 + '</td><td>' + "y" * 40
                           + '</td></tr></table></div>')
    assert wide.paras[0].kind == "text"                     # wider than the text: not lined up
